"""call_script.py — the first-call sales script. docs/specs/call-script.md.

A VIEW over crm_brief.build() plus a few signals about the account. It does no
arithmetic of its own: every number it puts in a salesperson's mouth is the
number the brief shows, read from the same dict.

Two inputs pick the words:

  * origin — how the contact reached us: 'self' (signed up on /register),
    'contractor_db' (imported from the contractor database, leads.py) or
    'external' (anything else: created by an admin, a list, a referral).
  * basis  — how much we know, straight from the brief: history /
    history+kad / kad / none.

The truth rules (§5): the award ledger is SAID, the ΚΑΔ estimate is only ever
ASKED, the ΑΦΜ typed at sign-up is only CONFIRMED. Every block carries its
kind and its source so a test can hold that line. Account activity chooses a
branch but is never spoken — "I saw you logged in twice" is surveillance.

No model, no stored script: built per request, like the brief. The only
writes are the call results (save_result) and the do-not-call flag.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

try:
    from app import auth as _auth
    from app import crm_brief as _brief
    from app import leads as _leads
    from app import onboarding as _onboarding
    from app.call_script_text import BRANDS, SCRIPT_VERSION, TEXT
except ImportError:                       # pragma: no cover - run with --app-dir=app
    import auth as _auth
    import crm_brief as _brief
    import leads as _leads
    import onboarding as _onboarding
    from call_script_text import BRANDS, SCRIPT_VERSION, TEXT

ATHENS = ZoneInfo("Europe/Athens")

ORIGINS = ("self", "contractor_db", "external")
BASES = ("history", "history+kad", "kad", "none")
HOOKS = ("H1", "H2", "H3", "H4", "H5")
TRIAL_SOON_DAYS = 3        # "your trial ends on …" is said only this close
GEMI_ACTIVE_ID = 3         # the registry's 'Ενεργή'
TITLE_MAX = 140            # a tender title read aloud; longer ones are cut at a word

# (code, label, the customer_call status it is logged with). The labels are
# what the Activity tab shows in `outcome`, so they stay Greek like the rest
# of the CRM record; the CHECK in migration …20261008145739 repeats the codes.
RESULTS = (
    ("no_answer", "Δεν απάντησε", "not_answered"),
    ("wrong_person", "Λάθος αριθμός / όχι αρμόδιος", "held"),
    ("callback", "Ξανακαλέστε", "held"),
    ("not_interested", "Δεν ενδιαφέρεται", "held"),
    ("send_material", "Θέλει υλικό με email", "held"),
    ("demo_booked", "Κλείστηκε παρουσίαση", "held"),
    ("trial_started", "Ξεκίνησε δοκιμή", "held"),
    ("do_not_call", "Να μην ξανακληθεί", "held"),
)
RESULT_CODES = tuple(code for code, _, _ in RESULTS)
RESULT_LABELS = {code: label for code, label, _ in RESULTS}
RESULT_STATUS = {code: status for code, _, status in RESULTS}
# Results that come with a date: they open a task on that date.
TASK_SUBJECTS = {"callback": "Επανάκληση (σενάριο πρώτης κλήσης)",
                 "demo_booked": "Παρουσίαση πλατφόρμας"}
CALL_SUBJECT = "Πρώτη κλήση (σενάριο)"
NOTE_MAX = 1000

BRANCH_RE = re.compile(
    r"^(self|contractor_db|external)\|(history|history\+kad|kad|none)\|H[1-5]$")
TOKEN = re.compile(r"\[\[([a-z_]+)\]\]")


# --------------------------------------------------------------------------- #
# Small pure helpers
# --------------------------------------------------------------------------- #
def fill(text: str, values: dict) -> list[tuple[str, bool]] | None:
    """`text` with its [[tokens]] replaced, as (piece, is_value) segments so
    the page can show the facts in bold. None when ANY token has no value:
    the caller then offers another text, never a sentence with a hole."""
    segs, pos = [], 0
    for m in TOKEN.finditer(text):
        value = values.get(m.group(1))
        if value is None or not str(value).strip():
            return None
        if m.start() > pos:
            segs.append((text[pos:m.start()], False))
        segs.append((str(value), True))
        pos = m.end()
    if pos < len(text):
        segs.append((text[pos:], False))
    return segs


def plain(segs) -> str:
    return "".join(piece for piece, _ in segs or [])


def _day(ts) -> date | None:
    if ts is None:
        return None
    if isinstance(ts, datetime):
        return _brief._aware(ts).astimezone(ATHENS).date()
    return ts


def _fmt_date(ts) -> str | None:
    d = _day(ts)
    return d.strftime("%d/%m/%Y") if d else None


def _fmt_int(n, lang: str) -> str | None:
    if n is None:
        return None
    s = f"{int(n):,}"
    return s.replace(",", ".") if lang == "el" else s


def _fmt_eur(v, lang: str) -> str | None:
    """Same rounding as the brief's eur() macro: whole euros."""
    if not v:
        return None
    s = f"{float(v):,.0f}"
    return f"{s.replace(',', '.')} €" if lang == "el" else f"€{s}"


def _lower_first(s: str | None) -> str | None:
    s = (s or "").strip()
    return s[:1].lower() + s[1:] if s else None


def _sentence(s: str | None) -> str | None:
    """ΚΑΔ descriptions arrive in capitals; read aloud they need sentence
    case. str.lower() handles the final sigma (ΕΙΔΩΝ ΙΑΤΡΙΚΗΣ → ειδών…)."""
    s = (s or "").strip()
    return s[:1].upper() + s[1:].lower() if s else None


def _short(s: str | None, n: int = TITLE_MAX) -> str | None:
    """A title to read aloud: one line, its own quotes dropped (the script
    puts it in «» already), cut at a word."""
    s = " ".join((s or "").split()).strip("«»\"'“”„ ")
    if len(s) <= n:
        return s or None
    cut = s[:n].rsplit(" ", 1)[0]
    return cut.rstrip(" ,.;:-–—") + "…"


def origin_of(creation_source: str | None) -> str:
    src = (creation_source or "").strip()
    if src == "OrgDB":
        return "contractor_db"
    if src == "register":
        return "self"
    return "external"


# --------------------------------------------------------------------------- #
# Signals: what the account itself tells us (read-only)
# --------------------------------------------------------------------------- #
def signals(c, uid: int) -> dict:
    cust = _auth.get_customer(c, uid) or {}
    c.execute("""SELECT creation_source, lead_source, full_name, company, phone,
                        mobile, tender_experience, do_not_call_at
                   FROM proc.customer_profile WHERE user_id = %s""", (uid,))
    p = c.fetchone() or {}
    c.execute("""SELECT first_name, last_name, phone, mobile
                   FROM proc.customer_contact
                  WHERE user_id = %s AND is_active
                  ORDER BY is_main DESC, ord, id""", (uid,))
    contacts = c.fetchall()
    main = contacts[0] if contacts else {}
    name = " ".join(x.strip() for x in (main.get("first_name"), main.get("last_name"))
                    if x and x.strip())
    sub = _auth.current_subscription(c, uid)
    c.execute("""SELECT 1 FROM proc.digest_subscription
                  WHERE user_id = %s AND is_active LIMIT 1""", (uid,))
    has_alert = c.fetchone() is not None
    c.execute("""SELECT coalesce(started_at, scheduled_at, created_at) AS at, outcome
                   FROM proc.customer_call
                  WHERE user_id = %s AND status = 'held'
                  ORDER BY coalesce(started_at, scheduled_at, created_at) DESC
                  LIMIT 1""", (uid,))
    prior = c.fetchone()
    email = (cust.get("email") or "").lower()
    return {
        "origin": origin_of(p.get("creation_source")),
        "status": cust.get("status"),
        "contact": name or (p.get("full_name") or "").strip() or None,
        "company": (p.get("company") or "").strip() or None,
        "registered_on": cust.get("created_at"),
        "trial_ends": (sub["expires_at"]
                       if sub and sub["product_code"] == "test" else None),
        "has_phone": bool(p.get("phone") or p.get("mobile") or any(
            ct.get("phone") or ct.get("mobile") for ct in contacts)),
        "has_alert": has_alert,
        "tender_experience": p.get("tender_experience"),
        "lead_source": (p.get("lead_source") or "").strip() or None,
        "dnc_at": p.get("do_not_call_at"),
        "prior": dict(prior) if prior else None,
        # A claim (docs/specs/onboarding-wizard.md §5): only ever CONFIRMED.
        "declared_afm": _onboarding.declared_afm(c, uid),
        "generated_email": email.endswith("@" + _leads.GENERATED_EMAIL_DOMAIN),
    }


# --------------------------------------------------------------------------- #
# The script
# --------------------------------------------------------------------------- #
class _Blocks:
    """Builds blocks in one language against one set of values."""

    def __init__(self, lang: str, values: dict):
        self.tx = TEXT[lang]
        self.values = values

    def blk(self, kind: str, *keys: str, src: str | None = None, answers=None):
        """One paragraph from the texts of `keys`. None when any text has a
        token without a value — the branch logic then picks another."""
        segs = []
        for key in keys:
            part = fill(self.tx[key], self.values)
            if part is None:
                return None
            if segs:
                segs.append((" ", False))
            segs.extend(part)
        return {"kind": kind, "keys": list(keys), "src": src, "segs": segs,
                "answers": [a for a in (answers or []) if a]}

    def ans(self, label_key: str, *then):
        return {"label": self.tx[label_key], "then": [b for b in then if b]}

    def note(self, key: str):
        return self.blk("note", key)


def _estimate_facts(brief: dict) -> tuple[str | None, str | None]:
    """(ΚΑΔ label, the estimate's strongest CPV group) — only from a usable
    estimate. Both are needed to ask H4."""
    est = brief.get("est")
    if est is None or not getattr(est, "usable", False):
        return None, None
    pk = next((k for k in brief.get("kads") or [] if k.get("primary")), None)
    areas = est.areas or []
    return (_sentence(pk.get("descr")) if pk else None,
            _lower_first(areas[0].get("label")) if areas else None)


def pick_hook(brief: dict, now: datetime) -> tuple[str, dict | None]:
    """The first available of H1…H5 (§7.2). H1–H3 need the award history."""
    h = brief.get("history")
    if h:
        soon = now + timedelta(days=_brief.SOON_DAYS)
        for r in h.get("top") or []:
            d = _brief._aware(r.get("final_submission_date"))
            if (r["score"] >= _brief.GOOD_SCORE and d and now < d <= soon
                    and r.get("title") and r.get("authority_name")):
                return "H1", r
        if (h.get("headline") or {}).get("n_good"):
            return "H2", None
        if (h.get("buyers") or [{}])[0].get("authority_name"):
            return "H3", None
    if all(_estimate_facts(brief)):
        return "H4", None
    return "H5", None


def _values(brief, sig, lang, brand, top) -> dict:
    h = brief.get("history") or {}
    hl = h.get("headline") or {}
    kad_hl = (brief.get("kad") or {}).get("headline") or {}
    groups = h.get("groups") or []
    buyers = h.get("buyers") or []
    kad_label, est_group = _estimate_facts(brief)
    gemi = brief.get("gemi") or {}
    prior = sig.get("prior") or {}
    return {
        "agent": TEXT[lang]["agent_placeholder"],
        "brand": brand,
        "contact": sig.get("contact"),
        # The name a person says: the registry's trade title when there is
        # one («ΣΗΜΑ»), not the legal name («ΣΗΜΑ ΜΟΝΟΠΡΟΣΩΠΗ ΑΝΩΝΥΜΗ …»).
        "company": (brief.get("trade_title") or brief.get("company")
                    or sig.get("company")),
        "registered_on": _fmt_date(sig.get("registered_on")),
        "trial_ends": _fmt_date(sig.get("trial_ends")),
        "top_group": _lower_first(groups[0].get("label")) if groups else None,
        "n_good": _fmt_int(hl.get("n_good"), lang),
        "value_good": _fmt_eur(hl.get("value_good"), lang),
        "n_soon": _fmt_int(hl.get("n_soon"), lang),
        "top_buyer": buyers[0].get("authority_name") if buyers else None,
        "top_title": _short((top or {}).get("title")),
        "top_authority": (top or {}).get("authority_name"),
        "top_deadline": _fmt_date((top or {}).get("final_submission_date")),
        "kad_label": kad_label,
        "est_group": est_group,
        "n_good_kad": _fmt_int(kad_hl.get("n_good"), lang),
        "lead_source": sig.get("lead_source"),
        "dnc_date": _fmt_date(sig.get("dnc_at")),
        "gemi_status": gemi.get("status"),
        "prior_date": _fmt_date(prior.get("at")),
        "prior_outcome": (prior.get("outcome") or "").strip() or None,
    }


def _trial_soon(sig, today: date) -> bool:
    ends = _day(sig.get("trial_ends"))
    return bool(ends and today <= ends <= today + timedelta(days=TRIAL_SOON_DAYS))


def build(brief: dict, sig: dict, *, lang: str = "el", brand: int = 0,
          now: datetime | None = None) -> dict:
    """The whole script for one customer. Pure: `brief` is crm_brief.build(),
    `sig` is signals() — tests pass dicts."""
    lang = lang if lang in TEXT else "el"
    brand = brand if 0 <= brand < len(BRANDS) else 0
    now = now or datetime.now(timezone.utc)
    today = now.astimezone(ATHENS).date()
    origin = sig["origin"] if sig.get("origin") in ORIGINS else "external"
    basis = brief.get("basis") if brief.get("basis") in BASES else "none"
    cold = origin != "self"
    status = sig.get("status")
    hook, top = pick_hook(brief, now)
    values = _values(brief, sig, lang, BRANDS[brand], top)
    b = _Blocks(lang, values)
    tx = TEXT[lang]

    # ---- banners --------------------------------------------------------
    stops = []
    if status == "subscriber":
        stops.append(fill(tx["s_subscriber"], values))
    if sig.get("dnc_at"):
        stops.append(fill(tx["s_dnc"], values))
    gemi = brief.get("gemi") or {}
    if gemi.get("status_id") is not None and gemi["status_id"] != GEMI_ACTIVE_ID:
        stops.append(fill(tx["s_gemi"], values))
    warnings = []
    if cold:
        warnings.append(fill(tx["w_register"], values))
    if not sig.get("has_phone"):
        warnings.append(fill(tx["w_nophone"], values))
    if sig.get("prior"):
        warnings.append(fill(tx["w_prior"], values) or fill(tx["w_prior_plain"], values))
    if status == "expired_subscriber":
        warnings.append(fill(tx["w_expired_sub"], values))
    if sig.get("generated_email"):
        warnings.append(fill(tx["w_generated_email"], values))

    out = {"lang": lang, "brand": brand, "brands": BRANDS, "version": SCRIPT_VERSION,
           "origin": origin, "basis": basis, "hook": hook,
           "branch": f"{origin}|{basis}|{hook}", "tx": tx,
           "stops": [s for s in stops if s], "warnings": [w for w in warnings if w],
           "parts": [], "objections": [], "donts": []}
    if out["stops"]:
        out["blocked"] = True
        return out
    out["blocked"] = False

    # ---- 1. opener ------------------------------------------------------
    if origin == "self":
        greet = "greet_contact" if values["contact"] else "greet_unknown"
    elif values["company"]:
        greet = "greet_company"
    else:
        greet = "greet_contact" if values["contact"] else "greet_unknown"
    opener = [b.blk("say", "intro", greet)]
    if origin == "self":
        keys = ("body_self", "trial_soon") if _trial_soon(sig, today) else ("body_self",)
        opener.append(b.blk("say", *keys) or b.blk("say", "body_self"))
        if sig.get("declared_afm") and not brief.get("afm"):
            opener.append(b.blk("confirm", "confirm_afm", src="claim"))
            opener.append(b.note("confirm_afm_note"))
    elif origin == "contractor_db":
        opener.append(b.blk("say", "body_contractor", src="history")
                      or b.blk("say", "body_contractor_plain"))
    else:
        opener.append(b.blk("ask", "body_external", answers=[
            b.ans("ans_yes", b.blk("say", "ext_yes")),
            b.ans("ans_no", b.note("ext_no_note")),
            b.ans("ext_wrong_label", b.blk("ask", "ext_wrong"), b.note("ext_wrong_note")),
        ]))

    # ---- 2. hook --------------------------------------------------------
    def h4_block():
        n = (brief.get("kad") or {}).get("headline", {}).get("n_good") or 0
        yes = "h4_yes_many" if n > 1 else "h4_yes_one" if n == 1 else "h4_yes_none"
        return b.blk("ask", "h4", src="kad", answers=[
            b.ans("ans_yes", b.blk("say", yes, src="kad")),
            b.ans("ans_no", b.blk("ask", "h4_no"), b.note("h4_no_note")),
        ])

    if hook == "H1":
        hook_blocks = [b.blk("say", "h1", src="history")]
    elif hook == "H2":
        hl = brief["history"]["headline"]
        one = hl["n_good"] == 1
        keys = ["h2_one" if one else "h2_many"]
        if values["value_good"]:
            keys.append("h2_value_one" if one else "h2_value_many")
        if hl.get("n_soon"):
            keys.append("h2_soon_only" if one else
                        "h2_soon_one" if hl["n_soon"] == 1 else "h2_soon_many")
        hook_blocks = [b.blk("say", *keys, src="history")]
    elif hook == "H3":
        hook_blocks = [b.blk("say", "h3", src="history")]
    elif hook == "H4":
        hook_blocks = [h4_block()]
    else:
        hook_blocks = [b.blk("ask", "h5")]

    # ---- 3. discovery ---------------------------------------------------
    disc = []
    if basis == "history+kad" and hook != "H4" and all(_estimate_facts(brief)):
        disc.append(h4_block())
    disc.append(b.blk("ask", "q1", answers=[
        b.ans("q1_self", b.blk("say", "p_search")),
        b.ans("q1_other", b.note("see_o1_note")),
        b.ans("q1_consultant", b.blk("say", "p_consultant")),
        b.ans("q1_none", b.blk("say", "p_alert")),
    ]))
    disc.append(b.blk("ask", "q2", answers=[
        b.ans("ans_yes", b.blk("say", "p_deadline")),
        b.ans("ans_no", b.note("next_note")),
    ]))
    if brief.get("history"):
        disc.append(b.blk("ask", "q3", answers=[
            b.ans("ans_yes", b.blk("say", "p_fit")),
            b.ans("ans_no", b.note("next_note")),
        ]))
    if basis in ("history+kad", "kad"):
        disc.append(b.blk("ask", "q4", answers=[
            b.ans("q4_one", b.blk("say", "p_checklist")),
            b.ans("q4_more", b.blk("say", "p_team")),
        ]))
    if basis == "none" or sig.get("tender_experience") is False or origin == "external":
        disc.append(b.blk("ask", "q5", answers=[
            b.ans("q5_start", b.blk("say", "p_start")),
            b.ans("q5_yes", b.blk("say", "p_alert")),
            b.ans("q5_no", b.blk("say", "close_polite"), b.note("close_polite_note")),
        ]))

    # ---- 4. close -------------------------------------------------------
    entitled = status in _auth.ENTITLED_STATUSES
    close = [b.blk("ask", "c1"), b.note("c1_note")]
    if origin == "self":
        if _trial_soon(sig, today) or status == "expired_tester":
            close += [b.blk("say", "c3"), b.note("c3_note")]
    elif not entitled:
        close += [b.blk("ask", "c2"), b.note("c2_note")]
    if entitled and not sig.get("has_alert"):
        close += [b.blk("ask", "c4"), b.note("c4_note")]

    out["parts"] = [
        {"key": "opener", "title": tx["h_opener"], "blocks": [x for x in opener if x]},
        {"key": "hook", "title": tx["h_hook"], "blocks": [x for x in hook_blocks if x]},
        {"key": "discovery", "title": tx["h_discovery"], "blocks": [x for x in disc if x]},
        {"key": "close", "title": tx["h_close"], "blocks": [x for x in close if x]},
    ]

    # ---- objections -----------------------------------------------------
    if origin == "self":
        o5 = [b.blk("say", "o5_self")]
    elif origin == "contractor_db":
        o5 = [b.blk("say", "o5_contractor")]
    else:
        o5 = [b.blk("say", "o5_external")
              or b.blk("say", "o5_external_unknown"),
              None if values["lead_source"] else b.note("o5_external_unknown_note")]
    o5.append(b.blk("say", "o5_optout"))
    objections = [
        ("o1_q", [b.blk("say", "o1"), b.note("o1_note")]),
        ("o2_q", [b.blk("ask", "o2"), b.note("o2_note")]),
        ("o3_q", [b.blk("ask", "o3"), b.note("o3_note")]),
        ("o4_q", [b.blk("say", "o4"), b.note("o4_note")]),
        ("o5_q", o5),
        ("o6_q", [b.blk("say", "o6"), b.note("o6_note")]),
    ]
    out["objections"] = [{"q": tx[q], "blocks": [x for x in blocks if x]}
                         for q, blocks in objections]

    donts = ["d_price", "d_activity", "d_address"]
    if cold:
        donts += ["d_competitors", "d_watching"]
    if basis in ("kad", "history+kad"):
        donts.append("d_estimate")
    out["donts"] = [tx[k] for k in donts]
    return out


def walk(script: dict):
    """Every block of a script, answers included — what the tests and the
    spoken-text checks iterate over."""
    def _from(blocks):
        for blk in blocks:
            yield blk
            for ans in blk.get("answers") or []:
                yield from _from(ans["then"])
    for part in script.get("parts") or []:
        yield from _from(part["blocks"])
    for obj in script.get("objections") or []:
        yield from _from(obj["blocks"])


# --------------------------------------------------------------------------- #
# Writes: the call result and the do-not-call flag
# --------------------------------------------------------------------------- #
def _parse_due(raw: str | None) -> datetime | None:
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        d = datetime.fromisoformat(raw)
    except ValueError:
        raise ValueError("invalid date") from None
    return d if d.tzinfo else d.replace(tzinfo=ATHENS)


def save_result(c, uid: int, *, code: str, branch: str, version: str,
                note: str | None = None, due_at: str | None = None,
                admin_id: int | None = None) -> int:
    """Log one call made from the script: a customer_call row carrying the
    FROZEN branch (the brief changes daily), a task when the result has a
    date, and the do-not-call flag when that was the answer. One transaction.
    Returns the call id."""
    if code not in RESULT_CODES:
        raise ValueError("unknown result")
    if not BRANCH_RE.match(branch or ""):
        raise ValueError("unknown branch")
    if version != SCRIPT_VERSION:
        raise ValueError("stale script")
    note = " ".join((note or "").split())[:NOTE_MAX] or None
    due = _parse_due(due_at)
    outcome = RESULT_LABELS[code] + (f" — {note}" if note else "")
    with c.connection.transaction():
        c.execute("""INSERT INTO proc.customer_call
                       (user_id, subject, direction, status, outcome, assigned_to,
                        created_by, script_version, script_branch, script_result)
                     VALUES (%s, %s, 'outgoing', %s, %s, %s, %s, %s, %s, %s)
                     RETURNING id""",
                  (uid, CALL_SUBJECT, RESULT_STATUS[code], outcome, admin_id,
                   admin_id, version, branch, code))
        call_id = c.fetchone()["id"]
        if code in TASK_SUBJECTS and due:
            _auth.add_task(c, uid, subject=TASK_SUBJECTS[code], body=note,
                           status="open", due_at=due, outcome=None,
                           assigned_to=admin_id, created_by=admin_id)
        if code == "do_not_call":
            c.execute("""INSERT INTO proc.customer_profile
                           (user_id, do_not_call_at, do_not_call_by, updated_at)
                         VALUES (%s, now(), %s, now())
                         ON CONFLICT (user_id) DO UPDATE
                           SET do_not_call_at = coalesce(proc.customer_profile.do_not_call_at, now()),
                               do_not_call_by = coalesce(proc.customer_profile.do_not_call_by,
                                                         EXCLUDED.do_not_call_by)""",
                      (uid, admin_id))
    return call_id


def clear_do_not_call(c, uid: int) -> None:
    """Admin-only (the route is under /admin): the customer agreed to calls again."""
    c.execute("""UPDATE proc.customer_profile
                    SET do_not_call_at = NULL, do_not_call_by = NULL, updated_at = now()
                  WHERE user_id = %s""", (uid,))
