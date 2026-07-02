"""UI internationalisation (Greek default, English overlay).

Design: the templates are authored in Greek, and Greek is the *source* — also
the lookup key. `t("Διαχείριση Πράξεων")` returns the English string when the
active language is English and a translation exists, otherwise the Greek text
passes through unchanged. So:

  * Greek rendering is guaranteed identical to before (passthrough on miss).
  * English is an additive overlay — a missing key degrades to Greek, never to
    a blank or an error.

Only UI CHROME is translated here: buttons, headings, menus, form labels, fixed
enum labels (act type, contract/procedure type, award criteria). RECORD DATA —
act titles, authority/contractor names, CPV descriptions, full text, and the
DB-driven `code_list` values — is never translated; it stays in its source
language. That boundary is the whole point: we localise the app, not the data.

`lang` and the bound `t` callable are injected into every template render by a
context processor in main.py, so templates just call `{{ t("…") }}` / `{{ x|t }}`.
"""
from __future__ import annotations

SUPPORTED = ("el", "en")
DEFAULT_LANG = "el"


def normalize_lang(value: str | None) -> str:
    return value if value in SUPPORTED else DEFAULT_LANG


def lang_from_request(request) -> str:
    """Active language for this request: the `lang` cookie, else Greek."""
    try:
        return normalize_lang(request.cookies.get("lang"))
    except Exception:  # noqa: BLE001 — never let i18n break a page
        return DEFAULT_LANG


# --------------------------------------------------------------------------- #
# Fixed enum labels. Greek copies mirror the dicts in main.py (the authoritative
# Greek source); the English dicts are the overlay. Lookups go through
# enum_label(kind, code, lang) so the label filters can be language-aware.
# --------------------------------------------------------------------------- #
TYPE_LABELS_EN = {
    "notice":   "Notice",
    "auction":  "Award",
    "contract": "Contract",
    "payment":  "Payment order",
    "request":  "Primary request",
    "award":    "Award decision",
}
CONTRACT_TYPES_EN = {
    "9":  "Services",
    "10": "Works",
    "12": "Studies",
    "13": "Supplies",
    "14": "Technical or other related services",
}
PROCEDURE_TYPES_EN = {
    "1":  "Open procedure",
    "2":  "Restricted procedure",
    "4":  "Competitive dialogue",
    "6":  "Direct award",
    "7":  "Competitive procedure with negotiation",
    "11": "Innovation partnership",
    "12": "Negotiated procedure without prior publication",
    "13": "Negotiated procedure with prior call for competition (art. 266)",
    "18": "Procedure under article 128 of law 4412/16",
}
ASSIGN_CRITERIA_EN = {
    "1": "By cost — best price-quality ratio",
    "2": "By price",
    "3": "By cost — life-cycle costing",
    "4": "By price — other",
}
TYPE_ALL_LABEL_EN = "All acts"

# kind -> English code map, for a language-aware label filter.
_ENUM_EN = {
    "type": TYPE_LABELS_EN,
    "contract_type": CONTRACT_TYPES_EN,
    "procedure_type": PROCEDURE_TYPES_EN,
    "assign_criteria": ASSIGN_CRITERIA_EN,
}


def enum_label(kind: str, code, greek_map: dict, lang: str):
    """Resolve an enum code to a label in the active language. Falls back to the
    Greek label, then the raw code, then an em dash."""
    key = str(code) if code is not None else ""
    if lang == "en":
        en = _ENUM_EN.get(kind, {})
        if key in en:
            return en[key]
    if key in greek_map:
        return greek_map[key]
    return code or "—"


# --------------------------------------------------------------------------- #
# UI string catalog: Greek source -> English. Populated alongside the templates
# in i18n_catalog.py to keep this module focused on mechanics.
# --------------------------------------------------------------------------- #
try:
    from app.i18n_catalog import UI_EN
except ImportError:  # flat layout (run with --app-dir=app)
    from i18n_catalog import UI_EN

CATALOGS = {"en": UI_EN}


def translate(text, lang: str):
    """Translate a UI string. Passthrough for Greek / unknown keys / non-str."""
    if lang == "el" or not isinstance(text, str):
        return text
    return CATALOGS.get(lang, {}).get(text, text)
