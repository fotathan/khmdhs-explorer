"""
leads.py — create CRM Prospective Leads directly from the Contractor Database.

A prospective lead is a NON-LOGIN customer account: a proc.app_user (role=customer,
random password so it can't log in) carrying customer_profile.crm_stage='prospective'
plus the mapped contractor data and lead metadata (service, round-robin manager,
creation_source='OrgDB', a link back to the economic_operator). Contacts live in
proc.customer_contact (main + inactive).

Two-phase import (the router in app/crm.py drives it):
  1. map each selected operator + detect duplicates → bucket clean / conflict /
     hard-blocked (see PDF §5-6);
  2. execute the curator's per-row decisions (create / update / skip).

Pure helpers here; the router owns the HTTP + session. Portable duplicate
detection: exact identifiers + accent/case/final-sigma FOLDED name equality
(pg_trgm's schema placement differs across envs, so no trigram dependency).
"""
from __future__ import annotations

import re
import secrets
import uuid

try:
    from app import auth
except ImportError:                       # pragma: no cover - script/pkg dual import
    import auth

DEFAULT_COUNTRY = "GR"
GENERATED_EMAIL_DOMAIN = "prospective.com"
MAX_BATCH = 1000

# --------------------------------------------------------------------------- #
# small helpers
# --------------------------------------------------------------------------- #
def _s(v) -> str:
    return (v or "").strip() if isinstance(v, str) else ("" if v is None else str(v).strip())


def _fold_sql(col: str) -> str:
    """SQL that folds a text expression for portable, accent/case/final-sigma
    insensitive equality (mirrors app.interconnect / act-parties)."""
    return f"translate(lower(proc.f_unaccent({col})), 'ς', 'σ')"


def _split_name(s: str) -> tuple[str, str]:
    """A contact person string → (first, last). Placeholder when empty (PDF §3)."""
    s = _s(s)
    if not s:
        return "FirstName", "LastName"
    parts = s.split()
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], " ".join(parts[1:])


# --------------------------------------------------------------------------- #
# mapping: economic_operator (+ gemi fallback + act_contractor contacts) → lead
# --------------------------------------------------------------------------- #
def _gemi(c, afm):
    if not afm:
        return None
    c.execute("""SELECT legal_name, trade_title, ar_gemi, street, street_number,
                        zip_code, city, email, phone
                 FROM proc.gemi_enrichment WHERE afm = %s
                 ORDER BY fetched_at DESC LIMIT 1""", (afm,))
    return c.fetchone()


def _operator_extra_contacts(c, operator_id, primary_email):
    """Additional distinct contacts for this operator, gathered from its per-act
    contractor rows (proc.act_contractor). These import as INACTIVE contacts."""
    c.execute("""SELECT DISTINCT contact_person, email, phone
                 FROM proc.act_contractor
                 WHERE operator_id = %s
                   AND (nullif(btrim(coalesce(contact_person,'')),'') IS NOT NULL
                        OR nullif(btrim(coalesce(email,'')),'') IS NOT NULL)""",
              (operator_id,))
    seen = {(_s(primary_email).lower())}
    out = []
    for r in c.fetchall():
        em = _s(r.get("email")).lower()
        key = em or _s(r.get("contact_person")).lower()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append({"name": _s(r.get("contact_person")),
                    "email": _s(r.get("email")) or None,
                    "phone": _s(r.get("phone")) or None})
    return out


def map_operator(c, op: dict) -> dict:
    """Map one economic_operator row → the customer/lead payload, filling blanks
    from ΓΕΜΗ enrichment and collecting extra contacts from act_contractor."""
    d = {
        "operator_id": op["operator_id"],
        "orgdb_id": _s(op.get("orgdb_id")),
        "company": _s(op.get("name")),
        "vat_number": _s(op.get("vat_number")),
        "tax_number": _s(op.get("statistical_or_tax_number")),
        "reg_number": _s(op.get("ar_gemi")),
        "country": _s(op.get("country")),
        "city": _s(op.get("city")),
        "postal_code": _s(op.get("postal_code")),
        "address": _s(op.get("street_address")),
        "contact_person": _s(op.get("contact_person")),
        "contact_email": _s(op.get("contact_email")),
        "contact_phone": _s(op.get("contact_phone")),
    }
    g = _gemi(c, d["vat_number"]) if d["vat_number"] else None
    if g:
        d["company"] = d["company"] or _s(g.get("legal_name")) or _s(g.get("trade_title"))
        d["reg_number"] = d["reg_number"] or _s(g.get("ar_gemi"))
        d["city"] = d["city"] or _s(g.get("city"))
        d["postal_code"] = d["postal_code"] or _s(g.get("zip_code"))
        d["address"] = d["address"] or " ".join(
            x for x in [_s(g.get("street")), _s(g.get("street_number"))] if x)
        d["contact_email"] = d["contact_email"] or _s(g.get("email"))
        d["contact_phone"] = d["contact_phone"] or _s(g.get("phone"))
    d["extra_contacts"] = _operator_extra_contacts(c, op["operator_id"], d["contact_email"])
    return d


def build_contacts(lead: dict) -> list[dict]:
    """Contacts to create: the operator's contact as the MAIN (active), extras as
    INACTIVE. None → a placeholder FirstName/LastName main contact (PDF §3)."""
    fn, ln = _split_name(lead.get("contact_person"))
    contacts = [{
        "first_name": fn, "last_name": ln,
        "email": lead.get("contact_email") or None,
        "phone": lead.get("contact_phone") or None,
        "is_main": True, "is_active": True,
    }]
    for ec in lead.get("extra_contacts", []):
        efn, eln = _split_name(ec.get("name"))
        contacts.append({
            "first_name": efn, "last_name": eln,
            "email": ec.get("email"), "phone": ec.get("phone"),
            "is_main": False, "is_active": False,
        })
    return contacts


# --------------------------------------------------------------------------- #
# duplicate detection (PDF §5)
# --------------------------------------------------------------------------- #
def is_freemail(c, domain: str) -> bool:
    domain = _s(domain).lower()
    if not domain:
        return False
    c.execute("SELECT 1 FROM proc.crm_freemail_domain WHERE domain = %s", (domain,))
    return c.fetchone() is not None


def _customer_label(row) -> str:
    return _s(row.get("company")) or _s(row.get("full_name")) or _s(row.get("username")) or f"ID {row['id']}"


def _cust_by_email(c, email):
    c.execute("""SELECT u.id, u.username, u.email, p.company, p.full_name
                 FROM proc.app_user u LEFT JOIN proc.customer_profile p ON p.user_id = u.id
                 WHERE u.role='customer' AND lower(u.email) = lower(%s) LIMIT 1""", (email,))
    return c.fetchone()


def _cust_by_ids(c, vat, reg, tax):
    ids = [(v) for v in (vat, reg, tax) if _s(v)]
    if not ids:
        return None
    c.execute("""SELECT u.id, u.username, u.email, p.company, p.full_name
                 FROM proc.customer_profile p JOIN proc.app_user u ON u.id = p.user_id
                 WHERE u.role='customer' AND (
                       (%(vat)s <> '' AND p.vat_number = %(vat)s)
                    OR (%(reg)s <> '' AND p.reg_number = %(reg)s)
                    OR (%(tax)s <> '' AND p.tax_number = %(tax)s))
                 LIMIT 1""", {"vat": _s(vat), "reg": _s(reg), "tax": _s(tax)})
    return c.fetchone()


def _cust_by_domain(c, domain):
    c.execute("""SELECT u.id, u.username, u.email, p.company, p.full_name
                 FROM proc.app_user u LEFT JOIN proc.customer_profile p ON p.user_id = u.id
                 WHERE u.role='customer' AND u.email IS NOT NULL
                   AND lower(split_part(u.email,'@',2)) = lower(%s) LIMIT 1""", (domain,))
    return c.fetchone()


def _cust_by_name(c, company):
    if not _s(company):
        return None
    c.execute(f"""SELECT u.id, u.username, u.email, p.company, p.full_name
                  FROM proc.customer_profile p JOIN proc.app_user u ON u.id = p.user_id
                  WHERE u.role='customer' AND {_fold_sql('p.company')} = {_fold_sql('%s')}
                  LIMIT 1""", (company,))
    return c.fetchone()


def detect_conflict(c, lead: dict) -> dict:
    """Classify a lead vs existing customers. Precedence (strongest first):
    strong_id (ΑΦΜ/ΓΕΜΗ/tax → hard block, no create) > exact_email > email_domain
    (non-freemail) > name_soft (folded-equal company; soft) > clean."""
    email = _s(lead.get("contact_email"))
    # strong id — hard block
    row = _cust_by_ids(c, lead.get("vat_number"), lead.get("reg_number"), lead.get("tax_number"))
    if row:
        return {"kind": "strong_id", "existing_uid": row["id"],
                "existing_label": _customer_label(row), "allowed": ["update", "skip"],
                "bucket": "blocked"}
    # exact email
    if email:
        row = _cust_by_email(c, email)
        if row:
            return {"kind": "exact_email", "existing_uid": row["id"],
                    "existing_label": _customer_label(row),
                    "allowed": ["update", "create_new_email", "skip"], "bucket": "conflict"}
    # email domain (non-freemail)
    if email and "@" in email:
        dom = email.split("@", 1)[1]
        if dom and not is_freemail(c, dom):
            row = _cust_by_domain(c, dom)
            if row:
                return {"kind": "email_domain", "existing_uid": row["id"],
                        "existing_label": _customer_label(row),
                        "allowed": ["update", "create", "skip"], "bucket": "conflict"}
    # soft name
    row = _cust_by_name(c, lead.get("company"))
    if row:
        return {"kind": "name_soft", "existing_uid": row["id"],
                "existing_label": _customer_label(row),
                "allowed": ["create", "update", "skip"], "bucket": "conflict", "soft": True}
    return {"kind": "clean", "existing_uid": None, "existing_label": None,
            "allowed": ["create"], "bucket": "clean"}


# --------------------------------------------------------------------------- #
# create / update
# --------------------------------------------------------------------------- #
def round_robin_manager(c):
    """The admin with the fewest assigned customers (stable tie-break by id).
    Called per-lead so a batch distributes across admins (same-txn visibility)."""
    admins = auth.list_admins(c)
    if not admins:
        return None
    c.execute("""SELECT manager_id, count(*) AS n FROM proc.customer_profile
                 WHERE manager_id IS NOT NULL GROUP BY manager_id""")
    counts = {r["manager_id"]: r["n"] for r in c.fetchall()}
    return min(admins, key=lambda a: (counts.get(a["id"], 0), a["id"]))["id"]


def _unique_username(c, base: str) -> str:
    base = re.sub(r"[^A-Za-z0-9._@-]", "-", base)[:36].strip("-") or "lead"
    cand, n = base, 1
    while True:
        c.execute("SELECT 1 FROM proc.app_user WHERE lower(username) = lower(%s)", (cand,))
        if not c.fetchone():
            return cand
        n += 1
        cand = f"{base}-{n}"


_PROFILE_COLS = ("full_name", "phone", "company", "vat_number", "tax_number",
                 "reg_number", "country", "city", "postal_code", "address",
                 "lead_source", "crm_stage", "service", "manager_id",
                 "creation_source", "operator_id", "orgdb_id", "is_recipient")


def _upsert_profile(c, uid, values: dict, by=None):
    """Plain upsert of the (extended) customer_profile — every _PROFILE_COLS value
    is written as given. Callers that want fill-only semantics merge with the
    existing row first (see update_existing)."""
    cols = ["user_id"] + list(_PROFILE_COLS) + ["updated_by"]
    vals = [uid] + [values.get(k) for k in _PROFILE_COLS] + [by]
    set_parts = [f"{k} = EXCLUDED.{k}" for k in _PROFILE_COLS]
    set_parts += ["updated_by = EXCLUDED.updated_by", "updated_at = now()"]
    c.execute(
        f"INSERT INTO proc.customer_profile ({', '.join(cols)}) "
        f"VALUES ({', '.join(['%s'] * len(cols))}) "
        f"ON CONFLICT (user_id) DO UPDATE SET {', '.join(set_parts)}",
        vals)


def _insert_contacts(c, uid, contacts, skip_emails=None):
    skip = {(_s(e).lower()) for e in (skip_emails or []) if _s(e)}
    ord_ = 0
    for ct in contacts:
        em = _s(ct.get("email")).lower()
        if em and em in skip:
            continue
        c.execute("""INSERT INTO proc.customer_contact
                       (user_id, ord, first_name, last_name, email, phone, mobile,
                        job_title, is_main, is_active, is_recipient)
                     VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,false)""",
                  (uid, ord_, ct.get("first_name"), ct.get("last_name"),
                   ct.get("email"), ct.get("phone"), ct.get("mobile"),
                   ct.get("job_title"), ct.get("is_main", False),
                   ct.get("is_active", True)))
        ord_ += 1


def create_lead(c, lead: dict, by=None, override_email: str | None = None) -> int:
    """Create a prospective lead as a non-login customer account. If the operator
    has no email (and none overridden), generate {customerID}@prospective.com."""
    base = "lead-" + (_s(lead.get("orgdb_id")) or str(lead.get("operator_id") or "")
                      or uuid.uuid4().hex[:8])
    username = _unique_username(c, base)
    password = secrets.token_urlsafe(24)          # random → account can't be logged into
    email = _s(override_email) or _s(lead.get("contact_email")) or None
    urow = auth.create_user(c, username, password, role="customer", email=email)
    uid = urow["id"]
    if not email:
        gen = f"{uid}@{GENERATED_EMAIL_DOMAIN}"
        c.execute("UPDATE proc.app_user SET email = %s WHERE id = %s", (gen, uid))

    contacts = build_contacts(lead)
    main = contacts[0]
    full_name = f"{_s(main['first_name'])} {_s(main['last_name'])}".strip()
    _upsert_profile(c, uid, {
        "full_name": full_name,
        "phone": lead.get("contact_phone") or None,
        "company": lead.get("company") or None,
        "vat_number": lead.get("vat_number") or None,
        "tax_number": lead.get("tax_number") or None,
        "reg_number": lead.get("reg_number") or None,
        "country": lead.get("country") or DEFAULT_COUNTRY,
        "city": lead.get("city") or None,
        "postal_code": lead.get("postal_code") or None,
        "address": lead.get("address") or None,
        "lead_source": "OrgDB",
        "crm_stage": "prospective",
        "service": "TAS",
        "manager_id": round_robin_manager(c),
        "creation_source": "OrgDB",
        "operator_id": lead.get("operator_id"),
        "orgdb_id": lead.get("orgdb_id") or None,
        "is_recipient": False,
    }, by=by)
    _insert_contacts(c, uid, contacts)
    return uid


def update_existing(c, uid: int, lead: dict, by=None) -> int:
    """Fill-only-if-empty update of a matched customer + append any new contacts
    (by email) that aren't already present. Never overwrites curator data or the
    customer's existing lead metadata (crm_stage/service/manager stay as-is)."""
    c.execute("SELECT * FROM proc.customer_profile WHERE user_id = %s", (uid,))
    cur = dict(c.fetchone() or {})
    merged = {k: cur.get(k) for k in _PROFILE_COLS}   # start from existing values
    for col, new in (("company", lead.get("company")),
                     ("phone", lead.get("contact_phone")),
                     ("vat_number", lead.get("vat_number")),
                     ("tax_number", lead.get("tax_number")),
                     ("reg_number", lead.get("reg_number")),
                     ("country", lead.get("country")),
                     ("city", lead.get("city")),
                     ("postal_code", lead.get("postal_code")),
                     ("address", lead.get("address")),
                     ("operator_id", lead.get("operator_id")),
                     ("orgdb_id", lead.get("orgdb_id"))):
        old = merged.get(col)
        if isinstance(old, str):
            old = old.strip()
        if old in (None, "") and _s(new):
            merged[col] = new
    _upsert_profile(c, uid, merged, by=by)
    c.execute("SELECT lower(email) AS e FROM proc.customer_contact WHERE user_id=%s AND email IS NOT NULL",
              (uid,))
    have = {r["e"] for r in c.fetchall()}
    fresh = [ct for ct in build_contacts(lead)
             if not ct.get("is_main") and _s(ct.get("email")).lower() not in have]
    for ct in fresh:
        ct["is_active"] = False
    _insert_contacts(c, uid, fresh)
    return uid
