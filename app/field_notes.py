"""Fixed explanations next to an act's coded fields.

Slice 1 of docs/specs/public-detail-pages.md. A coded field — the act type,
the procedure, the contract type, the award criterion — is shown with one or
two sentences saying what that value MEANS, in the reader's language, and a
link to the glossary term where one exists.

Content, not UI strings: like glossary.py it carries both languages itself,
so it is not in i18n_catalog. Drafted 2026-10-09, to be checked by the owner
(§6 of the spec lists the texts to read first).

Rules every text here keeps:
  - it explains the value, never what this particular authority did;
  - it never repeats a figure the page already shows;
  - a legal statement is only what the law's text supports — anything
    uncertain is left out, not guessed.

Which notes a GATED visitor sees is the template's decision, not this
module's: the act type is public (its badge is), the others follow their
field and are subscriber-only.
"""
from __future__ import annotations

import re
import unicodedata


def _n(el: str, en: str, glossary: str | None = None) -> dict:
    return {"el": el, "en": en, "glossary": glossary}


# --------------------------------------------------------------------------- #
# Act type — procurement_act.type (every key of main.TYPE_LABELS). PUBLIC.
# --------------------------------------------------------------------------- #
ACT_TYPES = {
    "notice": _n(
        "Πρόκειται για προκήρυξη: ο φορέας ανακοινώνει δημόσια ότι θα αναθέσει "
        "μια σύμβαση και καλεί τους ενδιαφερόμενους οικονομικούς φορείς να "
        "υποβάλουν προσφορά μέσα στην προθεσμία. Οι πλήρεις όροι βρίσκονται στη "
        "διακήρυξη.",
        "This is a contract notice: the authority publicly announces that it "
        "will award a contract and invites interested businesses to submit a "
        "tender before the deadline. The full terms are in the tender documents.",
        "prokiryxi"),
    "auction": _n(
        "Πρόκειται για απόφαση ανάθεσης (κατακύρωση): ο φορέας ορίζει σε ποιον "
        "ανατίθεται η σύμβαση και με ποιο τίμημα. Συνήθως ακολουθεί η υπογραφή "
        "της σύμβασης.",
        "This is an award decision: the authority states who is awarded the "
        "contract and at what price. The signed contract usually follows.",
        "apotelesma"),
    "award": _n(
        "Πρόκειται για απόφαση ανάθεσης δημοσιευμένη στη Διαύγεια: ο φορέας "
        "αποφασίζει σε ποιον ανατίθεται μια σύμβαση και με ποιο ποσό.",
        "This is an award decision published on Diavgeia: the authority decides "
        "who is awarded a contract and for what amount.",
        "apotelesma"),
    "contract": _n(
        "Πρόκειται για υπογεγραμμένη δημόσια σύμβαση μεταξύ του φορέα και του "
        "αναδόχου. Ορίζει το τελικό τίμημα, τη διάρκεια και τους όρους "
        "εκτέλεσης.",
        "This is a signed public contract between the authority and the "
        "contractor. It sets the final price, the duration and the terms of "
        "performance.",
        "symvasi"),
    "payment": _n(
        "Πρόκειται για εντολή πληρωμής: η πράξη με την οποία ο φορέας εγκρίνει "
        "την καταβολή χρημάτων στον ανάδοχο. Για κάθε πληρωμή εκδίδεται χωριστή "
        "πράξη.",
        "This is a payment order: the act by which the authority approves a "
        "payment to the contractor. A separate act is issued for each payment.",
        "entalma"),
    "request": _n(
        "Πρόκειται για πρωτογενές αίτημα: η υπηρεσία καταγράφει μια ανάγκη και "
        "την εκτιμώμενη δαπάνη της, πριν ξεκινήσει οποιαδήποτε διαδικασία "
        "ανάθεσης. Είναι συχνά η πρώτη ένδειξη ότι κάτι πρόκειται να "
        "προκηρυχθεί.",
        "This is a primary request: a department records a need and its "
        "estimated cost before any award procedure starts. It is often the "
        "first sign that something is about to be tendered.",
        "protogenes-aitima"),
    "prior_info": _n(
        "Πρόκειται για ανακοίνωση με προηγούμενες πληροφορίες για μια διαδικασία "
        "ανάθεσης, όπως δημοσιεύτηκε στην πηγή της. Δεν είναι η ίδια η "
        "πρόσκληση υποβολής προσφορών.",
        "This is an announcement carrying prior information about an award "
        "procedure, as published by its source. It is not itself the call for "
        "tenders."),
}


# --------------------------------------------------------------------------- #
# Procedure — procurement_act.procedure_family, the normalised label from
# proc.compute_procedure_family() (procedure_family_migration.sql). The raw
# procedure_type_code has ~25 spellings; the family has one per procedure.
# «Άλλο / Άγνωστο» deliberately has no note. Subscriber-only.
# --------------------------------------------------------------------------- #
PROCEDURES = {
    "Ανοιχτή διαδικασία": _n(
        "Ανοικτή διαδικασία: κάθε ενδιαφερόμενος οικονομικός φορέας που πληροί "
        "τα κριτήρια μπορεί να υποβάλει προσφορά, χωρίς στάδιο προεπιλογής.",
        "Open procedure: any interested business that meets the criteria may "
        "submit a tender; there is no pre-selection stage.",
        "anoikti-diadikasia"),
    "Κλειστή διαδικασία": _n(
        "Κλειστή διαδικασία: οι ενδιαφερόμενοι υποβάλλουν πρώτα αίτηση "
        "συμμετοχής και μόνο όσοι επιλεγούν καλούνται να υποβάλουν προσφορά.",
        "Restricted procedure: interested businesses first request to take part, "
        "and only those selected are invited to submit a tender.",
        "kleisti-diadikasia"),
    "Απευθείας ανάθεση": _n(
        "Απευθείας ανάθεση: η διαδικασία για συμβάσεις μικρής αξίας, κάτω από "
        "το όριο του άρθρου 118 του ν. 4412/2016. Δεν γίνεται διαγωνισμός· ο "
        "φορέας μπορεί όμως να ζητήσει προσφορές και, για ορισμένες αξίες, "
        "δημοσιεύει πρώτα πρόσκληση στο ΚΗΜΔΗΣ.",
        "Direct award: the procedure for low-value contracts, below the limit "
        "set by article 118 of Law 4412/2016. There is no competition, but the "
        "authority may ask for offers and, for some values, first publishes an "
        "invitation on KIMDIS.",
        "apeftheias-anathesi"),
    "Συνοπτικός διαγωνισμός": _n(
        "Συνοπτικός διαγωνισμός: απλουστευμένος διαγωνισμός για συμβάσεις κάτω "
        "των ορίων (άρθρο 117 του ν. 4412/2016, όπως ίσχυε). Εμφανίζεται σε "
        "παλαιότερες πράξεις· καταργήθηκε με τον ν. 4782/2021.",
        "Simplified tender: a lighter competitive procedure for contracts below "
        "the thresholds (article 117 of Law 4412/2016, as it then stood). It "
        "appears in older acts; Law 4782/2021 abolished it.",
        "katofli"),
    "Διαπραγμάτευση χωρίς προηγούμενη δημοσίευση": _n(
        "Διαπραγμάτευση χωρίς προηγούμενη δημοσίευση: ο φορέας διαπραγματεύεται "
        "απευθείας με έναν ή λίγους οικονομικούς φορείς, χωρίς δημόσια "
        "πρόσκληση. Επιτρέπεται μόνο στις περιπτώσεις που απαριθμεί ο νόμος, "
        "π.χ. κατεπείγουσα ανάγκη ή άγονος προηγούμενος διαγωνισμός.",
        "Negotiated procedure without prior publication: the authority "
        "negotiates directly with one or a few businesses, with no public call. "
        "It is allowed only on the grounds the law lists, e.g. extreme urgency "
        "or an earlier tender that drew no suitable offers.",
        "diapragmatefsi"),
    "Διαπραγμάτευση με προηγούμενη προκήρυξη": _n(
        "Διαπραγμάτευση με προηγούμενη προκήρυξη διαγωνισμού: η διαδικασία "
        "ανοίγει με δημόσια πρόσκληση και ο φορέας διαπραγματεύεται τους όρους "
        "με τους υποψηφίους που επιλέγει. Προβλέπεται για φορείς κοινής "
        "ωφέλειας — ενέργεια, ύδρευση, μεταφορές, ταχυδρομεία (άρθρο 266 του "
        "ν. 4412/2016).",
        "Negotiated procedure with a prior call for competition: the procedure "
        "opens with a public call, and the authority negotiates the terms with "
        "the candidates it selects. It is provided for utilities — energy, "
        "water, transport, postal services (article 266 of Law 4412/2016)."),
    "Ανταγωνιστική διαδικασία με διαπραγμάτευση": _n(
        "Ανταγωνιστική διαδικασία με διαπραγμάτευση: οι υποψήφιοι που "
        "επιλέγονται υποβάλλουν αρχικές προσφορές, τις οποίες ο φορέας "
        "διαπραγματεύεται μαζί τους πριν ζητήσει τις τελικές. Χρησιμοποιείται "
        "όταν οι ανάγκες δεν καλύπτονται χωρίς προσαρμογή διαθέσιμων λύσεων.",
        "Competitive procedure with negotiation: the selected candidates submit "
        "initial tenders, which the authority negotiates with them before asking "
        "for final ones. It is used when needs cannot be met without adapting "
        "solutions already available."),
    "Ανταγωνιστικός διάλογος": _n(
        "Ανταγωνιστικός διάλογος: ο φορέας συζητά με επιλεγμένους υποψηφίους "
        "για να διαμορφώσει τη λύση που χρειάζεται και μετά τους καλεί να "
        "υποβάλουν τελικές προσφορές. Προορίζεται για σύνθετες συμβάσεις, όπου "
        "η λύση δεν μπορεί να οριστεί εκ των προτέρων.",
        "Competitive dialogue: the authority discusses with selected candidates "
        "to shape the solution it needs, then invites them to submit final "
        "tenders. It is meant for complex contracts where the solution cannot "
        "be defined in advance."),
    "Σύμπραξη καινοτομίας": _n(
        "Σύμπραξη καινοτομίας: ο φορέας αναζητεί την ανάπτυξη καινοτόμου "
        "προϊόντος, υπηρεσίας ή έργου που δεν διατίθεται στην αγορά, και την "
        "αγορά του στη συνέχεια, με έναν ή περισσότερους εταίρους.",
        "Innovation partnership: the authority seeks the development of an "
        "innovative product, service or work not available on the market, and "
        "its later purchase, with one or more partners."),
    # Today's article 128 is about directly awarding specialist consultants on
    # very large works; the acts carrying this label are small purchases
    # (median €6,200 over ~40,000 acts, 2023-2026). So the note says only what
    # was declared and what the data shows. Spec §6.
    "Διαδικασία άρθρου 128": _n(
        "Ο φορέας δήλωσε στο ΚΗΜΔΗΣ «Διαδικασία άρθρου 128 του ν. 4412/2016». "
        "Στα δεδομένα μας η επιλογή αυτή αφορά κυρίως αγορές μικρής αξίας· οι "
        "όροι της συγκεκριμένης ανάθεσης βρίσκονται στο έγγραφο της πράξης.",
        "The authority declared «procedure under article 128 of Law 4412/2016» "
        "on KIMDIS. In our data this option is used mostly for low-value "
        "purchases; the terms of this award are in the act's own document."),
    "Διαδικασία κάτω των ορίων εκτός ν.4412/2016": _n(
        "Διαδικασία για σύμβαση κάτω των ορίων που δεν διέπεται από τον ν. "
        "4412/2016 αλλά από άλλους κανόνες — για παράδειγμα τον κανονισμό "
        "προμηθειών του ίδιου του φορέα.",
        "A procedure for a below-threshold contract that is governed not by Law "
        "4412/2016 but by other rules — for example the authority's own "
        "procurement regulation.",
        "katofli"),
}


# --------------------------------------------------------------------------- #
# Contract type — contract_type_code (every key of main.CONTRACT_TYPES).
# Subscriber-only.
# --------------------------------------------------------------------------- #
CONTRACT_TYPES = {
    "13": _n(
        "Σύμβαση προμήθειας: αφορά την αγορά, μίσθωση ή χρηματοδοτική μίσθωση "
        "αγαθών — προϊόντων, εξοπλισμού, υλικών.",
        "A supply contract: the purchase, lease or hire-purchase of goods — "
        "products, equipment, materials."),
    "9": _n(
        "Σύμβαση υπηρεσιών: ο ανάδοχος παρέχει μια υπηρεσία — π.χ. "
        "καθαριότητα, φύλαξη, συντήρηση, πληροφορική — και όχι αγαθά ή "
        "κατασκευή.",
        "A services contract: the contractor provides a service — e.g. "
        "cleaning, security, maintenance, IT — rather than goods or "
        "construction."),
    "10": _n(
        "Σύμβαση έργου: αφορά την κατασκευή, ανακαίνιση ή επισκευή τεχνικού "
        "έργου — κτιρίων, δρόμων, δικτύων.",
        "A works contract: the construction, renovation or repair of a "
        "structure — buildings, roads, networks."),
    "12": _n(
        "Σύμβαση μελέτης: αφορά την εκπόνηση τεχνικών μελετών — αρχιτεκτονικών, "
        "στατικών, ηλεκτρομηχανολογικών κ.ά. — συνήθως πριν από την κατασκευή "
        "ενός έργου.",
        "A design-study contract: the preparation of technical studies — "
        "architectural, structural, electromechanical and others — usually "
        "before a work is built."),
    "14": _n(
        "Σύμβαση τεχνικών ή συναφών επιστημονικών υπηρεσιών: υπηρεσίες "
        "μηχανικών και ειδικών που συνδέονται με μελέτες και έργα — π.χ. "
        "επίβλεψη, τοπογραφικές αποτυπώσεις, γεωτεχνικές έρευνες, υποστήριξη "
        "του φορέα.",
        "A technical or related scientific services contract: engineering and "
        "specialist services linked to studies and works — e.g. supervision, "
        "surveying, geotechnical investigation, support to the authority."),
}


# --------------------------------------------------------------------------- #
# Award criterion. Notices and award decisions carry criteria_code (1-4);
# contracts carry assign_criteria_label, whose CODE column holds the contract
# type instead (measured) — so contracts are matched on the folded label.
# Subscriber-only.
# --------------------------------------------------------------------------- #
CRITERIA = {
    "price": _n(
        "Κριτήριο βάσει τιμής: κερδίζει η χαμηλότερη αποδεκτή προσφορά, αρκεί "
        "να πληροί τις απαιτήσεις της διακήρυξης.",
        "Price only: the lowest acceptable tender wins, provided it meets the "
        "requirements of the tender documents.",
        "kritirio-anathesis"),
    "quality_price": _n(
        "Βέλτιστη σχέση ποιότητας–τιμής: οι προσφορές βαθμολογούνται σε "
        "ποιοτικά κριτήρια και στην τιμή, με τις βαρύτητες που ορίζει η "
        "διακήρυξη· δεν κερδίζει απαραίτητα η φθηνότερη.",
        "Best price-quality ratio: tenders are scored on quality criteria and on "
        "price, with the weightings set in the tender documents; the cheapest "
        "does not necessarily win.",
        "kritirio-anathesis"),
    "life_cycle": _n(
        "Κοστολόγηση κύκλου ζωής: συγκρίνεται το συνολικό κόστος σε όλη τη "
        "διάρκεια ζωής — αγορά, λειτουργία, συντήρηση, απόσυρση — και όχι μόνο "
        "η τιμή αγοράς.",
        "Life-cycle costing: tenders are compared on the total cost over the "
        "whole life — purchase, operation, maintenance, disposal — not only on "
        "the purchase price.",
        "kritirio-anathesis"),
    "other": _n(
        "Η προσφορά επιλέγεται με βάση την τιμή ή το κόστος, με τρόπο που "
        "ορίζει ειδικά η διακήρυξη. Τα ακριβή κριτήρια βρίσκονται στο έγγραφο "
        "της πράξης.",
        "The tender is chosen on price or cost, in a way the tender documents "
        "set out specifically. The exact criteria are in the act's own "
        "document.",
        "kritirio-anathesis"),
}

# proc.code_list 'criteria' / main.ASSIGN_CRITERIA.
CRITERIA_CODES = {"1": "quality_price", "2": "price", "3": "life_cycle",
                  "4": "other"}


def _fold(s: str) -> str:
    """Case, accents and dash style do not decide a match."""
    s = unicodedata.normalize("NFD", s or "")
    s = "".join(ch for ch in s if unicodedata.category(ch) != "Mn").lower()
    s = re.sub(r"[\u2010-\u2015\u2212-]", "-", s)
    return re.sub(r"\s+", " ", s).strip()


# Every assign_criteria_label measured on contracts (2026-10-09), plus the
# code-list spellings so a label that matches the list also resolves.
CRITERIA_LABELS = {_fold(k): v for k, v in {
    "Βάσει τιμής": "price",
    "Βάσει κόστους – βέλτιστη σχέση ποιότητας – τιμής": "quality_price",
    "Βάσει κόστους – βέλτιστη σχέση ποιότητας τιμής": "quality_price",
    "Βάσει κόστους – κοστολόγηση κύκλου ζωής": "life_cycle",
    "Βάσει κόστους – άλλο": "other",
    "Βάσει τιμής – άλλο": "other",
}.items()}


KINDS = ("act_type", "procedure", "contract_type", "criteria")


def _entry(kind: str, value) -> dict | None:
    if value is None or value == "":
        return None
    key = str(value).strip()
    if kind == "act_type":
        return ACT_TYPES.get(key)
    if kind == "procedure":
        return PROCEDURES.get(key)
    if kind == "contract_type":
        return CONTRACT_TYPES.get(key)
    if kind == "criteria":
        slug = CRITERIA_CODES.get(key) or CRITERIA_LABELS.get(_fold(key))
        return CRITERIA.get(slug) if slug else None
    raise ValueError(f"unknown field-note kind: {kind}")


def note(kind: str, value, lang: str = "el") -> dict | None:
    """The explanation of one field value, or None when there is none.

    Returns {"text": ..., "href": "/glossary/<slug>" or None}."""
    e = _entry(kind, value)
    if e is None:
        return None
    text = e["en"] if lang == "en" else e["el"]
    href = f"/glossary/{e['glossary']}" if e.get("glossary") else None
    return {"text": text, "href": href}


def all_entries() -> list[tuple[str, str, dict]]:
    """(kind, key, entry) for every text — what the tests walk."""
    out = []
    for kind, table in (("act_type", ACT_TYPES), ("procedure", PROCEDURES),
                        ("contract_type", CONTRACT_TYPES), ("criteria", CRITERIA)):
        out += [(kind, k, e) for k, e in table.items()]
    return out
