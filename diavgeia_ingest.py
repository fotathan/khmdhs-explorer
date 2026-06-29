"""
diavgeia_ingest.py — ingestion for the Diavgeia (diavgeia.gov.gr) data source.

Parallel to khmdhs_ingest.py, but for the Diavgeia opendata API. Diavgeia
decisions are keyed by ADA (not ADAM), so they live in their own ADA-keyed tables
(proc.diavgeia_decision + children, see diavgeia_migration.sql) and REUSE the
shared dimensions (authority, economic_operator, cpv_code) to avoid duplicates.

Scope (for now): three decision types harvested from /luminapi/opendata/search —
   notice   -> Δ.2.1  ΠΕΡΙΛΗΨΗ ΔΙΑΚΗΡΥΞΗΣ / ΔΙΑΚΗΡΥΞΗ (ΑΠΟ 1.10.2025)
   award    -> Δ.2.2  ΚΑΤΑΚΥΡΩΣΗ
   contract -> Γ.3.4  ΣΥΜΒΑΣΗ

Key API facts (verified live, 2026-06):
 * search GET /search?type=<uid>&status=published&sort=recent&page=&size=
   returns {decisions:[...], info:{total, actualSize, page, size}}; size up to 500
   honored and there is NO deep-paging cap, so a window paginates fully.
 * from_date/to_date filter submissionTimestamp, but the API ALSO silently applies
   issueDate >= now-180d unless you pass from_issue_date / to_issue_date. We window
   on issueDate (the meaningful decision date) via those params.
 * dictionary lookups: /organizations/<uid>.json (carries vatNumber -> our dedup
   key into authority), /units/<uid>.json, /signers/<uid>.json.

The DB layer is the same thin object khmdhs_ingest uses (execute /
execute_returning / commit / rollback / query / cur), so nothing in db.py's DB
surface changes.
"""

from __future__ import annotations
import os
import time
import datetime as dt
from dataclasses import dataclass, field
from typing import Iterator

import requests

# Reuse the building blocks from the KHMDHS ingester rather than duplicate them.
from khmdhs_ingest import (
    RateLimiter, _as_jsonb, windows, Repository,
    MAX_RETRIES, RETRY_STATUSES,
)

BASE = "https://diavgeia.gov.gr/luminapi/opendata"

# Friendly name <-> Diavgeia decision-type uid.
NAME_TO_UID = {"notice": "Δ.2.1", "award": "Δ.2.2", "contract": "Γ.3.4"}
UID_TO_NAME = {v: k for k, v in NAME_TO_UID.items()}
TYPE_NAMES = list(NAME_TO_UID)               # canonical order

PAGE_SIZE = 500                              # max honored by the API
# Diavgeia's opendata rate limit isn't published; stay deliberately polite.
RATE_LIMIT_PER_MIN = int(os.environ.get("DIAVGEIA_RATE_PER_MIN", "120"))
# Window on issueDate. No deep-paging cap means this is purely a resumability
# knob (smaller windows = finer-grained resume / progress).
WINDOW_DAYS = int(os.environ.get("DIAVGEIA_WINDOW_DAYS", "30"))

BACKOFF_BASE = 5.0
BACKOFF_CAP = 120.0
PROGRESS_COMMIT_SECONDS = float(os.environ.get("INGEST_PROGRESS_COMMIT_SECONDS", "3"))


# --------------------------------------------------------------------------- #
# small coercion helpers
# --------------------------------------------------------------------------- #
def _epoch_ms_to_dt(ms) -> dt.datetime | None:
    if ms in (None, ""):
        return None
    try:
        return dt.datetime.fromtimestamp(int(ms) / 1000, tz=dt.timezone.utc)
    except (TypeError, ValueError, OSError):
        return None


def _epoch_ms_to_date(ms) -> dt.date | None:
    d = _epoch_ms_to_dt(ms)
    return d.date() if d else None


def _num(v):
    if v in (None, ""):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _int(v):
    n = _num(v)
    return int(n) if n is not None else None


def _str(v):
    if v is None:
        return None
    s = str(v).strip()
    return s or None


# --------------------------------------------------------------------------- #
# API client
# --------------------------------------------------------------------------- #
@dataclass
class DiavgeiaClient:
    session: requests.Session = field(default_factory=requests.Session)
    limiter: RateLimiter = field(
        default_factory=lambda: RateLimiter(per_minute=RATE_LIMIT_PER_MIN))

    def __post_init__(self):
        # in-process org-lookup cache (uid -> resolved object | None), used by
        # the resolve pass so a repeated organizationId isn't re-fetched.
        self._org_cache: dict[str, dict | None] = {}

    def _get(self, path: str, params: dict | None) -> dict | None:
        """GET one JSON resource, retrying transient failures with backoff.
        Returns the parsed body, or None on a 404 (missing dictionary entry)."""
        url = f"{BASE}{path}"
        for attempt in range(MAX_RETRIES):
            self.limiter.wait()
            try:
                r = self.session.get(
                    url, params=params,
                    headers={"Accept": "application/json"}, timeout=60)
            except (requests.ConnectionError, requests.Timeout) as e:
                if attempt == MAX_RETRIES - 1:
                    raise
                self._sleep_backoff(attempt, None)
                continue
            if r.status_code == 404:
                return None
            if r.status_code in RETRY_STATUSES:
                if attempt == MAX_RETRIES - 1:
                    r.raise_for_status()
                self._sleep_backoff(attempt, r.headers.get("Retry-After"))
                continue
            r.raise_for_status()
            return r.json()
        raise RuntimeError(f"giving up on {url} after {MAX_RETRIES} tries")

    @staticmethod
    def _sleep_backoff(attempt: int, retry_after: str | None) -> None:
        delay = None
        if retry_after:
            try:
                delay = float(retry_after)
            except (TypeError, ValueError):
                delay = None
        if delay is None:
            delay = BACKOFF_BASE * (2 ** attempt)
        time.sleep(min(delay, BACKOFF_CAP))

    def search(self, decision_type: str,
               from_issue: dt.date, to_issue: dt.date) -> Iterator[dict]:
        """Yield every decision in [from_issue, to_issue] (by issueDate) for one
        decision type, paginating size=500 pages until the result set is drained."""
        page = 0
        while True:
            data = self._get("/search", {
                "type": decision_type,
                "status": "published",
                "sort": "recent",
                "from_issue_date": from_issue.isoformat(),
                "to_issue_date": to_issue.isoformat(),
                "page": page,
                "size": PAGE_SIZE,
            }) or {}
            decisions = data.get("decisions") or []
            for d in decisions:
                yield d
            info = data.get("info") or {}
            total = info.get("total") or 0
            if len(decisions) < PAGE_SIZE or (page + 1) * PAGE_SIZE >= total:
                break
            page += 1

    # ---- dictionary lookups (cached) ----------------------------------- #
    def lookup_org(self, uid: str) -> dict | None:
        if uid in self._org_cache:
            return self._org_cache[uid]
        org = self._get(f"/organizations/{uid}.json", None)
        self._org_cache[uid] = org
        return org

    def lookup_unit(self, uid: str) -> dict | None:
        return self._get(f"/units/{uid}.json", None)

    def lookup_signer(self, uid: str) -> dict | None:
        return self._get(f"/signers/{uid}.json", None)


# --------------------------------------------------------------------------- #
# Persistence — ADA-keyed, idempotent (upsert on the natural key).
# --------------------------------------------------------------------------- #
class DiavgeiaRepository:
    # Columns written by upsert_decision, in order (also drives the UPDATE set).
    # NOTE: authority_id is intentionally NOT here — it's set by the separate
    # resolve pass (resolve_authorities) so a re-run of the backfill never
    # clobbers a resolved value back to NULL.
    _COLS = [
        "ada", "subject", "decision_type", "organization_uid",
        "signer_uid", "issue_date", "protocol_number", "status", "version_id",
        "corrected_version_id", "private_data", "publish_timestamp",
        "submission_timestamp", "document_url", "api_url", "document_checksum",
        "document_type", "amount", "currency_code", "contest_progress_type",
        "selection_criterion", "manifest_contract_type", "org_budget_code",
        "text_related_ada", "contract_type", "number_of_people",
        "financed_project", "duration", "raw_json",
    ]

    def __init__(self, db, client: DiavgeiaClient):
        self.db = db
        self.client = client
        # reuse the KHMDHS operator upsert (VAT-keyed, idempotent) verbatim
        self._ops = Repository(db)

    # ---- authority resolution (dedupe into the shared table) ------------ #
    def resolve_authority(self, org_uid: str | None) -> str | None:
        """Map a Diavgeia organizationId to a proc.authority.org_id, reusing an
        existing authority matched by ΑΦΜ (vat_number) where possible so we don't
        create duplicates. Falls back to a synthetic 'DIAV:<uid>' authority for
        organizations we don't already hold from KHMDHS."""
        if not org_uid:
            return None
        # already linked from a previous decision/run? (cheap, avoids the API)
        rows = self.db.query(
            "SELECT org_id FROM proc.authority WHERE diavgeia_org_uid=%s LIMIT 1",
            (org_uid,))
        if rows:
            return rows[0][0]

        org = self.client.lookup_org(org_uid)
        if not org:
            return None
        name = _str(org.get("label")) or f"(diavgeia org {org_uid})"
        vat = _str(org.get("vatNumber"))

        # match an existing authority by ΑΦΜ → link it to this Diavgeia org.
        if vat:
            rows = self.db.query(
                "SELECT org_id FROM proc.authority WHERE vat_number=%s "
                "ORDER BY (source='khmdhs') DESC LIMIT 1", (vat,))
            if rows:
                org_id = rows[0][0]
                self.db.execute(
                    """UPDATE proc.authority
                       SET diavgeia_org_uid=%s,
                           source=CASE WHEN source='diavgeia' THEN 'diavgeia'
                                       ELSE 'merged' END,
                           last_seen=now()
                       WHERE org_id=%s""", (org_uid, org_id))
                return org_id

        # no match → create a Diavgeia-sourced authority under a synthetic key.
        org_id = f"DIAV:{org_uid}"
        self.db.execute(
            """INSERT INTO proc.authority
                 (org_id, name, vat_number, diavgeia_org_uid, source, last_seen)
               VALUES (%s,%s,%s,%s,'diavgeia', now())
               ON CONFLICT (org_id) DO UPDATE
                 SET name=EXCLUDED.name,
                     vat_number=COALESCE(EXCLUDED.vat_number, proc.authority.vat_number),
                     diavgeia_org_uid=EXCLUDED.diavgeia_org_uid,
                     last_seen=now()""",
            (org_id, name, vat, org_uid))
        return org_id

    # ---- core decision -------------------------------------------------- #
    def upsert_decision(self, d: dict) -> None:
        ada = d.get("ada")
        if not ada:
            return
        ev = d.get("extraFieldValues") or {}
        signer_ids = d.get("signerIds") or []
        money = ev.get("estimatedAmount") or ev.get("awardAmount") \
            or ev.get("contractAmount") or {}

        values = {
            "ada": ada,
            "subject": _str(d.get("subject")),
            "decision_type": _str(d.get("decisionTypeId")),
            "organization_uid": _str(d.get("organizationId")),
            "signer_uid": _str(signer_ids[0]) if signer_ids else None,
            "issue_date": _epoch_ms_to_date(d.get("issueDate")),
            "protocol_number": _str(d.get("protocolNumber")),
            "status": _str(d.get("status")),
            "version_id": _str(d.get("versionId")),
            "corrected_version_id": _str(d.get("correctedVersionId")),
            "private_data": d.get("privateData") if isinstance(d.get("privateData"), bool) else None,
            "publish_timestamp": _epoch_ms_to_dt(d.get("publishTimestamp")),
            "submission_timestamp": _epoch_ms_to_dt(d.get("submissionTimestamp")),
            "document_url": _str(d.get("documentUrl")),
            "api_url": _str(d.get("url")),
            "document_checksum": _str(d.get("documentChecksum")),
            "document_type": _str(ev.get("documentType")),
            "amount": _num(money.get("amount")),
            "currency_code": _str(money.get("currency")),
            "contest_progress_type": _str(ev.get("contestProgressType")),
            "selection_criterion": _str(ev.get("manifestSelectionCriterion")),
            "manifest_contract_type": _str(ev.get("manifestContractType")),
            "org_budget_code": _str(ev.get("orgBudgetCode")),
            "text_related_ada": _str(ev.get("textRelatedADA")),
            "contract_type": _str(ev.get("contractType")),
            "number_of_people": _int(ev.get("numberOfPeople")),
            "financed_project": ev.get("financedProject") if isinstance(ev.get("financedProject"), bool) else None,
            "duration": _str(ev.get("duration")),
            "raw_json": _as_jsonb(d),
        }
        cols = self._COLS
        placeholders = ",".join(["%s"] * len(cols))
        update_set = ",".join(
            f"{c}=EXCLUDED.{c}" for c in cols if c != "ada") + ", ingested_at=now()"
        self.db.execute(
            f"INSERT INTO proc.diavgeia_decision ({','.join(cols)}) "
            f"VALUES ({placeholders}) "
            f"ON CONFLICT (ada) DO UPDATE SET {update_set}",
            tuple(values[c] for c in cols))

        # children (delete + reinsert each run → idempotent)
        self._replace_cpv(ada, ev.get("cpv"))
        self._replace_persons(ada, ev.get("person"))
        self._replace_signers(ada, signer_ids)
        self._replace_units(ada, d.get("unitIds") or [])
        self._replace_thematic(ada, d.get("thematicCategoryIds") or [])
        self._replace_attachments(ada, d.get("attachments") or [])
        self._record_related(ada, ev)

    # ---- children ------------------------------------------------------- #
    def _replace_cpv(self, ada: str, codes) -> None:
        self.db.execute("DELETE FROM proc.diavgeia_decision_cpv WHERE ada=%s", (ada,))
        seen = set()
        ord_ = 0
        for code in (codes or []):
            code = _str(code)
            if not code or len(code) > 10 or code in seen:
                continue
            seen.add(code)
            # reuse the shared CPV dimension; create a stub row if unseen.
            self.db.execute(
                "INSERT INTO proc.cpv_code (cpv_code) VALUES (%s) "
                "ON CONFLICT (cpv_code) DO NOTHING", (code,))
            self.db.execute(
                "INSERT INTO proc.diavgeia_decision_cpv (ada, cpv_code, ord) "
                "VALUES (%s,%s,%s) ON CONFLICT DO NOTHING", (ada, code, ord_))
            ord_ += 1

    def _replace_persons(self, ada: str, persons) -> None:
        self.db.execute("DELETE FROM proc.diavgeia_decision_person WHERE ada=%s", (ada,))
        for i, p in enumerate(persons or []):
            if not isinstance(p, dict):
                continue
            afm = _str(p.get("afm"))
            name = _str(p.get("name"))
            if not (afm or name):
                continue
            # only route persons WITH an ΑΦΜ through economic_operator (VAT-keyed,
            # deduped). Name-only persons keep their name on the link row.
            operator_id = self._ops._upsert_operator(
                afm, name, country=p.get("afmCountry")) if afm else None
            self.db.execute(
                """INSERT INTO proc.diavgeia_decision_person
                     (ada, operator_id, afm, name, afm_type, afm_country, ord)
                   VALUES (%s,%s,%s,%s,%s,%s,%s)""",
                (ada, operator_id, afm, name,
                 _str(p.get("afmType")), _str(p.get("afmCountry")), i))

    def _replace_signers(self, ada: str, signer_ids) -> None:
        # UIDs only — label resolution is deferred to resolve_dictionaries() so
        # the ingest hot path makes no per-decision API calls.
        self.db.execute("DELETE FROM proc.diavgeia_decision_signer WHERE ada=%s", (ada,))
        for i, sid in enumerate(signer_ids or []):
            sid = _str(sid)
            if not sid:
                continue
            self.db.execute(
                "INSERT INTO proc.diavgeia_decision_signer (ada, signer_uid, ord) "
                "VALUES (%s,%s,%s) ON CONFLICT DO NOTHING", (ada, sid, i))

    def _replace_units(self, ada: str, unit_ids) -> None:
        self.db.execute("DELETE FROM proc.diavgeia_decision_unit WHERE ada=%s", (ada,))
        for i, uid in enumerate(unit_ids or []):
            uid = _str(uid)
            if not uid:
                continue
            self.db.execute(
                "INSERT INTO proc.diavgeia_decision_unit (ada, unit_uid, ord) "
                "VALUES (%s,%s,%s) ON CONFLICT DO NOTHING", (ada, uid, i))

    def _replace_thematic(self, ada: str, ids) -> None:
        self.db.execute("DELETE FROM proc.diavgeia_decision_thematic WHERE ada=%s", (ada,))
        for tid in ids or []:
            tid = _str(tid)
            if not tid:
                continue
            self.db.execute(
                "INSERT INTO proc.diavgeia_decision_thematic (ada, thematic_uid) "
                "VALUES (%s,%s) ON CONFLICT DO NOTHING", (ada, tid))

    def _replace_attachments(self, ada: str, attachments) -> None:
        self.db.execute("DELETE FROM proc.diavgeia_attachment WHERE ada=%s", (ada,))
        for a in attachments or []:
            if not isinstance(a, dict):
                continue
            self.db.execute(
                """INSERT INTO proc.diavgeia_attachment (ada, filename, mimetype, url, checksum)
                   VALUES (%s,%s,%s,%s,%s)""",
                (ada, _str(a.get("filename") or a.get("description")),
                 _str(a.get("mimeType") or a.get("mimetype")),
                 _str(a.get("url") or a.get("path")), _str(a.get("checksum"))))

    def _record_related(self, ada: str, ev: dict) -> None:
        self.db.execute("DELETE FROM proc.diavgeia_related WHERE source_ada=%s", (ada,))
        edges = set()
        for rel in (ev.get("relatedDecisions") or []):
            tgt = _str(rel.get("relatedDecisionsADA")) if isinstance(rel, dict) else None
            if tgt:
                edges.add((tgt, "related"))
        text_rel = _str(ev.get("textRelatedADA"))
        if text_rel:
            edges.add((text_rel, "text_related"))
        for tgt, kind in edges:
            self.db.execute(
                "INSERT INTO proc.diavgeia_related (source_ada, target_ada, kind) "
                "VALUES (%s,%s,%s) ON CONFLICT DO NOTHING", (ada, tgt, kind))

    # ---- resolve pass (bounded: one API call per DISTINCT uid, decoupled
    #      from the per-decision ingest hot path) --------------------------- #
    def resolve_authorities(self, batch_commit: int = 200) -> int:
        """Set authority_id on decisions that don't have one, deduping into the
        shared proc.authority table by ΑΦΜ. At most one /organizations API call
        per DISTINCT organizationId (the org dict is also cached in-process).
        Idempotent and re-runnable. Returns the number of distinct orgs resolved."""
        rows = self.db.query(
            "SELECT DISTINCT organization_uid FROM proc.diavgeia_decision "
            "WHERE authority_id IS NULL AND organization_uid IS NOT NULL")
        n = 0
        for i, (org_uid,) in enumerate(rows):
            org_id = self.resolve_authority(org_uid)
            if org_id:
                self.db.execute(
                    "UPDATE proc.diavgeia_decision SET authority_id=%s "
                    "WHERE organization_uid=%s AND authority_id IS NULL",
                    (org_id, org_uid))
                n += 1
            if (i + 1) % batch_commit == 0:
                self.db.commit()
        self.db.commit()
        return n

    def resolve_dictionaries(self, batch_commit: int = 200) -> tuple[int, int]:
        """Populate diavgeia_signer / diavgeia_unit labels for UIDs referenced by
        decisions but not yet in the dictionaries — one API call per NEW uid.
        Optional (the link tables already hold the UIDs)."""
        ns = nu = 0
        srows = self.db.query(
            "SELECT DISTINCT s.signer_uid FROM proc.diavgeia_decision_signer s "
            "LEFT JOIN proc.diavgeia_signer d ON d.uid=s.signer_uid "
            "WHERE d.uid IS NULL")
        for i, (uid,) in enumerate(srows):
            s = self.client.lookup_signer(uid) or {}
            self.db.execute(
                """INSERT INTO proc.diavgeia_signer (uid, first_name, last_name)
                   VALUES (%s,%s,%s) ON CONFLICT (uid) DO UPDATE
                     SET first_name=EXCLUDED.first_name, last_name=EXCLUDED.last_name""",
                (uid, _str(s.get("firstName")), _str(s.get("lastName"))))
            ns += 1
            if (i + 1) % batch_commit == 0:
                self.db.commit()
        urows = self.db.query(
            "SELECT DISTINCT u.unit_uid FROM proc.diavgeia_decision_unit u "
            "LEFT JOIN proc.diavgeia_unit d ON d.uid=u.unit_uid "
            "WHERE d.uid IS NULL")
        for i, (uid,) in enumerate(urows):
            uu = self.client.lookup_unit(uid) or {}
            self.db.execute(
                """INSERT INTO proc.diavgeia_unit (uid, label, category)
                   VALUES (%s,%s,%s) ON CONFLICT (uid) DO UPDATE
                     SET label=EXCLUDED.label, category=EXCLUDED.category""",
                (uid, _str(uu.get("label")), _str(uu.get("category"))))
            nu += 1
            if (i + 1) % batch_commit == 0:
                self.db.commit()
        self.db.commit()
        return ns, nu


# --------------------------------------------------------------------------- #
# Orchestration — windowed, per-decision-type, resumable (mirrors ingest_type)
# --------------------------------------------------------------------------- #
def ingest_type(client: DiavgeiaClient, repo: DiavgeiaRepository,
                decision_type: str, start: dt.date, end: dt.date,
                *, resume: bool = False) -> dict:
    """Ingest [start, end] (by issueDate) for one Diavgeia decision type, tracking
    per-window status in proc.diavgeia_ingest_window (pending→running→done|error).
    One bad window is recorded and skipped; the rest of the run continues.
    Returns {windows, done, skipped, errored}."""
    db = repo.db
    summary = {"windows": 0, "done": 0, "skipped": 0, "errored": 0}

    all_windows = list(windows(start, end, size_days=WINDOW_DAYS))
    summary["windows"] = len(all_windows)
    for w_from, w_to in all_windows:
        db.execute(
            """INSERT INTO proc.diavgeia_ingest_window
                 (decision_type, date_from, date_to, status)
               VALUES (%s,%s,%s,'pending')
               ON CONFLICT (decision_type, date_from, date_to) DO NOTHING""",
            (decision_type, w_from, w_to))
    db.commit()

    done_set = set(_done_windows(db, decision_type)) if resume else set()

    for w_from, w_to in all_windows:
        if (w_from, w_to) in done_set:
            summary["skipped"] += 1
            print(f"[{decision_type}] window {w_from}..{w_to} SKIPPED (already done)")
            continue

        db.execute(
            """UPDATE proc.diavgeia_ingest_window
               SET status='running', started_at=now(), last_error=NULL
               WHERE decision_type=%s AND date_from=%s AND date_to=%s""",
            (decision_type, w_from, w_to))
        db.commit()

        try:
            last_commit = time.monotonic()
            for d in client.search(decision_type, w_from, w_to):
                if time.monotonic() - last_commit >= PROGRESS_COMMIT_SECONDS:
                    db.commit()
                    last_commit = time.monotonic()
                repo.upsert_decision(d)
            db.execute(
                """UPDATE proc.diavgeia_ingest_window
                   SET status='done', finished_at=now()
                   WHERE decision_type=%s AND date_from=%s AND date_to=%s""",
                (decision_type, w_from, w_to))
            db.commit()
            summary["done"] += 1
            print(f"[{decision_type}] window {w_from}..{w_to} done")
        except Exception as e:  # noqa: BLE001 — record and continue
            try:
                db.rollback()
            except Exception:
                pass
            err = f"{type(e).__name__}: {e}"[:1000]
            try:
                db.execute(
                    """UPDATE proc.diavgeia_ingest_window
                       SET status='error', finished_at=now(), last_error=%s
                       WHERE decision_type=%s AND date_from=%s AND date_to=%s""",
                    (err, decision_type, w_from, w_to))
                db.commit()
            except Exception:
                pass
            summary["errored"] += 1
            print(f"[{decision_type}] window {w_from}..{w_to} ERROR: {err}")

    return summary


def _done_windows(db, decision_type: str):
    db.cur.execute(
        """SELECT date_from, date_to FROM proc.diavgeia_ingest_window
           WHERE decision_type=%s AND status='done'""", (decision_type,))
    return [(r[0], r[1]) for r in db.cur.fetchall()]


def watermark(db, decision_type: str):
    """Latest end-date of a done window for this decision type, or None."""
    rows = db.query("""SELECT max(date_to) FROM proc.diavgeia_ingest_window
                       WHERE decision_type=%s AND status='done'""", (decision_type,))
    return rows[0][0] if rows and rows[0][0] else None


def backfill_all(db, start: dt.date, end: dt.date,
                 names=None, *, resume: bool = False, resolve: bool = True):
    client = DiavgeiaClient()
    repo = DiavgeiaRepository(db, client)
    for name in (names or TYPE_NAMES):
        uid = NAME_TO_UID[name]
        print(f"\n=== diavgeia {name} ({uid}): {start} .. {end}"
              f"{' (resume)' if resume else ''} ===")
        ingest_type(client, repo, uid, start, end, resume=resume)
    if resolve:
        print("\n=== resolving authorities (dedupe by ΑΦΜ) ===")
        n = repo.resolve_authorities()
        print(f"  resolved {n} distinct organizations into proc.authority")
