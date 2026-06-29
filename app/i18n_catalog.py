# -*- coding: utf-8 -*-
"""Greek -> English UI string catalog. Keys are the exact Greek source strings
used in templates; values are the English overlay. A missing key falls back to
Greek (see i18n.translate), so partial coverage is always safe.

Organised by area only for readability — it's one flat dict at the end. Keep
keys verbatim (including punctuation/whitespace) so template lookups hit.
"""

# Masthead / global navigation (beta_base.html, base.html)
_NAV = {
    "Ελληνικές Δημόσιες Συμβάσεις · ": "Greek Public Contracts · ",
    "Εξερευνητής": "Explorer",
    "Διαφάνεια στις δημόσιες προμήθειες": "Transparency in public procurement",
    "Πράξεις": "Acts",
    "Αναθέτουσες": "Authorities",
    "Ανάδοχοι": "Contractors",
    "Σύνοψη": "Summary",
    "Στατιστικά": "Analytics",
    "Διαχείριση": "Administration",
    "KHMDHS · Εξερευνητής": "KHMDHS · Explorer",
    "Γλώσσα": "Language",
}

# Admin navigation tabs (_admin_tabs.html)
_ADMIN_TABS = {
    "Συλλογή Δεδομένων": "Data Collection",
    "Διαχείριση Πράξεων": "Act Management",
    "Επιμέλεια Σημειώσεων": "Notes Curation",
    "Ενοποίηση Αναδόχων": "Merge Contractors",
    "Ενοποίηση Αρχών": "Merge Authorities",
}

# Legacy base.html masthead (pre-redesign UI, kept as fallback)
_BASE_LEGACY = {
    "Ελληνικές Δημόσιες Συμβάσεις · Εξερευνητής": "Greek Public Contracts · Explorer",
    "Δημόσιες ": "Public ",
    "Συμβάσεις": "Contracts",
    "Αναλυτικά": "Analytics",
    "Αρχές": "Authorities",
    "Ένα αρχείο διαγωνισμών, αναθέσεων, συμβάσεων και πληρωμών του ελληνικού δημοσίου.":
        "An archive of Greek public-sector tenders, awards, contracts and payments.",
}

# Common verbs / buttons / chrome reused across many pages.
_COMMON = {
    "Αποθήκευση": "Save",
    "Άκυρο": "Cancel",
    "Επεξεργασία": "Edit",
    "Διαγραφή": "Delete",
    "Εύρεση": "Search",
    "Αναζήτηση": "Search",
    "καθαρισμός": "clear",
    "Λήψη": "Download",
    "Κλείσιμο": "Close",
    "Φόρτωση…": "Loading…",
    "Πίσω": "Back",
    "Ναι": "Yes",
    "Όχι": "No",
    "Όλα": "All",
    "Όλες": "All",
    "Όλοι": "All",
    "Τύπος": "Type",
    "Τίτλος": "Title",
    "Κατάσταση": "Status",
    "Ημερομηνία": "Date",
    "Ώρα": "Time",
    "Σφάλμα": "Error",
    "Ενέργεια": "Action",
    "Αποτέλεσμα": "Result",
    "Από": "From",
    "Έως": "To",
    "Ταξινόμηση": "Sort",
    "Πρόσφατες": "Most recent",
    "Παλαιότερες": "Oldest",
    "— όλες —": "— all —",
    "— όλοι —": "— all —",
    "— όλα —": "— all —",
}

# Search / landing page + result cards (beta_index.html, beta_results.html)
_SEARCH = {
    "Αναζήτηση σε τίτλους, αναθέτουσες, αναδόχους, ΑΔΑΜ…":
        "Search titles, authorities, contractors, ΑΔΑΜ…",
    "βρέθηκαν": "found",
    "πράξεις": "acts",
    "ανανέωση…": "refreshing…",
    "Φίλτρα": "Filters",
    "Τύπος πράξης": "Act type",
    "Αναθέτουσα αρχή": "Contracting authority",
    "Είδος σύμβασης": "Contract category",
    "Τύπος διαδικασίας": "Procedure type",
    "Γεωγραφία — Περιφέρεια": "Geography — Region",
    "Εκτιμώμενη αξία / προϋπολογισμός (€)": "Estimated value / budget (€)",
    "από": "from",
    "έως": "to",
    "Κατηγορία / Υποκατηγορία": "Category / Subcategory",
    "▸ όλη η κατηγορία": "▸ whole category",
    "προσθήκη CPV, π.χ. 331…": "add CPV, e.g. 331…",
    "Ημ/νία δημοσίευσης": "Publication date",
    "Καταληκτική ημ/νία (λήξη)": "Deadline (closing)",
    "συνολική αξία €": "total value €",
    "ανά σελίδα": "per page",
    "ταξινόμηση": "sort",
    "Νεότερες πρώτα": "Newest first",
    "Αξία ↓": "Value ↓",
    "Σχετικότητα": "Relevance",
    "δημ.": "pub.",
    "λήξη": "closing",
    "ακυρωμένη": "cancelled",
    "ορθή επ.": "corrected",
    "Διορθωμένη τιμή — αρχική: €": "Corrected value — original: €",
    "αξία σύμβασης": "contract value",
    "εκτ. προϋπολογισμός": "est. budget",
    "‹ προηγ.": "‹ prev.",
    "επόμ. ›": "next ›",
    "Καμία πράξη δεν αντιστοιχεί στα φίλτρα. Δοκιμάστε να χαλαρώσετε τα κριτήρια.":
        "No acts match the filters. Try relaxing the criteria.",
}

# Merge into one flat catalog. Later groups override earlier ones on key clash
# (there should be none — keep keys unique across groups).
UI_EN: dict[str, str] = {}
for _grp in (_NAV, _ADMIN_TABS, _BASE_LEGACY, _COMMON, _SEARCH):
    UI_EN.update(_grp)
