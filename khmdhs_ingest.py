"""
khmdhs_ingest.py — reference ingestion for the Greek public procurement DB.

What it does
------------
1. Backfills each KHMDHS act type (request, notice, auction, contract, payment)
   in <=180-day windows, because the search API silently clamps wider ranges.
2. Respects the 350 requests/minute opendata rate limit.
3. Upserts each act into procurement_act (+ child tables) keyed on ADAM.
4. Records every cross-ADAM reference into act_link, so the
   request->notice->auction->contract->payment graph is fully traversable.
5. Optionally resolves the graph for a given ADAM via the linked-acts endpoint.

This is a readable reference, not production code: swap the DB layer for your
ORM/driver of choice and add retry/backoff + structured logging as needed.

Dependencies: requests, psycopg2-binary  (both optional to merely read the code)
"""

from __future__ import annotations
import time
import datetime as dt
from dataclasses import dataclass, field
from typing import Iterable, Iterator

import json
import re
import unicodedata
import requests

# Adapt a Python dict to a jsonb bind parameter. psycopg2/psycopg3 both need the
# value wrapped (a bare dict raises "cannot adapt type 'dict'"). We try the
# driver's Json wrapper and fall back to a JSON string, so this module stays
# independent of which driver db.py loaded.
def _as_jsonb(value):
    if value is None:
        return None
    try:
        from psycopg.types.json import Json as _Json   # psycopg 3
        return _Json(value)
    except Exception:
        pass
    try:
        from psycopg2.extras import Json as _Json2      # psycopg 2
        return _Json2(value)
    except Exception:
        pass
    return json.dumps(value, ensure_ascii=False)        # last resort: text ::jsonb cast-free

BASE = "https://cerpp.eprocurement.gov.gr"
OPENDATA = f"{BASE}/khmdhs-opendata"

# Endpoint path per act type (search = POST, attachment = GET .../attachment/{adam})
SEARCH_PATH = {
    "request":  "/request",
    "notice":   "/notice",
    "auction":  "/auction",
    "contract": "/contract",
    "payment":  "/payment",
}

# Cross-ADAM reference fields, CONFIRMED against a live probe (2026-05).
# Important quirks discovered:
#   * The link vocabulary differs per type — it is NOT uniform.
#   * Some fields are arrays (handle list), others are a single ADAM string
#     (handle str). record_links() copes with both, so a field can appear in
#     either map without harm.
#
# Array-valued reference fields:
LINK_FIELDS = {
    "request":  [("noticeRefNo",   "request_to_notice"),
                 ("auctionRefNo",  "request_to_auction"),
                 ("contractRefNo", "request_to_contract"),
                 ("paymentRefNo",  "request_to_payment"),
                 ("approvalRefNo", "request_approves")],
    "notice":   [("auctionRefNo",       "notice_to_auction"),
                 ("amendsNoticeRefNo",  "notice_amends_notice")],
    "auction":  [("contractRefNo",      "auction_to_contract"),
                 ("paymentRefNo",       "auction_to_payment"),
                 ("amendsAuctionRefNo", "auction_amends_auction")],
    "contract": [("paymentRefNo",       "contract_to_payment")],
    "payment":  [],
}

# Single-string ADAM pointers (each holds at most one ADAM, or None):
SINGLE_LINK_FIELDS = {
    "notice":   [("amendedNoticeADAM",            "notice_amends_notice"),
                 ("frameworkAgreementNoticeADAM", "framework_of_notice"),
                 ("relatedNoticeADAM",            "notice_related")],
    "auction":  [("noticeRefNo",        "auction_under_notice"),
                 ("amendedAuctionADAM", "auction_amends_auction")],
    "contract": [("auctionRefNo",   "contract_from_auction"),
                 ("requestRefNo",   "contract_from_request"),
                 ("prevReferenceNo","contract_prev"),
                 ("nextRefNo",      "contract_next")],
    "payment":  [("contractRefNo", "payment_for_contract"),
                 ("auctionRefNo",  "payment_for_auction"),
                 ("requestRefNo",  "payment_for_request")],
}

MAX_WINDOW_DAYS = 180
RATE_LIMIT_PER_MIN = 350
# Retry policy for transient failures (429 throttle, 5xx, network blips).
MAX_RETRIES = 5          # attempts per request before giving up
BACKOFF_BASE = 5.0       # seconds; doubles each retry (5, 10, 20, 40, 80)
BACKOFF_CAP = 120.0      # never wait longer than this between tries
RETRY_STATUSES = {429, 500, 502, 503, 504}

# Full-text extraction on import (opt-in via env). When on, each newly-upserted
# act has its official attachment fetched and its text extracted into
# procurement_act.full_text — but ONLY when that column is currently empty, so
# a manual extraction is never clobbered. Scanned PDFs / images (no text layer)
# are skipped silently and left for the manual OCR path on the edit page.
import os as _os
EXTRACT_FULLTEXT = _os.environ.get("EXTRACT_FULLTEXT", "0") == "1"
# Cap one attachment download so a pathological file can't stall a window.
MAX_ATTACHMENT_BYTES = int(_os.environ.get("FULLTEXT_MAX_MB", "60")) * 1024 * 1024

# Per-act transparency log. When a run is launched from the admin UI it passes
# its proc.ingest_job id via INGEST_JOB_ID; the ingest loop then records one
# proc.ingest_act_log row per act (see Repository.log_act). A plain CLI backfill
# leaves this unset and writes no per-act rows — opt-in, no shell-path overhead.
try:
    INGEST_JOB_ID = int(_os.environ["INGEST_JOB_ID"])
except (KeyError, ValueError):
    INGEST_JOB_ID = None

# How often to flush in-progress work mid-window so the admin job page shows
# live progress (acts handled so far) instead of nothing until the whole
# 180-day window commits. Commits are cheap and the upserts are idempotent, so
# a window that errors after a flush just gets re-processed on resume.
PROGRESS_COMMIT_SECONDS = float(_os.environ.get("INGEST_PROGRESS_COMMIT_SECONDS", "3"))


# --------------------------------------------------------------------------- #
# Rate limiter: simple token-bucket sized to the documented 350 req/min.
# --------------------------------------------------------------------------- #
class RateLimiter:
    def __init__(self, per_minute: int = RATE_LIMIT_PER_MIN):
        self.min_interval = 60.0 / per_minute
        self._last = 0.0

    def wait(self) -> None:
        now = time.monotonic()
        delta = now - self._last
        if delta < self.min_interval:
            time.sleep(self.min_interval - delta)
        self._last = time.monotonic()


# --------------------------------------------------------------------------- #
# API client
# --------------------------------------------------------------------------- #
@dataclass
class KhmdhsClient:
    session: requests.Session = field(default_factory=requests.Session)
    limiter: RateLimiter = field(default_factory=RateLimiter)

    def _post(self, path: str, body: dict, page: int) -> dict:
        """POST one page, retrying transient failures with exponential backoff.

        Retries on 429 (throttle) and 5xx, plus network/timeout errors, up to
        MAX_RETRIES times. Honors a Retry-After header when present; otherwise
        backs off 5,10,20,… seconds (capped). The proactive rate limiter still
        paces every attempt, so this only kicks in when the server pushes back.
        Raises after the cap so the window is recorded as an error and can be
        retried later with --resume."""
        url = f"{OPENDATA}{path}"
        last_exc: Exception | None = None
        for attempt in range(MAX_RETRIES):
            self.limiter.wait()
            try:
                r = self.session.post(
                    url, params={"page": page}, json=body,
                    headers={"Accept": "application/json"}, timeout=60,
                )
            except (requests.ConnectionError, requests.Timeout) as e:
                # network blip — back off and retry
                last_exc = e
                self._sleep_backoff(attempt, None)
                continue

            if r.status_code in RETRY_STATUSES:
                if attempt == MAX_RETRIES - 1:
                    r.raise_for_status()   # out of retries → surface the error
                self._sleep_backoff(attempt, r.headers.get("Retry-After"))
                continue

            r.raise_for_status()           # non-retryable error → raise now
            return r.json()

        # exhausted retries on network errors
        if last_exc:
            raise last_exc
        raise RuntimeError(f"giving up on {url} page {page} after {MAX_RETRIES} tries")

    @staticmethod
    def _sleep_backoff(attempt: int, retry_after: str | None) -> None:
        """Wait before the next attempt: honor Retry-After if the server sent a
        usable value, else exponential backoff (BACKOFF_BASE * 2**attempt)."""
        delay = None
        if retry_after:
            try:
                delay = float(retry_after)        # Retry-After: seconds form
            except (TypeError, ValueError):
                delay = None                       # HTTP-date form — ignore, use backoff
        if delay is None:
            delay = BACKOFF_BASE * (2 ** attempt)
        time.sleep(min(delay, BACKOFF_CAP))

    def search(self, act_type: str, body: dict) -> Iterator[dict]:
        """Yield every act object across all result pages for one search body."""
        path = SEARCH_PATH[act_type]
        page = 0
        while True:
            data = self._post(path, body, page)
            for item in data.get("content", []):
                yield item
            if data.get("last", True):
                break
            page += 1

    def attachment_url(self, act_type: str, adam: str) -> str:
        return f"{OPENDATA}{SEARCH_PATH[act_type]}/attachment/{adam}"

    def fetch_attachment(self, act_type: str, adam: str) -> tuple[bytes, str] | None:
        """GET an act's official KHMDHS document. Returns (data, filename) or
        None if there's no attachment / it can't be fetched. Uses the shared
        session and rate limiter so these downloads are paced alongside searches.
        Fail-soft by design: any error returns None rather than raising, so a
        bad attachment never aborts an ingest window."""
        url = self.attachment_url(act_type, adam)
        for attempt in range(MAX_RETRIES):
            self.limiter.wait()
            try:
                r = self.session.get(url, timeout=120, stream=True)
            except (requests.ConnectionError, requests.Timeout):
                self._sleep_backoff(attempt, None)
                continue
            if r.status_code == 404:
                return None  # no document for this act — normal, not an error
            if r.status_code in RETRY_STATUSES:
                if attempt == MAX_RETRIES - 1:
                    return None
                self._sleep_backoff(attempt, r.headers.get("Retry-After"))
                continue
            if r.status_code != 200:
                return None
            # stream with a hard size cap
            chunks: list[bytes] = []
            total = 0
            try:
                for chunk in r.iter_content(64 * 1024):
                    if not chunk:
                        continue
                    total += len(chunk)
                    if total > MAX_ATTACHMENT_BYTES:
                        return None  # oversize — skip rather than buffer it all
                    chunks.append(chunk)
            except (requests.ConnectionError, requests.Timeout):
                return None
            data = b"".join(chunks)
            if not data:
                return None
            # name it so the extractor sniffs by extension; PDF default, zip if magic
            fname = f"{adam}.zip" if data[:2] == b"PK" else f"{adam}.pdf"
            return data, fname
        return None


# --------------------------------------------------------------------------- #
# 180-day window generator
# --------------------------------------------------------------------------- #
def windows(start: dt.date, end: dt.date,
            size_days: int = MAX_WINDOW_DAYS) -> Iterator[tuple[dt.date, dt.date]]:
    cur = start
    step = dt.timedelta(days=size_days)
    one = dt.timedelta(days=1)
    while cur <= end:
        w_end = min(cur + step - one, end)
        yield cur, w_end
        cur = w_end + one


def search_body(date_from: dt.date, date_to: dt.date) -> dict:
    # Minimal body: just the registration-date window. Add cpvItems/organizations
    # /contractType etc. here to scope a narrower harvest.
    return {"dateFrom": date_from.isoformat(), "dateTo": date_to.isoformat()}


# --------------------------------------------------------------------------- #
# Mapping helpers: pull a {key,value} safely
# --------------------------------------------------------------------------- #
def kv_key(obj: dict, field_name: str) -> str | None:
    v = obj.get(field_name)
    return v.get("key") if isinstance(v, dict) else (v if isinstance(v, str) else None)

def kv_label(obj: dict, field_name: str) -> str | None:
    v = obj.get(field_name)
    return v.get("value") if isinstance(v, dict) else None


# --------------------------------------------------------------------------- #
# Persistence (sketch). Replace `db` with your real connection/ORM.
# Every function is idempotent: upsert on the natural key so re-runs are safe.
# --------------------------------------------------------------------------- #
class Repository:
    def __init__(self, db):
        self.db = db  # e.g. a psycopg2 connection

    # ---- parties -------------------------------------------------------- #
    def upsert_authority(self, act: dict) -> str | None:
        org = act.get("organization")
        if not org:
            return None
        org_id, name = org.get("key"), org.get("value")
        self.db.execute(
            """INSERT INTO proc.authority
                 (org_id, name, vat_number, is_greek_vat, aaht,
                  type_code, classification_code, last_seen)
               VALUES (%s,%s,%s,%s,%s,%s,%s, now())
               ON CONFLICT (org_id) DO UPDATE
                 SET name=EXCLUDED.name,
                     vat_number=COALESCE(EXCLUDED.vat_number, proc.authority.vat_number),
                     last_seen=now()""",
            (org_id, name, act.get("organizationVatNumber"),
             act.get("greekOrganizationVatNumber"), act.get("aaht"),
             kv_key(act, "typeOfContractingAuthority"),
             kv_key(act, "classificationOfPublicLawOrganization")),
        )
        return org_id

    def upsert_unit_and_signer(self, act: dict, authority_id: str | None):
        # request/notice/payment expose contractingData; auction/contract nest
        # the same shape under contractingDataDetails (which also carries
        # contractingMembersDataList — the awarded parties; see act_operator).
        cd = act.get("contractingData") or act.get("contractingDataDetails") or {}
        unit, signer = cd.get("unitsOperator"), cd.get("signers")
        unit_id = signer_id = None
        if isinstance(unit, dict) and unit.get("key"):
            unit_id = unit["key"]
            self.db.execute(
                """INSERT INTO proc.org_unit (unit_id, name, authority_id)
                   VALUES (%s,%s,%s) ON CONFLICT (unit_id) DO UPDATE
                     SET name=EXCLUDED.name""",
                (unit_id, unit.get("value"), authority_id))
        if isinstance(signer, dict) and signer.get("key"):
            signer_id = signer["key"]
            self.db.execute(
                """INSERT INTO proc.signer (signer_id, name, authority_id)
                   VALUES (%s,%s,%s) ON CONFLICT (signer_id) DO UPDATE
                     SET name=EXCLUDED.name""",
                (signer_id, signer.get("value"), authority_id))
        return unit_id, signer_id

    # ---- core act ------------------------------------------------------- #
    def _ensure_nuts(self, act: dict):
        """Upsert the NUTS code (and label) so the act's FK is satisfied.

        nutsCode arrives as {key,value}; the value is the region label. We also
        upsert any place-of-performance codes from nutsCodes[]/nutsCodeList[].
        """
        seen = set()
        main = act.get("nutsCode")
        candidates = []
        if isinstance(main, dict) and main.get("key"):
            candidates.append((main["key"], main.get("value")))
        for arr_name in ("nutsCodes", "nutsCodeList"):
            for n in act.get(arr_name) or []:
                if isinstance(n, dict):
                    code = n.get("key") or kv_key(n, "nutsCode")
                    if code:
                        candidates.append((code, n.get("value")))
        for code, label in candidates:
            if not code or code in seen:
                continue
            seen.add(code)
            self.db.execute(
                """INSERT INTO proc.nuts_code (nuts_code, label)
                   VALUES (%s,%s) ON CONFLICT (nuts_code) DO UPDATE
                     SET label=COALESCE(EXCLUDED.label, proc.nuts_code.label)""",
                (code, label))

    def upsert_act(self, act_type: str, act: dict,
                   authority_id, unit_id, signer_id):
        adam = act["referenceNumber"]
        self._ensure_nuts(act)
        inserted = self.db.execute_returning(
            """INSERT INTO proc.procurement_act
                 (adam, type, title, signed_date, submission_date,
                  last_update_date, published_eu_date, final_submission_date,
                  procurement_delivery_date, cancelled, cancellation_date,
                  cancellation_type, cancellation_reason, cancellation_ada,
                  is_modified, amends_previous, amended_adam,
                  contract_type_code, mixed_contract, procedure_type_code,
                  award_procedure_code, criteria_code, legal_context_code,
                  notice_type_code, conducting_proceedings_code,
                  digital_platform_code, contracting_authority_activity_code,
                  budget, total_cost_without_vat, total_cost_with_vat,
                  nuts_code, city, postal_code, country,
                  authority_id, org_unit_id, signer_id,
                  number_of_sections, contract_duration, contract_duration_unit,
                  offers_valid_time, offers_valid_time_unit,
                  max_number_of_contractors, option_right, option_right_description,
                  framework_agreement_adam, bidding_website,
                  contract_number, contract_signed_date, start_date, end_date,
                  no_end_date, assign_criteria_code, bids_submitted, max_bids_submitted,
                  is_credit, payment_commitment_code, contract_value,
                  approval_ada, commitment_no, protocol_number, author_email,
                  raw_json, source_endpoint)
               VALUES (%(adam)s, %(type)s, %(title)s, %(signed_date)s, %(submission_date)s,
                  %(last_update_date)s, %(published_eu_date)s, %(final_submission_date)s,
                  %(procurement_delivery_date)s, %(cancelled)s, %(cancellation_date)s,
                  %(cancellation_type)s, %(cancellation_reason)s, %(cancellation_ada)s,
                  %(is_modified)s, %(amends_previous)s, %(amended_adam)s,
                  %(contract_type_code)s, %(mixed_contract)s, %(procedure_type_code)s,
                  %(award_procedure_code)s, %(criteria_code)s, %(legal_context_code)s,
                  %(notice_type_code)s, %(conducting_proceedings_code)s,
                  %(digital_platform_code)s, %(contracting_authority_activity_code)s,
                  %(budget)s, %(total_cost_without_vat)s, %(total_cost_with_vat)s,
                  %(nuts_code)s, %(city)s, %(postal_code)s, %(country)s,
                  %(authority_id)s, %(org_unit_id)s, %(signer_id)s,
                  %(number_of_sections)s, %(contract_duration)s, %(contract_duration_unit)s,
                  %(offers_valid_time)s, %(offers_valid_time_unit)s,
                  %(max_number_of_contractors)s, %(option_right)s, %(option_right_description)s,
                  %(framework_agreement_adam)s, %(bidding_website)s,
                  %(contract_number)s, %(contract_signed_date)s, %(start_date)s, %(end_date)s,
                  %(no_end_date)s, %(assign_criteria_code)s, %(bids_submitted)s, %(max_bids_submitted)s,
                  %(is_credit)s, %(payment_commitment_code)s, %(contract_value)s,
                  %(approval_ada)s, %(commitment_no)s, %(protocol_number)s, %(author_email)s,
                  %(raw_json)s, %(source_endpoint)s)
               ON CONFLICT (adam) DO UPDATE SET
                  title=EXCLUDED.title, last_update_date=EXCLUDED.last_update_date,
                  cancelled=EXCLUDED.cancelled, cancellation_date=EXCLUDED.cancellation_date,
                  total_cost_without_vat=EXCLUDED.total_cost_without_vat,
                  total_cost_with_vat=EXCLUDED.total_cost_with_vat,
                  raw_json=EXCLUDED.raw_json, ingested_at=now()
               WHERE proc.procurement_act.origin = 'import'
               RETURNING (xmax = 0) AS inserted""",
            {
                "adam": adam, "type": act_type, "title": act.get("title"),
                "signed_date": act.get("signedDate"),
                "submission_date": act.get("submissionDate"),
                "last_update_date": act.get("lastUpdateDate"),
                "published_eu_date": act.get("publishedDate"),
                "final_submission_date": act.get("finalSubmissionDate"),
                "procurement_delivery_date": act.get("procurementDeliveryDate"),
                "cancelled": act.get("cancelled", False),
                "cancellation_date": act.get("cancellationDate"),
                "cancellation_type": act.get("cancellationType"),
                "cancellation_reason": act.get("cancellationReason"),
                "cancellation_ada": act.get("cancellationADA"),
                "is_modified": act.get("amendPreviousNotice"),
                "amends_previous": act.get("amendPreviousNotice"),
                "amended_adam": act.get("amendedNoticeADAM"),
                "contract_type_code": kv_key(act, "contractType"),
                "mixed_contract": act.get("mixedContract"),
                "procedure_type_code": kv_key(act, "typeOfProcedure") or act.get("procedureType"),
                "award_procedure_code": kv_key(act, "awardProcedure"),
                "criteria_code": kv_key(act, "criteriaCode"),
                "legal_context_code": kv_key(act, "legalContext"),
                "notice_type_code": kv_key(act, "noticeType"),
                "conducting_proceedings_code": kv_key(act, "conductingProceedings"),
                "digital_platform_code": kv_key(act, "digitalPlatform"),
                "contracting_authority_activity_code": kv_key(act, "contractingAuthorityActivity"),
                "budget": act.get("budget"),
                "total_cost_without_vat": act.get("totalCostWithoutVAT"),
                "total_cost_with_vat": act.get("totalCostWithVAT"),
                "nuts_code": kv_key(act, "nutsCode"),
                "city": act.get("nutsCity"), "postal_code": act.get("nutsPostalCode"),
                # nutsCountry comes as {key,value} on most types (key='GR'); fall
                # back to a plain string if a type ever sends one.
                "country": kv_key(act, "nutsCountry") or (
                    act.get("nutsCountry") if isinstance(act.get("nutsCountry"), str) else None),
                "authority_id": authority_id, "org_unit_id": unit_id, "signer_id": signer_id,
                "number_of_sections": act.get("numberOfSections"),
                "contract_duration": act.get("contractDuration"),
                "contract_duration_unit": kv_key(act, "contractDurationUnitOfMeasure"),
                "offers_valid_time": act.get("offersValidTime"),
                "offers_valid_time_unit": kv_key(act, "offersValidTimeUnitOfMeasure"),
                "max_number_of_contractors": act.get("maxNumberOfContractors"),
                "option_right": (kv_key(act, "optionRight") == "1") if act.get("optionRight") else None,
                "option_right_description": act.get("optionRightDescription"),
                "framework_agreement_adam": act.get("frameworkAgreementNoticeADAM"),
                "bidding_website": act.get("biddingWebsite"),
                # contract-specific
                "contract_number": act.get("contractNumber"),
                "contract_signed_date": act.get("contractSignedDate"),
                "start_date": act.get("startDate"),
                "end_date": act.get("endDate"),
                "no_end_date": act.get("noEndDate"),
                "assign_criteria_code": kv_key(act, "assignCriteria"),
                "bids_submitted": act.get("bidsSubmitted"),
                "max_bids_submitted": act.get("maxBidsSubmitted"),
                # payment-specific
                "is_credit": act.get("credit"),
                "payment_commitment_code": act.get("paymentCommitmentCode"),
                "contract_value": act.get("contractValue"),
                "approval_ada": act.get("approvalADA"),
                "commitment_no": act.get("commitmentNo"),
                "protocol_number": act.get("protocolNumber"),
                "author_email": act.get("authorEmail"),
                "raw_json": _as_jsonb(act), "source_endpoint": SEARCH_PATH[act_type],
            },
        )
        # execute_returning gives back RETURNING (xmax = 0) as a scalar, or None
        # when no row was affected:
        #   True  -> fresh INSERT                       -> 'new'
        #   False -> conflict-update of an import row    -> 'updated'
        #   None  -> conflict row is AUTHORED, so the DO UPDATE ... WHERE
        #            origin='import' matched nothing     -> 'skipped_authored'
        # Best-effort label for the run log; the authored-act guard in the loop
        # is separate and authoritative.
        if inserted is None:
            return "skipped_authored"
        return "new" if inserted else "updated"

    def is_authored(self, adam: str) -> bool:
        """True if this ADAM exists as an AUTHORED (manually created/edited) act.
        The import pipeline uses this to leave such acts — and their child
        tables — completely untouched on re-import."""
        # NB: db.execute() returns None (it doesn't hand back the cursor), so we
        # go through execute_returning, which runs the query and returns the
        # first column (or None when the act isn't present yet).
        origin = self.db.execute_returning(
            "SELECT origin FROM proc.procurement_act WHERE adam = %s", (adam,))
        return origin == "authored"

    def log_act(self, job_id: int, adam: str, act_type: str, title,
                action: str, ft_extracted: bool, ft_chars, ft_note) -> None:
        """Append one per-act transparency row for an admin-launched run. Written
        inside the current window transaction, so it commits/rolls back together
        with the act rows it describes (a rolled-back window logs nothing)."""
        self.db.execute(
            """INSERT INTO proc.ingest_act_log
                 (job_id, adam, act_type, title, action,
                  full_text_extracted, full_text_chars, full_text_note)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
            (job_id, adam, act_type, title, action,
             ft_extracted, ft_chars, ft_note),
        )

    def mark_full_text_attempted_empty(self, adam: str, reason: str):
        """Record that we tried to extract text for this act but got none
        (scanned PDF, no attachment, etc.), WITHOUT setting full_text. This lets
        a mass re-run skip acts already known to yield nothing — otherwise every
        run would re-download the same un-extractable attachments forever.
        Only marks rows that are still untouched (full_text NULL and no prior
        source), so it never overwrites real text or a manual edit."""
        self.db.execute(
            """UPDATE proc.procurement_act
               SET full_text_extracted_at = now(),
                   full_text_source = %(reason)s
               WHERE adam = %(adam)s
                 AND full_text IS NULL
                 AND full_text_source IS NULL""",
            {"reason": reason, "adam": adam},
        )

    def set_full_text_if_empty(self, adam: str, text: str, source: str) -> bool:
        """Write full_text for an act ONLY if it's currently NULL/empty, so an
        auto-extraction never overwrites a value a curator put there by hand.
        Returns True if a row was updated."""
        self.db.execute(
            """UPDATE proc.procurement_act
               SET full_text = %(text)s,
                   full_text_extracted_at = now(),
                   full_text_source = %(source)s
               WHERE adam = %(adam)s
                 AND (full_text IS NULL OR full_text = '')""",
            {"text": text, "source": source, "adam": adam},
        )
        # rowcount reflects whether the fill-only-if-empty WHERE matched: 1 when
        # we actually wrote, 0 when full_text was already set (left untouched).
        # Used only for the run log's 'extracted' vs 'exists' note.
        return self.db.cur.rowcount > 0
    def replace_object_details(self, adam: str, act: dict):
        self.db.execute("DELETE FROM proc.act_object_detail WHERE adam=%s", (adam,))
        # request/notice/payment use 'objectDetails'; auction/contract use
        # 'objectDetailsList'. Accept either.
        details = act.get("objectDetails") or act.get("objectDetailsList") or []
        for i, od in enumerate(details):
            row_id = self.db.execute_returning(
                """INSERT INTO proc.act_object_detail
                     (adam, line_no, short_description, quantity, unit_code,
                      cost_without_vat, vat_rate, currency_code,
                      green_contract_code, good_services_code, budget_code)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                (adam, i, od.get("shortDescription"), od.get("quantity"),
                 kv_key(od, "type"), od.get("costWithoutVAT"), od.get("vat"),
                 kv_key(od, "currency"), kv_key(od, "greenContracts"),
                 kv_key(od, "goodServices"), od.get("budgetCode")))
            for cpv in od.get("cpvs") or []:
                code = cpv.get("key") if isinstance(cpv, dict) else cpv
                if not code:
                    continue
                self.db.execute(
                    """INSERT INTO proc.cpv_code (cpv_code, description)
                       VALUES (%s,%s) ON CONFLICT (cpv_code) DO NOTHING""",
                    (code, cpv.get("value") if isinstance(cpv, dict) else None))
                self.db.execute(
                    """INSERT INTO proc.object_detail_cpv (object_detail_id, cpv_code)
                       VALUES (%s,%s) ON CONFLICT DO NOTHING""", (row_id, code))

    # ---- contractors -------------------------------------------------------#
    def record_contractors(self, act_type: str, act: dict):
        """Extract economic-operator identity.

        KEY FINDING from the live probe: the contractor's VAT + name live on
        PAYMENT line items (objectDetails[].vatNo / greekVatNo / name / country),
        not as a top-level field. Auctions/contracts instead carry awarded
        parties under contractingDataDetails.contractingMembersDataList — wire
        that in once its sub-shape is confirmed.
        """
        adam = act["referenceNumber"]

        # (a) Payment line-item suppliers -> winner of the linked contract.
        if act_type == "payment":
            seen = set()
            for od in act.get("objectDetails") or []:
                vat = self._scalar(od.get("vatNo"))
                name = self._scalar(od.get("name"))
                key = (str(vat) if vat else None, str(name) if name else None)
                if not (vat or name) or key in seen:
                    continue
                seen.add(key)
                op_id = self._upsert_operator(
                    vat=vat, name=name,
                    is_greek=od.get("greekVatNo"), country=od.get("country"))
                if op_id:
                    self.db.execute(
                        """INSERT INTO proc.act_operator (adam, operator_id, role)
                           VALUES (%s,%s,'winner') ON CONFLICT DO NOTHING""",
                        (adam, op_id))

        # (b) Awarded members on auctions/contracts (TENTATIVE shape).
        cdd = act.get("contractingDataDetails") or {}
        for member in cdd.get("contractingMembersDataList") or []:
            if not isinstance(member, dict):
                continue
            op_id = self._upsert_operator(
                vat=member.get("vatNo") or member.get("vatNumber"),
                name=member.get("name"),
                is_greek=member.get("greekVatNo"),
                country=member.get("country"))
            if op_id:
                self.db.execute(
                    """INSERT INTO proc.act_operator (adam, operator_id, role)
                       VALUES (%s,%s,'winner') ON CONFLICT DO NOTHING""",
                    (adam, op_id))

    @staticmethod
    def _scalar(v):
        """Coerce a value that might be a {key,value} dict (or {value:..}) to a
        plain scalar. Live KHMDHS data sends greekVatNo / country / even vatNo as
        either a primitive or a {key,value} object depending on the record."""
        if isinstance(v, dict):
            return v.get("value", v.get("key"))
        return v

    @staticmethod
    def _as_bool(v):
        """Coerce greekVatNo to a real bool/None (it arrives as bool, 'true'/'1'
        string, or {key,value})."""
        v = Repository._scalar(v)
        if isinstance(v, bool) or v is None:
            return v
        if isinstance(v, (int, float)):
            return bool(v)
        if isinstance(v, str):
            return v.strip().lower() in ("true", "1", "yes", "ναι", "y")
        return None

    def _upsert_operator(self, vat, name, is_greek=None, country=None):
        # Normalise every field to a bindable scalar (live data sometimes nests
        # these as {key,value} dicts, which psycopg cannot adapt).
        vat = self._scalar(vat)
        name = self._scalar(name)
        country = self._scalar(country)
        is_greek = self._as_bool(is_greek)
        vat = str(vat).strip() if vat not in (None, "") else None
        name = str(name).strip() if name not in (None, "") else None
        country = str(country).strip() if country not in (None, "") else None

        if not (vat or name):
            return None
        # VAT is the natural key when present; otherwise fall back to name.
        if vat:
            return self.db.execute_returning(
                """INSERT INTO proc.economic_operator
                     (vat_number, name, is_greek_vat, country, last_seen)
                   VALUES (%s,%s,%s,%s, now())
                   ON CONFLICT (vat_number) DO UPDATE
                     SET name=EXCLUDED.name, last_seen=now()
                   RETURNING operator_id""",
                (vat, name or "(unknown)", is_greek, country))
        return self.db.execute_returning(
            """INSERT INTO proc.economic_operator (name, country, last_seen)
               VALUES (%s,%s, now()) RETURNING operator_id""",
            (name, country))

    # ---- link graph ----------------------------------------------------- #
    def record_links(self, act_type: str, act: dict):
        src = act["referenceNumber"]

        def add_edge(target, relation):
            if target:
                self.db.execute(
                    """INSERT INTO proc.act_link (source_adam, target_adam, relation)
                       VALUES (%s,%s,%s) ON CONFLICT DO NOTHING""",
                    (src, target, relation))

        # Array-valued reference fields (a field may also arrive as a bare
        # string on some types, so coerce to a list).
        for field_name, relation in LINK_FIELDS.get(act_type, []):
            val = act.get(field_name)
            targets = val if isinstance(val, list) else ([val] if val else [])
            for target in targets:
                add_edge(target, relation)

        # Single-string ADAM pointers (also tolerate a stray list).
        for field_name, relation in SINGLE_LINK_FIELDS.get(act_type, []):
            val = act.get(field_name)
            if isinstance(val, list):
                for target in val:
                    add_edge(target, relation)
            else:
                add_edge(val, relation)

    # ---- diavgeia bridge ------------------------------------------------ #
    def record_diavgeia_links(self, act: dict):
        adam = act["referenceNumber"]
        for fld, kind in (("approvalADA", "approval"), ("cancellationADA", "cancellation")):
            ada = act.get(fld)
            if ada:
                self.db.execute(
                    """INSERT INTO proc.act_diavgeia_link (adam, ada, link_kind)
                       VALUES (%s,%s,%s) ON CONFLICT DO NOTHING""", (adam, ada, kind))


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #
# Heuristic detection of garbled / mojibake extraction (a PDF whose font has no
# ToUnicode map, so pdfminer emits "(cid:N)" tokens; U+FFFD replacement chars;
# or Greek that decoded into Latin/symbol soup). Cheap character-class checks
# only — no language model. Thresholds are conservative (require a minimum count
# AND a ratio) to avoid false positives on short or code-heavy text. Used to
# FLAG, never to reject — the text is still stored, just marked for manual OCR.
_GREEK_RE = re.compile(r"[Ͱ-Ͽἀ-῿]")
_LATIN_RE = re.compile(r"[A-Za-z]")
_CID_RE = re.compile(r"\(cid:\d+\)")


def looks_garbled(text: str | None) -> bool:
    """True if extracted text looks garbled rather than real document text."""
    if not text:
        return False
    s = text[:20000]                       # cap work on huge documents
    n = len(s)
    # 1) Unmapped-glyph "(cid:N)" tokens — the strongest signal.
    cid = len(_CID_RE.findall(s))
    if cid >= 5 and cid * 40 > n:
        return True
    # 2) Unicode replacement characters.
    repl = s.count("�")
    if repl >= 5 and repl * 50 > n:
        return True
    # 3) Mojibake: plenty of letters but almost no Greek (Greek docs are ~always
    #    mostly Greek; a near-zero Greek ratio over substantial text is suspect).
    greek = len(_GREEK_RE.findall(s))
    latin = len(_LATIN_RE.findall(s))
    letters = greek + latin
    if letters >= 300 and greek * 100 < letters * 12:    # < 12% Greek
        return True
    # 4) Control / non-printable noise (excluding ordinary whitespace).
    ctrl = sum(1 for ch in s
               if ch not in "\t\n\r\f\v" and unicodedata.category(ch)[0] == "C")
    if ctrl >= 20 and ctrl * 100 > n:                    # > 1% control chars
        return True
    return False


def _local_ocr_text(data: bytes) -> str | None:
    """Try the local Tesseract OCR tier on a document's bytes. Returns clean text
    ONLY if it clears the garbled heuristic; otherwise None, so the caller keeps
    the pdfplumber result (garbled) or flags the doc for the Anthropic tier.
    Fail-soft + lazily imported so the ingester still runs without the tier."""
    try:
        import local_ocr
    except Exception:
        return None
    try:
        if not local_ocr.enabled():
            return None
        ocr = local_ocr.ocr_pdf(data)
    except Exception:
        return None
    if ocr and not looks_garbled(ocr):
        return ocr
    return None


def extract_full_text_for_act(client: "KhmdhsClient", repo: "Repository",
                              act_type: str, adam: str) -> tuple[bool, int | None, str]:
    """Fetch an act's attachment, extract its text, and store it (fill-only-if-
    empty). Entirely fail-soft: any problem is swallowed so a single act can
    never break the ingest window. Scanned/no-text-layer documents yield no
    text and are simply skipped, leaving full_text NULL for the manual OCR path.

    Imported lazily so the ingester still imports cleanly on a machine that
    doesn't have the extraction libs installed (e.g. one that only reads data).

    Returns (extracted, chars, note) for the run log:
      (True,  n,    'extracted')      text fetched and stored (n chars)
      (True,  n,    'garbled')        text stored but looks garbled (needs OCR)
      (False, n,    'exists')         text found but full_text already set
      (False, None, 'no_attachment')  no document to fetch
      (False, None, 'no_text')        attachment has no text layer (scanned)
      (False, None, 'libs_missing')   extraction libs not installed
      (False, None, 'error')          anything threw
    """
    try:
        fetched = client.fetch_attachment(act_type, adam)
        if not fetched:
            return (False, None, "no_attachment")
        data, fname = fetched
        try:
            from app.extractors import extract_text_from_upload
        except ImportError:
            try:
                from extractors import extract_text_from_upload
            except ImportError:
                return (False, None, "libs_missing")
        text = extract_text_from_upload(fname, data)
        garbled = bool(text) and looks_garbled(text)
        source, used_ocr = "auto:import", False
        # Local OCR tier: for scanned (no text layer) or garbled (broken-font)
        # documents, try Tesseract before giving up / flagging for the API tier.
        if (not text) or garbled:
            ocr = _local_ocr_text(data)
            if ocr:
                text, garbled, source, used_ocr = ocr, False, "auto:ocr-local", True
        if not text:
            return (False, None, "no_text")  # scanned & local OCR unavailable/failed
        # Flag (don't reject) still-garbled output: stored but marked so it
        # surfaces for the Anthropic OCR path; a manual re-save overwrites it.
        if garbled:
            source = "auto:garbled?"
        wrote = repo.set_full_text_if_empty(adam, text, source=source)
        if wrote:
            note = "ocr_local" if used_ocr else ("garbled" if garbled else "extracted")
            return (True, len(text), note)
        return (False, len(text), "exists")
    except Exception:  # noqa: BLE001 — never propagate into the ingest loop
        return (False, None, "error")


def extract_full_text_status(client: "KhmdhsClient", repo: "Repository",
                             act_type: str, adam: str) -> str:
    """Like extract_full_text_for_act, but for the MASS backfill: returns a
    status and records 'tried but empty' so a resumed run can skip acts that
    will never yield text. Returns one of:
        'stored'  — text extracted and saved
        'garbled' — text saved but looks garbled (flagged for manual OCR)
        'empty'   — no attachment / no text layer (marked, won't be retried)
        'error'   — a fetch/parse error (left unmarked so it CAN be retried)
    Fail-soft: never raises.
    """
    try:
        fetched = client.fetch_attachment(act_type, adam)
        if not fetched:
            repo.mark_full_text_attempted_empty(adam, "auto:no-attachment")
            return "empty"
        data, fname = fetched
        try:
            from app.extractors import extract_text_from_upload
        except ImportError:
            try:
                from extractors import extract_text_from_upload
            except ImportError:
                return "error"  # libs missing — don't mark, allow retry later
        text = extract_text_from_upload(fname, data)
        garbled = bool(text) and looks_garbled(text)
        source = "auto:mass"
        # Local OCR tier (scanned / garbled) before flagging for the API tier.
        if (not text) or garbled:
            ocr = _local_ocr_text(data)
            if ocr:
                text, garbled, source = ocr, False, "auto:ocr-local"
        if not text:
            repo.mark_full_text_attempted_empty(adam, "auto:no-text")
            return "empty"
        if garbled:
            source = "auto:garbled?"
        repo.set_full_text_if_empty(adam, text, source=source)
        return "garbled" if garbled else "stored"
    except Exception:  # noqa: BLE001
        return "error"


def ingest_type(client: KhmdhsClient, repo: Repository, act_type: str,
                start: dt.date, end: dt.date, *, resume: bool = False) -> dict:
    """Ingest a date range for one act type, recording per-window progress.

    Each 180-day window is tracked in proc.ingest_window with status
    pending -> running -> done | error. On `resume=True`, windows already
    marked 'done' (over the same act_type+date_from+date_to) are skipped.
    Per-window errors are caught and recorded so one bad window does not
    abort the rest of the backfill — the next window is attempted as normal.

    Returns a summary dict {windows, done, skipped, errored}.
    """
    db = repo.db
    summary = {"windows": 0, "done": 0, "skipped": 0, "errored": 0}

    # 1. Pre-register every window for this range as 'pending' (idempotent).
    all_windows = list(windows(start, end))
    summary["windows"] = len(all_windows)
    for w_from, w_to in all_windows:
        db.execute(
            """INSERT INTO proc.ingest_window (act_type, date_from, date_to, status)
               VALUES (%s,%s,%s,'pending')
               ON CONFLICT (act_type, date_from, date_to) DO NOTHING""",
            (act_type, w_from, w_to))
    db.commit()

    # 2. Build the worklist. On resume, skip windows already 'done'; otherwise
    #    process all of them (so a non-resume re-run re-fetches everything,
    #    which the upserts make safe).
    if resume:
        done_set = set(_done_windows(db, act_type))
    else:
        done_set = set()

    for w_from, w_to in all_windows:
        if (w_from, w_to) in done_set:
            summary["skipped"] += 1
            print(f"[{act_type}] window {w_from}..{w_to} SKIPPED (already done)")
            continue

        # 3. Mark running, then process. A crash here leaves status='running',
        #    which resume will pick up and reprocess on the next invocation.
        db.execute(
            """UPDATE proc.ingest_window
               SET status='running', started_at=now(), last_error=NULL
               WHERE act_type=%s AND date_from=%s AND date_to=%s""",
            (act_type, w_from, w_to))
        db.commit()

        try:
            body = search_body(w_from, w_to)
            last_commit = time.monotonic()
            for act in client.search(act_type, body):
                # Flush accumulated work every few seconds so the job page shows
                # acts handled so far DURING the run (the page reads on its own
                # connection and only sees committed rows). Idempotent upserts +
                # per-window resume make a mid-window flush safe.
                if time.monotonic() - last_commit >= PROGRESS_COMMIT_SECONDS:
                    db.commit()
                    last_commit = time.monotonic()
                adam = act.get("referenceNumber")
                if not adam:
                    continue
                authority_id = repo.upsert_authority(act)
                unit_id, signer_id = repo.upsert_unit_and_signer(act, authority_id)
                action = repo.upsert_act(act_type, act, authority_id, unit_id, signer_id)
                # GUARD: if this ADAM already exists as an AUTHORED (manually
                # created/edited) act, the upsert above left its row untouched —
                # and we must NOT rebuild its child tables (line items, operators,
                # links) or re-extract text from the import payload either, or the
                # curator's work would be clobbered. Skip everything downstream.
                if repo.is_authored(adam):
                    if INGEST_JOB_ID:
                        repo.log_act(INGEST_JOB_ID, adam, act_type, act.get("title"),
                                     "skipped_authored", False, None, "authored")
                    continue
                ft_extracted, ft_chars, ft_note = False, None, None
                if EXTRACT_FULLTEXT:
                    # Fail-soft, fill-only-if-empty; scanned docs skipped.
                    ft_extracted, ft_chars, ft_note = extract_full_text_for_act(
                        client, repo, act_type, adam)
                else:
                    ft_note = "disabled"
                repo.replace_object_details(adam, act)
                repo.record_contractors(act_type, act)
                repo.record_links(act_type, act)
                repo.record_diavgeia_links(act)
                if INGEST_JOB_ID:
                    repo.log_act(INGEST_JOB_ID, adam, act_type, act.get("title"),
                                 action, ft_extracted, ft_chars, ft_note)
            # Mark done + commit the final batch. (Earlier batches in this window
            # were already flushed for live progress; the window is only marked
            # 'done' once every act in it succeeded, so a crash before this leaves
            # it 'running' and resume reprocesses it — idempotently.)
            db.execute(
                """UPDATE proc.ingest_window
                   SET status='done', finished_at=now()
                   WHERE act_type=%s AND date_from=%s AND date_to=%s""",
                (act_type, w_from, w_to))
            db.commit()
            summary["done"] += 1
            print(f"[{act_type}] window {w_from}..{w_to} done")
        except Exception as e:  # noqa: BLE001 — record and continue
            # Roll back the unflushed tail (work since the last progress commit),
            # then record the error in a separate transaction. Already-flushed
            # acts persist; the window is marked 'error' and reprocessed on the
            # next resume, where the idempotent upserts make re-running it safe.
            try:
                db.rollback()
            except Exception:
                pass
            err = f"{type(e).__name__}: {e}"[:1000]
            try:
                db.execute(
                    """UPDATE proc.ingest_window
                       SET status='error', finished_at=now(), last_error=%s
                       WHERE act_type=%s AND date_from=%s AND date_to=%s""",
                    (err, act_type, w_from, w_to))
                db.commit()
            except Exception:
                pass
            summary["errored"] += 1
            print(f"[{act_type}] window {w_from}..{w_to} ERROR: {err}")
            # Continue to the next window rather than aborting the whole run.

    return summary


def _done_windows(db, act_type: str):
    """Return list of (date_from, date_to) tuples already marked done."""
    db.cur.execute(
        """SELECT date_from, date_to FROM proc.ingest_window
           WHERE act_type=%s AND status='done'""", (act_type,))
    return [(r[0], r[1]) for r in db.cur.fetchall()]


def backfill_all(db, start: dt.date, end: dt.date, *, resume: bool = False):
    client = KhmdhsClient()
    repo = Repository(db)
    for act_type in ("request", "notice", "auction", "contract", "payment"):
        ingest_type(client, repo, act_type, start, end, resume=resume)


if __name__ == "__main__":
    # Example: backfill the last two years. Provide your own DB connection that
    # exposes .execute(sql, params), .execute_returning(sql, params)->id, .commit().
    raise SystemExit(
        "Wire up a DB connection (psycopg2) and call backfill_all(db, start, end)."
    )
