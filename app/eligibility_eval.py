"""
eligibility_eval.py — the customer's certificates against the checklist.

docs/specs/evaluation-layer.md. Tier 2, the checklist's last slice: the
"evaluation layer" of ai-summary §3/§14.

What it does
------------
A checklist item reads «Πιστοποιητικό ISO 9001:2015 ή ισοδύναμο». If the
customer declared an ISO 9001 we say so UNDER the item — the edition, and
whether it is still valid on the closing date:

    ✓ Στο προφίλ σας: ISO 9001:2015, ισχύει έως 12/03/2027.
    ⚠ Στο προφίλ σας: ISO 14001, λήγει 02/10/2026 — πριν την υποβολή.
    · ISO 27001: δεν έχει δηλωθεί στο προφίλ σας.

It SUGGESTS and never ticks (owner's decision, 2026-09-24). A tick means
"done for this bid" — the certified copy is in the envelope — which holding
a certificate is not; and a regex cannot prove an item that ALSO asks for
three years' experience is met. The tick box is untouched.

The two layers (ai-summary §3)
------------------------------
The summary is extraction, one row per act served to everyone. Certificates
are one customer's data. So the evaluation is computed at RENDER time and
joined onto tender_checklist.view(), exactly like the ticks: never written to
proc.act_ai_summary, never in a cache key, never in a prompt. ai_summary.py
never reads proc.company_certificate and this module never writes the
summary — both directions are test-enforced (tests/test_eligibility_eval.py).
No model call: the matcher below is regex over text we already hold.

The catalogue
-------------
CLOSED, like ai_summary.SECTIONS: the management-system certificates a FIRM
holds, measured on 5,000 recent notices (spec §2). Product standards (ISO
10993, 15223, 7376 — properties of the goods) are simply not in it, so they
are never matched. Two aliases come from real notices:

  * «ISO 13458» is not a standard; it is how 15 notices misspell 13485.
  * «OHSAS 18001» was replaced by ISO 45001; a 45001 holder answers a notice
    that still asks for OHSAS. Not the reverse — and OHSAS cannot be declared
    at all (every such certificate lapsed in 2021), so the one-way rule holds
    by construction.

Who must hold it
----------------
Medical tenders often ask that «ο κατασκευαστής» hold ISO 13485 while the
bidder is a distributor. A certificate is declared with holder 'self' (the
customer's firm) or 'manufacturer' (with the manufacturer's name — owner's
decision: optional). An item that names the manufacturer is answered from
the manufacturer rows; one that also names the bidder («ο προσφέρων ή ο
κατασκευαστής») from both.

Absence
-------
A scheme the item asks for that the customer has not declared gets a NEUTRAL
line («δεν έχει δηλωθεί στο προφίλ σας»), never a verdict — the profile may
just be incomplete (owner's decision). And only for a customer who has
declared at least one certificate: for everyone else the checklist is
byte-identical to what it was before this module existed (test-enforced).
"""
from __future__ import annotations

import datetime as dt
import re
import unicodedata

# --------------------------------------------------------------------------- #
# The catalogue: key, display name, ISO number (None = not an ISO number).
# The migration's CHECK constraint repeats the keys — change both together.
# --------------------------------------------------------------------------- #
CATALOGUE = (
    ("iso9001",  "ISO 9001",  "9001"),
    ("iso13485", "ISO 13485", "13485"),
    ("iso14001", "ISO 14001", "14001"),
    ("iso45001", "ISO 45001", "45001"),
    ("iso27001", "ISO/IEC 27001", "27001"),
    ("iso37001", "ISO 37001", "37001"),
    ("iso22000", "ISO 22000", "22000"),
    ("haccp",    "HACCP (ΕΛΟΤ 1416)", None),
    ("iso22301", "ISO 22301", "22301"),
    ("iso50001", "ISO 50001", "50001"),
    ("iso39001", "ISO 39001", "39001"),
)
SCHEMES = {k: name for k, name, _n in CATALOGUE}
_BY_NUMBER = {n: k for k, _name, n in CATALOGUE if n}
_BY_NUMBER["13458"] = "iso13485"          # the typo real notices carry

HOLDERS = ("self", "manufacturer")

NUMBER_MAX = 100
ISSUER_MAX = 200
MANUFACTURER_MAX = 200

# --------------------------------------------------------------------------- #
# Finding the schemes an item asks for
# --------------------------------------------------------------------------- #
# Greek capitals that look like Latin ones: notices type «ΙSΟ» with a Greek
# Ι and Ο more often than you would think, and «ΕΝ ISO» is Greek throughout.
_LOOKALIKE = str.maketrans("ΑΒΕΖΗΙΚΜΝΟΡΤΥΧΆΈΉΊΌΎ", "ABEZHIKMNOPTYXAEHIOY")

_ED = r"(?:\s*[:\-]\s*((?:19|20)\d\d)(?!\d))?"
_ISO_RE = re.compile(r"\bISO\s*(?:/\s*IEC\s*)?[-\s]?(\d{4,5})(?!\d)" + _ED)
# "ISO 9001:2015 και 14001:2015", "ISO 9001, 14001 & 45001": the number
# after a separator belongs to the same ISO mention.
_MORE_RE = re.compile(
    r"\s*(?:,|&|/|\+|KAI|AND|H|OR)\s*(?:ISO\s*(?:/\s*IEC\s*)?[-\s]?)?(\d{4,5})(?!\d)" + _ED)
_OHSAS_RE = re.compile(r"\bOHSAS\s*[-\s]?18001(?!\d)")
_HACCP_RE = re.compile(r"\bHACCP\b|\bEΛOT\s*1416(?!\d)")


def _norm(text: str) -> str:
    return (text or "").upper().translate(_LOOKALIKE)


def requested(text: str) -> list[dict]:
    """The catalogue schemes `text` names, in order, each once:
    [{"scheme", "edition"}] — edition is the year the text attaches to the
    number (':2015'), or None. A number outside the catalogue ends a list
    ("ISO 9001, 10993") and is never matched."""
    s = _norm(text)
    found: dict[str, str | None] = {}

    def add(scheme, edition):
        if scheme not in found or (edition and not found[scheme]):
            found[scheme] = edition

    for m in _ISO_RE.finditer(s):
        scheme = _BY_NUMBER.get(m.group(1))
        if scheme is None:
            continue
        add(scheme, m.group(2))
        pos = m.end()
        while True:
            more = _MORE_RE.match(s, pos)
            if not more or more.group(1) not in _BY_NUMBER:
                break
            add(_BY_NUMBER[more.group(1)], more.group(2))
            pos = more.end()
    if _OHSAS_RE.search(s):
        add("iso45001", None)
    if _HACCP_RE.search(s):
        add("haccp", None)
    return [{"scheme": k, "edition": v} for k, v in found.items()]


def _fold(text: str) -> str:
    """Lower case, no accents, final sigma folded — for the phrase lists."""
    s = unicodedata.normalize("NFD", (text or "").lower())
    return "".join(ch for ch in s if unicodedata.category(ch) != "Mn").replace("ς", "σ")


_MANUFACTURER = ("κατασκευαστ", "οικοσ κατασκευ", "manufacturer")
_BIDDER = ("προσφερ", "οικονομικοσ φορεα", "οικονομικου φορεα", "αναδοχ",
           "υποψηφι", "διαγωνιζομεν", "συμμετεχ", "bidder", "tenderer")


def holders_for(text: str) -> tuple[str, ...]:
    """Whose certificate the item asks for: the bidder's ('self'), the
    manufacturer's, or — when it names both — either."""
    f = _fold(text)
    if not any(p in f for p in _MANUFACTURER):
        return ("self",)
    if any(p in f for p in _BIDDER):
        return ("self", "manufacturer")
    return ("manufacturer",)


# --------------------------------------------------------------------------- #
# The evaluation itself — pure, no DB
# --------------------------------------------------------------------------- #
def _status(cert: dict, closing: dt.date | None, today: dt.date) -> str:
    until = cert.get("valid_until")
    if until is None:
        return "no_date"
    if until < today:
        return "expired"
    if closing is not None and until < closing:
        return "expires_before"
    return "ok"


WARN = ("expired", "expires_before")


def evaluate(text: str, certs: list[dict], *, closing: dt.date | None,
             today: dt.date) -> list[dict]:
    """Notes for one checklist item: one per declared certificate that
    answers a scheme the item names, or one neutral 'undeclared' note per
    scheme nobody declared.

    note = {"scheme", "name", "edition" (asked), "status", "holder",
            "manufacturer", "cert" (the row or None), "edition_differs"}
    status: ok | expires_before | expired | no_date | undeclared.
    `closing` is the act's closing date (Athens); None → validity is judged
    against today only.
    """
    notes = []
    wanted = requested(text)
    if not wanted:
        return notes
    roles = holders_for(text)
    for req in wanted:
        scheme, asked = req["scheme"], req["edition"]
        held = [c for c in certs
                if c["scheme"] == scheme and c["holder"] in roles]
        if not held:
            notes.append({"scheme": scheme, "name": SCHEMES[scheme],
                          "edition": asked, "status": "undeclared",
                          "holder": "manufacturer" if roles == ("manufacturer",) else "self",
                          "manufacturer": None, "cert": None,
                          "edition_differs": False})
            continue
        # The firm's own first, then manufacturers by name.
        held.sort(key=lambda c: (c["holder"] != "self", (c.get("manufacturer") or "").lower()))
        for cert in held:
            notes.append({"scheme": scheme, "name": SCHEMES[scheme],
                          "edition": asked,
                          "status": _status(cert, closing, today),
                          "holder": cert["holder"],
                          "manufacturer": cert.get("manufacturer"),
                          "cert": cert,
                          "edition_differs": bool(asked and cert.get("edition")
                                                  and cert["edition"] != asked)})
    return notes


def item_text(item: dict) -> str:
    return " ".join(str(item.get(k) or "") for k in ("label", "value", "quote"))


def annotate(groups: list[dict], certs: list[dict], *,
             closing: dt.date | None, today: dt.date) -> int:
    """Put item["certs"] on every checklist item that names a catalogue
    scheme. Returns how many notes are warnings (expired / lapses before the
    closing date). Does nothing — not even an empty list — when the customer
    declared no certificates: their checklist stays exactly as it was."""
    if not certs:
        return 0
    n_warn = 0
    for g in groups:
        for item in g["items"]:
            notes = evaluate(item_text(item), certs, closing=closing, today=today)
            if notes:
                item["certs"] = notes
                n_warn += sum(1 for n in notes if n["status"] in WARN)
    return n_warn


def warnings(groups: list[dict], certs: list[dict], *,
             closing: dt.date | None, today: dt.date) -> list[str]:
    """Display names of the certificates that lapse before `closing` (or
    already have), each once — the favourites card's one-line warning."""
    out = []
    if not certs:
        return out
    for g in groups:
        for item in g["items"]:
            for n in evaluate(item_text(item), certs, closing=closing, today=today):
                label = n["name"] + (f" ({n['manufacturer']})" if n["manufacturer"] else "")
                if n["status"] in WARN and label not in out:
                    out.append(label)
    return out


def note_text(n: dict, t=lambda s: s) -> str:
    """One note as plain text — the spreadsheet's cell. Same wording as
    _cert_notes.html; `t` is the translator."""
    if n["status"] == "undeclared":
        who = f" ({t('κατασκευαστή')})" if n["holder"] == "manufacturer" else ""
        return f"· {n['name']}{who}: {t('δεν έχει δηλωθεί στο προφίλ σας.')}"
    cert = n["cert"]
    who = (f"{t('Κατασκευαστής')} {n['manufacturer']}" if n["manufacturer"]
           else t("Στο προφίλ σας"))
    name = n["name"] + (f":{cert['edition']}" if cert.get("edition") else "")
    until = cert["valid_until"].strftime("%d/%m/%Y") if cert.get("valid_until") else ""
    if n["status"] == "ok":
        text = f"✓ {who}: {name}, {t('ισχύει έως')} {until}."
    elif n["status"] == "expires_before":
        text = f"⚠ {who}: {name}, {t('λήγει')} {until} — {t('πριν την υποβολή')}."
    elif n["status"] == "expired":
        text = f"⚠ {who}: {name}, {t('έληξε στις')} {until}."
    else:
        text = f"· {who}: {name}, {t('χωρίς δηλωμένη ημερομηνία λήξης.')}"
    if n["edition_differs"]:
        text += f" ({t('η προκήρυξη αναφέρει έκδοση')} {n['edition']})"
    return text


def closing_date(deadlines: list[dict]) -> dt.date | None:
    """The act's own closing date from tender_checklist.build()'s deadline
    set — the 'record' entry, already on the Athens calendar."""
    for d in deadlines:
        if d.get("source") == "record":
            return d.get("date")
    return None


# --------------------------------------------------------------------------- #
# Database
# --------------------------------------------------------------------------- #
class CertError(ValueError):
    """A declaration that cannot be stored; the message is shown (Greek, t())."""


def certificates(c, user_id) -> list[dict]:
    c.execute("""SELECT id, scheme, holder, manufacturer, edition, number,
                        issuer, valid_until, source, updated_at
                   FROM proc.company_certificate
                  WHERE user_id = %s
                  ORDER BY holder, lower(coalesce(manufacturer, '')), scheme""",
              (user_id,))
    rows = [dict(r) for r in c.fetchall()]
    order = {k: i for i, (k, _n, _x) in enumerate(CATALOGUE)}
    rows.sort(key=lambda r: (r["holder"] != "self",
                             (r["manufacturer"] or "").lower(),
                             order.get(r["scheme"], 99)))
    for r in rows:
        r["name"] = SCHEMES.get(r["scheme"], r["scheme"])
    return rows


def _clean(text, limit: int) -> str | None:
    text = re.sub(r"\s+", " ", (text or "")).strip()
    if len(text) > limit:
        raise CertError("Το κείμενο είναι πολύ μεγάλο.")
    return text or None


def save(c, user_id, *, scheme: str, holder: str = "self",
         manufacturer: str = "", edition: str = "", number: str = "",
         issuer: str = "", valid_until: str = "", by=None,
         source: str = "admin") -> int:
    """Insert a certificate, or update the one already declared for the same
    scheme and holder (a renewal is re-entering it). Returns the row id."""
    if scheme not in SCHEMES:
        raise CertError("Άγνωστο πρότυπο.")
    if holder not in HOLDERS:
        raise CertError("Άγνωστος κάτοχος.")
    manufacturer = _clean(manufacturer, MANUFACTURER_MAX)
    if holder == "manufacturer" and not manufacturer:
        raise CertError("Συμπληρώστε το όνομα του κατασκευαστή.")
    if holder == "self":
        manufacturer = None
    edition = (edition or "").strip() or None
    if edition and not re.fullmatch(r"(?:19|20)\d\d", edition):
        raise CertError("Η έκδοση είναι έτος, π.χ. 2015.")
    until = None
    if (valid_until or "").strip():
        try:
            until = dt.date.fromisoformat(valid_until.strip())
        except ValueError:
            raise CertError("Μη έγκυρη ημερομηνία λήξης.") from None
    c.execute("""INSERT INTO proc.company_certificate
                   (user_id, scheme, holder, manufacturer, edition, number,
                    issuer, valid_until, source, created_by, updated_by)
                 VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                 ON CONFLICT (user_id, scheme, holder, lower(coalesce(manufacturer, '')))
                 DO UPDATE SET manufacturer = EXCLUDED.manufacturer,
                               edition = EXCLUDED.edition,
                               number = EXCLUDED.number,
                               issuer = EXCLUDED.issuer,
                               valid_until = EXCLUDED.valid_until,
                               source = EXCLUDED.source,
                               updated_at = now(),
                               updated_by = EXCLUDED.updated_by
                 RETURNING id""",
              (user_id, scheme, holder, manufacturer, edition,
               _clean(number, NUMBER_MAX), _clean(issuer, ISSUER_MAX), until,
               source, by, by))
    return c.fetchone()["id"]


def delete(c, user_id, cert_id: int) -> bool:
    c.execute("DELETE FROM proc.company_certificate WHERE id = %s AND user_id = %s",
              (cert_id, user_id))
    return c.rowcount > 0
