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
    "Βοήθεια": "Help",
    "Αρχείο ενεργειών": "Audit log",
    "Αρχείο ενεργειών διαχείρισης": "Admin audit log",
    "Οι πιο πρόσφατες ενέργειες που αλλάζουν δεδομένα — ποιος, τι, πότε και από πού.":
        "The most recent state-changing actions — who, what, when and from where.",
    "Χρήστης": "User",
    "Μέθοδος": "Method",
    "Διαδρομή": "Path",
    "Καμία ενέργεια ακόμη.": "No actions yet.",
    "Το": "The",
    "(Αρχείο ενεργειών) καταγράφει κάθε ενέργεια που αλλάζει δεδομένα — ποιος, τι, πότε, από ποια IP και με ποιο αποτέλεσμα — συμπεριλαμβανομένων των απορριφθεισών προσπαθειών.":
        "(Audit log) records every state-changing action — who, what, when, from which IP and with what outcome — including rejected attempts.",
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
    "φιλτράρισμα κατηγοριών…": "filter categories…",
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

# Act detail page (beta_act.html)
_ACT = {
    "‹ πίσω στην αναζήτηση": "‹ back to search",
    "ορθή επανάληψη": "corrected reissue",
    "Η αξία υπερβαίνει το όριο λογικού ελέγχου και πιθανότατα είναι σφάλμα στα δεδομένα της πηγής.":
        "The value exceeds the sanity-check ceiling and is most likely a source-data error.",
    "ύποπτη τιμή · εκτός στατιστικών": "suspicious value · excluded from analytics",
    "Έχει επισημανθεί ως ύποπτη.": "Flagged as suspicious.",
    "επισημασμένη · εκτός στατιστικών": "flagged · excluded from analytics",
    "αξία με ΦΠΑ (διορθωμένη)": "value incl. VAT (corrected)",
    "αξία με ΦΠΑ": "value incl. VAT",
    "προϋπολογισμός": "budget",
    "δημοσίευση": "publication",
    "λήξη υποβολής": "submission deadline",
    "Στοιχεία πράξης": "Act details",
    "Υπογραφή": "Signed",
    "Αρ. σύμβασης": "Contract no.",
    "Ημ. σύμβασης": "Contract date",
    "Έναρξη": "Start",
    "Λήξη": "End",
    "χωρίς προκαθορισμένη λήξη": "no predefined end",
    "Προσφορές": "Bids",
    "μέγ.": "max",
    "Κριτήριο ανάθεσης": "Award criterion",
    "Πιστωτικό": "Credit note",
    "ναι": "yes",
    "όχι": "no",
    "Κωδ. δέσμευσης": "Commitment code",
    "Αξία σύμβασης": "Contract value",
    "Αξία (χωρίς ΦΠΑ)": "Value (excl. VAT)",
    "Διαδικασία": "Procedure",
    "Κριτήριο": "Criterion",
    "Τόπος εκτέλεσης": "Place of performance",
    "Πλατφόρμα": "Platform",
    "Αιτία ακύρωσης": "Cancellation reason",
    "Πρόσθετα στοιχεία": "Additional details",
    "Διαίρεση σε τμήματα": "Division into lots",
    "Συμφωνία-πλαίσιο": "Framework agreement",
    "Τύπος απαιτούμενης προσφοράς": "Required bid type",
    "Εναλλακτικές προσφορές": "Alternative offers",
    "Αριθμός προσφορών": "Number of offers",
    "Δικαίωμα παράτασης": "Extension option",
    "Παράταση (μήνες)": "Extension (months)",
    "Συντελεστής ΦΠΑ": "VAT rate",
    "Περιλαμβάνεται ΦΠΑ": "VAT included",
    "Αξία (EUR)": "Value (EUR)",
    "Αξία (USD)": "Value (USD)",
    "Εκτιμώμενη τιμή (ελάχ.)": "Estimated price (min)",
    "Εκτιμώμενη τιμή (μέγ.)": "Estimated price (max)",
    "Ετήσιος προϋπολογισμός": "Annual budget",
    "Εγγύηση συμμετοχής": "Bid bond",
    "Βαρύτητα τιμής": "Price weighting",
    "Κριτήρια καταλληλότητας": "Eligibility criteria",
    "Κατηγορία καταλληλότητας": "Eligibility category",
    "Αριθμός δημοσίευσης (Journal)": "Publication number (Journal)",
    "Πλατφόρμα ηλ. προμηθειών": "E-procurement platform",
    "Τηλέφωνο": "Phone",
    "Φαξ": "Fax",
    "Διεύθυνση": "Address",
    "Ιστότοπος": "Website",
    "Προμηθευτής": "Supplier",
    "Ανάδοχος / Συμμετέχοντες": "Contractor / Participants",
    "Επωνυμία": "Name",
    "ΑΦΜ": "Tax ID",
    "Ρόλος": "Role",
    "Αξία (με ΦΠΑ)": "Value (incl. VAT)",
    "Είδη / Αντικείμενο": "Items / Object",
    "Περιγραφή": "Description",
    "Ποσότητα": "Quantity",
    "Δεν υπάρχουν καταχωρισμένα είδη.": "No items recorded.",
    "Κωδικοί CPV": "CPV codes",
    "Αναζήτηση πράξεων με αυτόν τον κωδικό": "Search acts with this code",
    "Κατηγορίες": "Categories",
    "Αναζήτηση πράξεων αυτής της κατηγορίας": "Search acts in this category",
    "Αναζήτηση πράξεων αυτής της υποκατηγορίας": "Search acts in this subcategory",
    "Πλήρες κείμενο": "Full text",
    "επεξεργασία ›": "edit ›",
    "χαρακτήρες": "characters",
    "προβολή": "view",
    "Ενέργειες": "Actions",
    "Έγγραφο στο ΚΗΜΔΗΣ": "Document on KHMDHS",
    "Το επίσημο PDF της πράξης": "The official act PDF",
    "Εξαγωγή πινάκων": "Extract tables",
    "Πίνακες από τα συνημμένα σε Excel": "Tables from the attachments to Excel",
    "Σημειώσεις ομάδας": "Team notes",
    "Επαληθευμένο": "Verified",
    "Προς έλεγχο": "To review",
    "Ύποπτο": "Suspicious",
    "Καμία σημείωση.": "No notes.",
    "Κύκλος ζωής διαδικασίας": "Procedure lifecycle",
    "αυτή η πράξη": "this act",
    "Δεν εντοπίστηκαν κατάντη συνδεδεμένες πράξεις.": "No downstream linked acts found.",
    "Συσχετιζόμενα προηγούμενα": "Related upstream",
}

# Authority / contractor detail + list pages (beta_authority/contractor[_results])
_PARTY = {
    "‹ πίσω στις αναθέτουσες": "‹ back to authorities",
    "‹ πίσω στους αναδόχους": "‹ back to contractors",
    "Συγχωνευμένη οντότητα —": "Merged entity —",
    "εγγραφές:": "records:",
    "ΑΦΜ:": "Tax ID:",
    "επεξεργασία στοιχείων ›": "edit details ›",
    "Στοιχεία επικοινωνίας & ταυτότητας": "Contact & identity details",
    "Αναγνωριστικό πηγής": "Source identifier",
    "σύνολο πράξεων": "total acts",
    "αξία συμβάσεων": "contract value",
    "Σύνολα ανά τύπο πράξης": "Totals by act type",
    "Πράξεις": "Acts",
    "Ακυρωμένες": "Cancelled",
    "Αξία": "Value",
    "μόνο αυτές ›": "only these ›",
    "Σύνολο": "Total",
    "μόνο": "only",
    "Ημ/νία": "Date",
    "Διορθωμένη τιμή": "Corrected value",
    "Καμία πράξη.": "No acts.",
    "Κορυφαία αντικείμενα (CPV)": "Top objects (CPV)",
    "Κατηγορία CPV": "CPV category",
    "Προκηρύξεις": "Notices",
    "Πρόθεμα 2 ψηφίων CPV (επίπεδο division) · μόνο προκηρύξεις.":
        "2-digit CPV prefix (division level) · notices only.",
    "Πρόθεμα 2 ψηφίων CPV (επίπεδο division) · μόνο συμβάσεις.":
        "2-digit CPV prefix (division level) · contracts only.",
    "Δεν υπάρχουν δεδομένα CPV.": "No CPV data.",
    "Ανάδοχος": "Contractor",
    "Ανάδοχος / Προμηθευτής": "Contractor / Supplier",
    "Στατιστικός/φορολογικός αρ.": "Statistical/tax no.",
    "Υπεύθυνος επικοινωνίας": "Contact person",
    "Αρ. ΓΕΜΗ": "GEMI no.",
    "Πόλη": "City",
    "Τ.Κ.": "Postal code",
    "Κορυφαίες αναθέτουσες (πελάτες)": "Top authorities (clients)",
    "Αναθέτουσα": "Authority",
    "Συμβάσεις": "Contracts",
    "Δεν υπάρχουν συμβάσεις.": "No contracts.",
    "Τίτλος / Αναθέτουσα": "Title / Authority",
    "Αναθέτουσες Αρχές · Εξερευνητής": "Contracting Authorities · Explorer",
    "Αναθέτουσες Αρχές": "Contracting Authorities",
    "εγγραφές": "records",
    "Αναζήτηση ονόματος αναθέτουσας…": "Search authority name…",
    "Δραστηριότητα ↓": "Activity ↓",
    "Όνομα Α–Ω": "Name A–Z",
    "Όνομα Ω–Α": "Name Z–A",
    "συγχ.": "merged",
    "προκηρύξεις": "notices",
    "συμβάσεις": "contracts",
    "Καμία αναθέτουσα δεν αντιστοιχεί στην αναζήτηση.": "No authority matches the search.",
    "Ανάδοχοι · Εξερευνητής": "Contractors · Explorer",
    "Ανάδοχοι / Προμηθευτές": "Contractors / Suppliers",
    "Αναζήτηση ονόματος ή ΑΦΜ…": "Search name or Tax ID…",
    "αναθέτουσες": "authorities",
    "Κανένας ανάδοχος δεν αντιστοιχεί στην αναζήτηση.": "No contractor matches the search.",
}

# Shared partials: inline name edit + ΓΕΜΗ block (_editable_name, _gemi_block)
_PARTIALS = {
    "Η διόρθωση αντικαθιστά το όνομα· το αρχικό διατηρείται για ασφάλεια.":
        "The correction replaces the name; the original is kept for safety.",
    "Επεξεργασία ονόματος": "Edit name",
    "αποθηκεύτηκε": "saved",
    "Στοιχεία μητρώου ΓΕΜΗ": "GEMI registry data",
    "Ανανέωση από ΓΕΜΗ": "Refresh from GEMI",
    "Άντληση από ΓΕΜΗ": "Fetch from GEMI",
    "επικοινωνία με ΓΕΜΗ…": "contacting GEMI…",
    "Προβολή στοιχείων μητρώου": "View registry data",
    "Νομική μορφή / Κατάσταση": "Legal form / Status",
    "Ημ. σύστασης": "Incorporation date",
    "Επικοινωνία": "Contact",
    "τηλ.": "tel.",
    "Κύριος ΚΑΔ": "Primary activity code",
    "Όλοι οι ενεργοί ΚΑΔ": "All active activity codes",
    "Πηγή:": "Source:",
    "ενημ.": "upd.",
    "Δεν υπάρχουν αποθηκευμένα στοιχεία ΓΕΜΗ για αυτόν τον ΑΦΜ.":
        "No stored GEMI data for this Tax ID.",
}

# Explore (summary) + analytics pages
_EXPLORE_ANALYTICS = {
    "Σύνοψη · Εξερευνητής": "Summary · Explorer",
    "Σύνοψη αναθέσεων": "Awards summary",
    "Συγκεντρωτική εικόνα: ποιες αρχές και ανάδοχοι κυριαρχούν στο τρέχον φίλτρο. Αξία αναθέσεων, ενοποιημένες οντότητες, εξαιρούνται ακυρωμένες/ύποπτες.":
        "Aggregate view: which authorities and contractors dominate the current filter. Award value, merged entities; cancelled/suspicious excluded.",
    "λέξη-κλειδί σε τίτλο/αναθέτουσα/ανάδοχο…": "keyword in title/authority/contractor…",
    "πράξεις στο φίλτρο": "acts in filter",
    "συνολική αξία": "total value",
    "υπολογισμός…": "calculating…",
    "Χωρίς φίλτρο ή μόνο με τύπο: στιγμιαία (προϋπολογισμένα). Τα υπόλοιπα φίλτρα κάνουν ζωντανό υπολογισμό.":
        "No filter or type only: instant (precomputed). Other filters trigger a live computation.",
    "CPV (πρόθεμα)": "CPV (prefix)",
    "→ προβολή ως λίστα πράξεων": "→ view as act list",
    "Ανά αναθέτουσα αρχή": "By contracting authority",
    "Αρχή": "Authority",
    "Καμία αρχή για αυτά τα φίλτρα.": "No authority for these filters.",
    "Ανά ανάδοχο": "By contractor",
    "Συμβ.": "Contr.",
    "Κανένας ανάδοχος για αυτά τα φίλτρα (η ανάλυση αφορά συμβάσεις με νικητή).":
        "No contractor for these filters (analysis covers contracts with a winner).",
    "Κορυφαίοι 100 ανά κατηγορία. Οι αξίες αναδόχων χρησιμοποιούν το ποσό ανάθεσης ανά οικονομικό φορέα όπου υπάρχει.":
        "Top 100 per category. Contractor values use the per-operator award amount where available.",
    "Στατιστικά · Εξερευνητής": "Analytics · Explorer",
    "Επισκόπηση αναθέσεων": "Awards overview",
    'Αξία <strong>αναθέσεων</strong> από <em>συμβάσεις</em> μόνο (όχι πληρωμές, όχι ακυρωμένες), με ενοποιημένες τις διπλότυπες οντότητες. Τα ποσά δεν διπλομετρώνται.':
        'Value of <strong>awards</strong> from <em>contracts</em> only (no payments, no cancelled), with duplicate entities merged. Amounts are not double-counted.',
    "Τα στατιστικά δεν έχουν δημιουργηθεί ακόμη. Εκτελέστε μία φορά:":
        "Analytics haven't been built yet. Run once:",
    "συνολική αξία αναθέσεων": "total award value",
    "αναθέτουσες αρχές": "contracting authorities",
    "Κάλυψη:": "Coverage:",
    "Μηνιαία αξία αναθέσεων": "Monthly award value",
    "συμβ.": "contr.",
    "Περάστε τον δείκτη πάνω από κάθε στήλη για το ποσό.": "Hover over each bar for the amount.",
    "Κορυφαίες αρχές κατά αξία": "Top authorities by value",
    "Κορυφαίοι ανάδοχοι κατά αξία": "Top contractors by value",
    "Κατηγορίες αντικειμένου (CPV)": "Object categories (CPV)",
    'Κατανομή ανά τομέα CPV (διψήφιος κωδικός). Οι αξίες προέρχονται από τις γραμμές ειδών <strong>χωρίς ΦΠΑ</strong>, οπότε δεν αθροίζουν στο συνολικό ποσό· δείχνουν τη σχετική κατανομή. Συμβάσεις και προκηρύξεις χωριστά.':
        'Distribution by CPV sector (two-digit code). Values come from line items <strong>excl. VAT</strong>, so they do not sum to the total amount; they show the relative distribution. Contracts and notices separately.',
    "Τομέας": "Sector",
    "Τομέας ": "Sector ",
    "Αξία συμβ. (χωρίς ΦΠΑ)": "Contract value (excl. VAT)",
    "Αξία προκ. (χωρίς ΦΠΑ)": "Notice value (excl. VAT)",
    "Τα στοιχεία ανανεώνονται μετά από κάθε εισαγωγή με": "Data refreshes after each import with",
}

# Admin: data collection, curation, merge pages
_ADMIN1 = {
    "Διαχείριση συλλογής · ΚΗΜΔΗΣ": "Collection management · KHMDHS",
    "Συλλογή δεδομένων": "Data collection",
    "Εκκίνηση και παρακολούθηση εκτελέσεων backfill για κάθε τύπο πράξης.":
        "Start and monitor backfill runs for each act type.",
    "Νέα συλλογή": "New collection",
    "Μια εκτέλεση είναι ήδη σε εξέλιξη. Δείτε την παρακάτω ή ακυρώστε την πριν ξεκινήσετε νέα.":
        "A run is already in progress. View it below or cancel it before starting a new one.",
    "τύποι πράξεων (επιλέξτε ένα ή περισσότερα — αν τίποτα, όλοι)":
        "act types (pick one or more — if none, all)",
    "Συνέχιση": "Resume",
    "παράλειψη παραθύρων που έχουν ήδη ολοκληρωθεί (συνιστάται)":
        "skip windows already completed (recommended)",
    "Εξαγωγή πλήρους κειμένου": "Full-text extraction",
    "για κάθε νέα πράξη, αντλεί το συνημμένο και αποθηκεύει το κείμενό του.":
        "for each new act, fetches the attachment and stores its text.",
    "Πιο αργό & με πολλές λήψεις από το ΚΗΜΔΗΣ. Συμπληρώνει μόνο όσες πράξεις δεν έχουν ήδη κείμενο· τα σαρωμένα PDF παραλείπονται.":
        "Slower & with many downloads from KHMDHS. Fills only acts that don't already have text; scanned PDFs are skipped.",
    "για κάθε απόφαση, αντλεί το έγγραφο από τη Διαύγεια και αποθηκεύει το κείμενό του.":
        "for each decision, fetches the document from Diavgeia and stores its text.",
    "Πιο αργό & με πολλές λήψεις από τη Διαύγεια. Συμπληρώνει μόνο όσες πράξεις δεν έχουν ήδη κείμενο· τα σαρωμένα PDF παραλείπονται.":
        "Slower & with many downloads from Diavgeia. Fills only acts that don't already have text; scanned PDFs are skipped.",
    "σε αναμονή…": "pending…",
    "Εκκίνηση συλλογής": "Start collection",
    "Η εκτέλεση γίνεται σε ξεχωριστή διαδικασία και επιβιώνει σε επανεκκίνηση του server. Διαστήματα 180 ημερών γίνονται αυτόματα.":
        "The run happens in a separate process and survives a server restart. 180-day windows are handled automatically.",
    "Νέα συλλογή Διαύγειας": "New Diavgeia collection",
    "Άντληση αποφάσεων από τη Διαύγεια (diavgeia.gov.gr) — προκηρύξεις, αναθέσεις και συμβάσεις — με βάση την ημερομηνία ανάρτησης. Μετά την άντληση, γίνεται αυτόματα ταύτιση αναθετουσών αρχών και ένταξη στην εφαρμογή.":
        "Harvest decisions from Diavgeia (diavgeia.gov.gr) — notices, awards and contracts — by posting date. After harvesting, awarding authorities are matched and the acts are surfaced in the app automatically.",
    # Data-collection page — source-grouped redesign
    "Άντληση και εμπλουτισμός πράξεων από τις δύο πηγές: ΚΗΜΔΗΣ και Διαύγεια. Μία εκτέλεση τη φορά.":
        "Harvest and enrich acts from both sources: KHMDHS and Diavgeia. One run at a time.",
    "Κεντρικό Μητρώο (ΚΗΜΔΗΣ)": "Central Registry (KHMDHS)",
    "Κύρια πηγή · cerpp.eprocurement.gov.gr": "Primary source · cerpp.eprocurement.gov.gr",
    "Δεύτερη πηγή · diavgeia.gov.gr": "Secondary source · diavgeia.gov.gr",
    "Άντληση πράξεων από το ΚΗΜΔΗΣ για ένα εύρος ημερομηνιών. Διαστήματα 180 ημερών γίνονται αυτόματα.":
        "Harvest acts from KHMDHS for a date range. 180-day windows are handled automatically.",
    "Άντληση αποφάσεων από τη Διαύγεια — προκηρύξεις, αναθέσεις και συμβάσεις — με βάση την ημερομηνία ανάρτησης. Μετά την άντληση, γίνεται αυτόματα ταύτιση αναθετουσών αρχών και ένταξη στην εφαρμογή.":
        "Harvest decisions from Diavgeia — notices, awards and contracts — by publication date. After harvesting, awarding authorities are matched and the acts are surfaced in the app automatically.",
    "Εκκίνηση συλλογής ΚΗΜΔΗΣ": "Start KHMDHS collection",
    "Εκκίνηση εξαγωγής κειμένου ΚΗΜΔΗΣ": "Start KHMDHS text extraction",
    "Εκκίνηση εξαγωγής κειμένου Διαύγειας": "Start Diavgeia text extraction",
    "Κάλυψη ΚΗΜΔΗΣ": "KHMDHS coverage",
    'Για πράξεις ΚΗΜΔΗΣ που <strong>υπάρχουν ήδη</strong> στη βάση αλλά δεν έχουν πλήρες κείμενο. Αντλεί & αποθηκεύει το κείμενο σε παρτίδες (resumable — μπορείτε να το ξανατρέξετε για να συνεχίσει). Τα σαρωμένα PDF παραλείπονται.':
        'For KHMDHS acts that <strong>already exist</strong> in the database but have no full text. Fetches & stores the text in batches (resumable — you can re-run it to continue). Scanned PDFs are skipped.',
    'Για πράξεις Διαύγειας που <strong>υπάρχουν ήδη</strong> στη βάση αλλά δεν έχουν πλήρες κείμενο. Αντλεί το έγγραφο από τη Διαύγεια & αποθηκεύει το κείμενο σε παρτίδες (resumable — μπορείτε να το ξανατρέξετε για να συνεχίσει). Τα σαρωμένα PDF παραλείπονται.':
        'For Diavgeia acts that <strong>already exist</strong> in the database but have no full text. Fetches the document from Diavgeia & stores the text in batches (resumable — you can re-run it to continue). Scanned PDFs are skipped.',
    "τύποι αποφάσεων (επιλέξτε ένα ή περισσότερα — αν τίποτα, όλοι)":
        "decision types (pick one or more — if none, all)",
    "Εκκίνηση συλλογής Διαύγειας": "Start Diavgeia collection",
    "Η εκτέλεση γίνεται σε ξεχωριστή διαδικασία και επιβιώνει σε επανεκκίνηση του server. Διαστήματα γίνονται αυτόματα.":
        "The run happens in a separate process and survives a server restart. Windows are handled automatically.",
    "ΚΗΜΔΗΣ": "KHMDHS",
    "Κάλυψη Διαύγειας": "Diavgeia coverage",
    "Μαζική εξαγωγή πλήρους κειμένου": "Bulk full-text extraction",
    'Για πράξεις που <strong>υπάρχουν ήδη</strong> στη βάση αλλά δεν έχουν πλήρες κείμενο. Αντλεί & αποθηκεύει το κείμενο σε παρτίδες (resumable — μπορείτε να το ξανατρέξετε για να συνεχίσει). Τα σαρωμένα PDF παραλείπονται.':
        'For acts that <strong>already exist</strong> in the database but have no full text. Fetches & stores the text in batches (resumable — you can re-run it to continue). Scanned PDFs are skipped.',
    'Αφορά την <strong>τοπική</strong> βάση (εκεί που τρέχει ο server). Για την παραγωγή, χρησιμοποιήστε το <code>ingest.sh</code> από το terminal.':
        'Applies to the <strong>local</strong> database (where the server runs). For production, use <code>ingest.sh</code> from the terminal.',
    "τύποι πράξεων (αν τίποτα, όλοι)": "act types (if none, all)",
    "όριο ανά εκτέλεση": "limit per run",
    "Πόσες πράξεις θα επιχειρηθούν αυτή τη φορά.": "How many acts will be attempted this time.",
    "Εκκίνηση εξαγωγής κειμένου": "Start text extraction",
    "Μαζική εξαγωγή πλήρους κειμένου (Διαύγεια)": "Bulk full-text extraction (Diavgeia)",
    'Για πράξεις της <strong>Διαύγειας</strong> που υπάρχουν ήδη στη βάση αλλά δεν έχουν πλήρες κείμενο. Αντλεί το έγγραφο από τη Διαύγεια & αποθηκεύει το κείμενο σε παρτίδες (resumable — μπορείτε να το ξανατρέξετε για να συνεχίσει). Τα σαρωμένα PDF παραλείπονται.':
        'For <strong>Diavgeia</strong> acts that already exist in the database but have no full text. Fetches the document from Diavgeia & stores the text in batches (resumable — you can re-run it to continue). Scanned PDFs are skipped.',
    "Εκκίνηση εξαγωγής κειμένου (Διαύγεια)": "Start text extraction (Diavgeia)",
    "Πρόσφατες εκτελέσεις": "Recent runs",
    "Παράμετροι": "Parameters",
    "Εκκίνηση": "Started",
    "Διάρκεια": "Duration",
    "σε εξέλιξη": "in progress",
    "ολοκληρώθηκε": "completed",
    "ακυρώθηκε": "cancelled",
    "σφάλμα": "error",
    "χάθηκε": "lost",
    "τρέχει…": "running…",
    "Δεν υπάρχουν εκτελέσεις ακόμη.": "No runs yet.",
    "Κάλυψη ανά τύπο": "Coverage by type",
    "Παράθυρα": "Windows",
    "κάλυψη": "coverage",
    "Καμία συλλογή δεν έχει εκτελεστεί ακόμη.": "No collection has run yet.",
    # curate
    "Επιμέλεια πράξεων · Διαχείριση": "Act curation · Administration",
    "Διαχείριση · Επιμέλεια": "Administration · Curation",
    "Σημειώσεις & επισημάνσεις": "Notes & flags",
    "Προσθέστε σημειώσεις, ετικέτες και επισημάνσεις πάνω στις πράξεις. Τα επίσημα δεδομένα δεν τροποποιούνται ποτέ — οι σημειώσεις είναι ξεχωριστό επίπεδο της ομάδας.":
        "Add notes, tags and flags on acts. The official data is never modified — notes are a separate team layer.",
    "Αναζήτηση (τίτλος ή ΑΔΑΜ)": "Search (title or ΑΔΑΜ)",
    "Επισήμανση": "Flag",
    "— οποιαδήποτε —": "— any —",
    "μόνο με σημειώσεις": "only annotated",
    "Βρέθηκαν": "Found",
    "Σημείωση ομάδας": "Team note",
    "επιμέλεια ›": "curate ›",
    "σελίδα": "page",
    # merge
    "Ενοποίηση οντοτήτων · ": "Merge entities · ",
    "Διαχείριση · Ενοποίηση": "Administration · Merge",
    "Ενοποίηση διπλότυπων — ": "Merge duplicates — ",
    "Η πηγή μερικές φορές καταχωρεί την ίδια οντότητα με διαφορετικό ΑΦΜ ή ορθογραφία. Ενοποιήστε τα διπλότυπα εδώ. Τα επίσημα δεδομένα δεν αλλάζουν ποτέ — οι μελλοντικές εισαγωγές θα αντιστοιχίζονται αυτόματα στην ενοποιημένη οντότητα.":
        "The source sometimes records the same entity under a different Tax ID or spelling. Merge duplicates here. The official data never changes — future imports map automatically to the merged entity.",
    "1 · Επιλογή εγγραφών προς ενοποίηση": "1 · Select records to merge",
    "όνομα ή ΑΦΜ/κωδικός… (κενό = πλήρης λίστα)": "name or Tax ID/code… (empty = full list)",
    "✕ καθαρισμός — προβολή όλων": "✕ clear — show all",
    "Πλήρης λίστα · ": "Full list · ",
    "Μέλος": "Member",
    "Κύριο": "Main",
    "ΑΦΜ / Κωδικός": "Tax ID / Code",
    "Άνοιγμα καρτέλας σε νέα καρτέλα": "Open record in a new tab",
    "ήδη ενοποιημένο": "already merged",
    "ομάδα #": "group #",
    "2 · Ενοποίηση επιλεγμένων": "2 · Merge selected",
    'Επιλέξτε ≥2 εγγραφές. <strong>Για να προσθέσετε σε υπάρχουσα ομάδα</strong>, επιλέξτε τη νέα εγγραφή <em>μαζί</em> με τουλάχιστον ένα μέλος της ομάδας (◆). Αν δεν ορίσετε «Κύριο», διατηρείται το υπάρχον (ή επιλέγεται το πιο ενεργό).':
        'Select ≥2 records. <strong>To add to an existing group</strong>, select the new record <em>together</em> with at least one member of the group (◆). If you don\'t set a «Main», the existing one is kept (or the most active is chosen).',
    "Σημ.: ολοκληρώστε την ενοποίηση πριν αλλάξετε σελίδα — οι επιλογές δεν διατηρούνται μεταξύ σελίδων.":
        "Note: complete the merge before changing page — selections are not kept across pages.",
    "Όνομα εμφάνισης (προαιρετικό — αλλιώς του «Κύριου»)":
        "Display name (optional — otherwise the Main's)",
    "Επιμελητής": "Curator",
    "το όνομά σας": "your name",
    "Σημείωση (προαιρετικό)": "Note (optional)",
    "π.χ. λάθος ΑΦΜ στην πηγή": "e.g. wrong Tax ID in source",
    "Ενοποίηση επιλεγμένων": "Merge selected",
    "Καμία εγγραφή για": "No record for",
    "Υπάρχουσες ενοποιήσεις": "Existing merges",
    "Ενοποιημένη οντότητα": "Merged entity",
    "Μέλη": "Members",
    "κύριο:": "main:",
    "Αναίρεση ενοποίησης;": "Undo merge?",
    "αναίρεση": "undo",
    "Καμία ενοποίηση ακόμη.": "No merges yet.",
}

# Admin: act-management list + table-extraction job log
_ADMIN2 = {
    "Διαχείριση δεδομένων · Πράξεις": "Data management · Acts",
    "Διαχείριση · Δεδομένα": "Administration · Data",
    "Διαχείριση πράξεων": "Act management",
    "Όλες οι πράξεις — εισαγόμενες (ΚΗΜΔΗΣ & άλλες πηγές) και χειροκίνητες. Φιλτράρετε, ελέγξτε και επεξεργαστείτε. Οι εισαγόμενες έχουν κλειδωμένα βασικά πεδία (επεξεργασία μέσω σημειώσεων/διορθώσεων)· οι χειροκίνητες είναι πλήρως επεξεργάσιμες.":
        "All acts — imported (KHMDHS & other sources) and manual. Filter, review and edit. Imported acts have locked core fields (edit via notes/corrections); manual acts are fully editable.",
    "Αρ. αναφοράς": "Reference no.",
    "Πηγή δεδομένων": "Data source",
    "Προέλευση": "Origin",
    "Εισαγόμενη": "Imported",
    "Χειροκίνητη": "Manual",
    "Κατάσταση πηγής": "Source status",
    "Συνημμένα": "Attachments",
    "Με συνημμένα": "With attachments",
    "Χωρίς": "Without",
    "Από (δημοσίευση)": "From (published)",
    "Κατηγορία / Υποκατηγορία (Ctrl/⌘-click για πολλαπλά)":
        "Category / Subcategory (Ctrl/⌘-click for multiple)",
    "Πρόσφατη επεξεργασία": "Recently edited",
    "+ Νέα πράξη (χειροκίνητη)": "+ New act (manual)",
    "Αποθήκευση των καθαρών πινάκων στις πράξεις (μη δημοσιευμένοι)":
        "Save the clean tables to the acts (unpublished)",
    "αποθήκευση": "save",
    "Έλεγχος & εξαγωγή πινάκων για το φιλτραρισμένο σύνολο":
        "Check & extract tables for the filtered set",
    "Το σύνολο": "The set",
    "υπερβαίνει το όριο των": "exceeds the limit of",
    "πράξεων — περιορίστε το φίλτρο για να ξεκινήσετε εξαγωγή.":
        "acts — narrow the filter to start an extraction.",
    "Όρια ανά εργασία: έως": "Per-job limits: up to",
    "πράξεις (αναφορά)": "acts (report)",
    "η αποθήκευση απαιτεί ≤": "saving requires ≤",
    "(τώρα": "(now",
    "έως": "to",
    "με αποθήκευση": "with saving",
    "Πρόσφατες εξαγωγές πινάκων:": "Recent table extractions:",
    "Πηγή": "Source",
    "Συνημ.": "Attach.",
    "χειροκίνητη": "manual",
    "εισαγωγή": "import",
    "έχει συνημμένα": "has attachments",
    "Μετατροπή σε χειροκίνητη πράξη; Τα βασικά πεδία θα γίνουν πλήρως επεξεργάσιμα και οι μελλοντικές εισαγωγές δεν θα την αγγίζουν. Μη αναστρέψιμο.":
        "Convert to a manual act? Core fields become fully editable and future imports won't touch it. Irreversible.",
    "ανάληψη →": "take over →",
    "Καμία πράξη για αυτά τα φίλτρα.": "No acts for these filters.",
    # JS alert/confirm in the extract guard
    "Δεν επιτρέπεται η έναρξη:": "Cannot start:",
    "πράξεις υπερβαίνουν το όριο των": "acts exceed the limit of",
    " με αποθήκευση": " with saving",
    " (μόνο αναφορά)": " (report only)",
    "Περιορίστε το φίλτρο": "Narrow the filter",
    " ή ξετικάρετε την «αποθήκευση».": " or untick «save».",
    "Έναρξη μαζικής εξαγωγής & ΑΠΟΘΗΚΕΥΣΗΣ πινάκων για τις":
        "Start bulk table extraction & SAVING for the",
    "φιλτραρισμένες πράξεις;\\n\\n(Οι καθαροί πίνακες αποθηκεύονται ΜΗ δημοσιευμένοι· πράξεις που έχουν ήδη πίνακες παραλείπονται. Μπορεί να διαρκέσει.)":
        "filtered acts?\\n\\n(Clean tables are saved UNPUBLISHED; acts that already have tables are skipped. May take a while.)",
    "Έναρξη μαζικής εξαγωγής πινάκων για τις":
        "Start bulk table extraction for the",
    "φιλτραρισμένες πράξεις;\\n\\n(Μόνο αναφορά — δεν αποθηκεύονται πίνακες. Μπορεί να διαρκέσει.)":
        "filtered acts?\\n\\n(Report only — no tables are saved. May take a while.)",
    # table-extraction job log
    "Εξαγωγή πινάκων · εκτέλεση #": "Table extraction · run #",
    "‹ πίσω στη Διαχείριση Πράξεων": "‹ back to Act Management",
    "Μαζική εξαγωγή πινάκων · ": "Bulk table extraction · ",
    "αποθήκευση (μη δημοσιευμένοι)": "save (unpublished)",
    "μόνο αναφορά (χωρίς αποθήκευση)": "report only (no saving)",
    "σε εξέλιξη — PID": "in progress — PID",
    "Ακύρωση εκτέλεσης #": "Cancel run #",
    "Ακύρωση": "Cancel",
    "η διαδικασία χάθηκε": "the process was lost",
    "χάθηκε χωρίς εξήγηση": "lost without explanation",
    "Αποτελέσματα ανά πράξη": "Results per act",
    "επεξεργασμένες": "processed",
    "με πίνακες": "with tables",
    "πίνακες": "tables",
    "αλλοιωμένα": "garbled",
    "χρειάζονται OCR": "need OCR",
    "χωρίς πίνακες": "no tables",
    "απέτυχαν": "failed",
    "πίνακες αποθηκεύτηκαν": "tables saved",
    "Με πίνακες": "With tables",
    "Αλλοιωμένα": "Garbled",
    "Χρειάζονται OCR": "Need OCR",
    "Χωρίς πίνακες": "No tables",
    "Απέτυχαν": "Failed",
    "φιλτραρισμένου": "filtered",
    "πλήρους": "full",
    "Πίνακες": "Tables",
    "Αποθηκ.": "Saved",
    "Άνοιγμα στην επεξεργασία πράξης (νέα καρτέλα)": "Open in act editor (new tab)",
    "✓ εξήχθησαν": "✓ extracted",
    "⚠ αλλοιωμένα": "⚠ garbled",
    "χωρίς συνημμένο": "no attachment",
    "Η επεξεργασία ξεκίνησε — τα αποτελέσματα εμφανίζονται καθώς προχωρά (ανανέωση κάθε 6 δλ).":
        "Processing started — results appear as it progresses (refreshes every 6s).",
    "Δεν καταγράφηκαν αποτελέσματα.": "No results recorded.",
    "Έξοδος (τέλος αρχείου)": "Output (end of file)",
}

# Admin: backfill job log + item correction + party edit form
_ADMIN3 = {
    "Εκτέλεση #": "Run #",
    "‹ πίσω στη Συλλογή Δεδομένων": "‹ back to Data Collection",
    "Εκτέλεση συλλογής · #": "Collection run · #",
    "με συνέχιση": "with resume",
    "Σίγουρα ακύρωση εκτέλεσης #": "Really cancel run #",
    "Παράθυρα ανά τύπο": "Windows by type",
    "ολοκληρωμένα": "completed",
    "εκκρεμή": "pending",
    "σφάλματα": "errors",
    "Διάστημα": "Interval",
    "ολοκλ.": "done",
    "τρέχει": "running",
    "εκκρεμές": "pending",
    "Δεν υπάρχουν παράθυρα για αυτή την εκτέλεση ακόμη.": "No windows for this run yet.",
    "Πράξεις αυτής της εκτέλεσης": "Acts in this run",
    "νέες": "new",
    "ενημερώσεις": "updates",
    "χειροκίνητες (παράλειψη)": "manual (skipped)",
    "με εξαγωγή πλήρους κειμένου": "with full text extracted",
    "Με πλήρες κείμενο": "With full text",
    "Χωρίς πλήρες κείμενο": "Without full text",
    "Με αλλοιωμένο κείμενο": "With garbled text",
    "καμία πράξη σε αυτό το φίλτρο": "no acts in this filter",
    "Ενέργεια": "Action",
    "● νέα": "● new",
    "↻ ενημέρωση": "↻ update",
    "⤫ χειροκίνητη": "⤫ manual",
    "Το κείμενο φαίνεται αλλοιωμένο — χρειάζεται OCR": "The text looks garbled — needs OCR",
    "⚠ αλλοιωμένο": "⚠ garbled",
    "χαρ.": "chars",
    "υπήρχε ήδη": "already existed",
    "σαρωμένο/χωρίς κείμενο": "scanned/no text",
    "μη διαθέσιμο": "unavailable",
    "Δεν έχει καταγραφεί αναλυτικό μητρώο πράξεων για αυτή την εκτέλεση":
        "No detailed act log recorded for this run",
    "ακόμη": "yet",
    "(Διαθέσιμο για εκτελέσεις που ξεκινούν από αυτό το πάνελ· παλαιότερες εκτελέσεις δεν το έχουν.)":
        "(Available for runs started from this panel; older runs don't have it.)",
    "Καμία έξοδος ακόμη — η εκτέλεση μόλις ξεκίνησε ή δεν δημιουργήθηκε αρχείο log.":
        "No output yet — the run just started or no log file was created.",
    "Λεπτομέρειες": "Details",
    "Η σελίδα ανανεώνεται αυτόματα κάθε 6 δευτερόλεπτα.":
        "The page refreshes automatically every 6 seconds.",
    # items
    "Διόρθωση ειδών · ": "Item correction · ",
    "Διόρθωση της αξίας (χωρίς ΦΠΑ) κάθε είδους. Η αρχική τιμή από την πηγή διατηρείται· η διόρθωση χρησιμοποιείται στην εμφάνιση και στα στατιστικά ανά CPV. Άφησε ένα πεδίο κενό για καμία διόρθωση.":
        "Correct the value (excl. VAT) of each item. The original source value is kept; the correction is used in display and in per-CPV analytics. Leave a field empty for no correction.",
    "‹ σημειώσεις & αξία σύμβασης": "‹ notes & contract value",
    "προβολή πράξης ›": "view act ›",
    "Αρχική αξία (χωρίς ΦΠΑ)": "Original value (excl. VAT)",
    "Διορθωμένη αξία": "Corrected value",
    "Καμία γραμμή ειδών για αυτή την πράξη.": "No item lines for this act.",
    "Επιμελητής (για το ιστορικό)": "Curator (for the log)",
    "Αποθήκευση διορθώσεων": "Save corrections",
    # party form
    "Επεξεργασία · ": "Edit · ",
    "‹ πίσω στην οντότητα": "‹ back to the entity",
    "Επεξεργασία στοιχείων. Αυτά τα πεδία δεν επικαλύπτονται ποτέ από αυτόματες εισαγωγές. Το όνομα επεξεργάζεται από τη σελίδα της οντότητας.":
        "Edit details. These fields are never overwritten by automatic imports. The name is edited from the entity's page.",
}

# Edit-form field labels & group headings (admin.py _act_form_fields /
# _party_form_fields). Passed through |t in the form templates.
_FORMLABELS = {
    "Έχει συνημμένα": "Has attachments",
    "ΑΦΜ/κωδικός αρχής": "Tax ID/authority code",
    "Ακυρωμένη": "Cancelled",
    "Αναφορές & πλατφόρμα": "References & platform",
    "Αξία με ΦΠΑ": "Value incl. VAT",
    "Αξία σε EUR": "Value in EUR",
    "Αξία σε USD": "Value in USD",
    "Αξία χωρίς ΦΠΑ": "Value excl. VAT",
    "Αριθμός αναφοράς": "Reference number",
    "Αριθμός τμήματος": "Lot number",
    "Βαρύτητα τιμής (%)": "Price weighting (%)",
    "Γεωγραφία": "Geography",
    "ΔΣΑ (DPS)": "DPS",
    "Διεύθυνση (οδός)": "Address (street)",
    "Είδος σύμβασης (πηγής)": "Contract category (source)",
    "Ελληνικό ΑΦΜ": "Greek Tax ID",
    "Επιλεξιμότητα": "Eligibility",
    "Επισημάνσεις": "Flags",
    "Ηλεκτρονικός πλειστηριασμός": "Electronic auction",
    "Ημ. δημοσίευσης": "Publication date",
    "Ημ. υπογραφής": "Signing date",
    "Ημερομηνίες": "Dates",
    "Κανονισμός": "Regulation",
    "Κατηγοριοποίηση": "Classification",
    "Κωδικός NUTS": "NUTS code",
    "Κωδικός κατηγορίας": "Category code",
    "Κωδικός τύπου": "Type code",
    "Λήξη υποβολής": "Submission deadline",
    "Νόμισμα": "Currency",
    "Οικονομικά": "Financials",
    "Προσφορές & παράταση": "Bids & extension",
    "Προϋπολογισμός": "Budget",
    "Στοιχεία": "Details",
    "Συντελεστής ΦΠΑ (%)": "VAT rate (%)",
    "Σύνδεσμος πηγής": "Source link",
    "Σύντομη περιγραφή": "Short description",
    "Ταυτότητα & πηγή": "Identity & source",
    "Ταυτότητα": "Identity",
    "Τοποθεσία": "Location",
    "Τύπος εγγράφου": "Document type",
    "Υποτύπος εγγράφου": "Document subtype",
    "Χώρα": "Country",
}

# Act-edit hub panels (_panel_fields/_annotate/_fulltext/_tables) + CPV picker
_PANELS = {
    # _panel_fields
    "Εισαγόμενη πράξη — τα βασικά πεδία ανήκουν στην πηγή και ενημερώνονται από τις αυτόματες εισαγωγές. Για διορθώσεις χωρίς αλλοίωση της πηγής, χρησιμοποιήστε την καρτέλα «Σημειώσεις». Για να επεξεργαστείτε απευθείας τα βασικά πεδία, αναλάβετε την κυριότητα της πράξης — μονόδρομη ενέργεια· έπειτα οι αυτόματες εισαγωγές δεν θα την τροποποιούν.":
        "Imported act — the core fields belong to the source and are updated by automatic imports. For corrections without altering the source, use the «Notes» tab. To edit the core fields directly, take ownership of the act — a one-way action; after that automatic imports won't modify it.",
    "Ανάληψη κυριότητας της πράξης": "Take ownership of act",
    "; Δεν αναιρείται.": "? Not undoable.",
    "Ανάληψη κυριότητας & επεξεργασία": "Take ownership & edit",
    "ΑΦΜ ή org_id της αρχής": "Tax ID or org_id of the authority",
    "Αποθήκευση βασικών πεδίων": "Save core fields",
    # _cpv_field
    "Αναζήτηση με κωδικό ή λέξεις…": "Search by code or words…",
    "Επιλογή κωδικών CPV": "Select CPV codes",
    "Κωδικοί CPV — επιλογή": "CPV codes — selection",
    "Κλείσιμο παραθύρου ✕": "Close window ✕",
    "Αναζήτηση κωδικού ή περιγραφής…": "Search code or description…",
    # _cpv_browse
    "Όλες οι κατηγορίες": "All categories",
    "Προσθήκη κωδικού": "Add code",
    "Υποκατηγορία ›": "Subcategory ›",
    "Καμία υποκατηγορία.": "No subcategory.",
    # _cpv_suggest
    "όλα όσα ξεκινούν με": "everything starting with",
    "Καμία αντιστοιχία.": "No match.",
    # _panel_annotate
    "Σημείωση": "Note",
    "ελεύθερο κείμενο…": "free text…",
    "Ετικέτες (χωρισμένες με κόμμα ή κενό)": "Tags (comma or space separated)",
    "— καμία —": "— none —",
    "Διορθωμένη αξία (με ΦΠΑ)": "Corrected value (incl. VAT)",
    "Αρχική τιμή από την πηγή:": "Original source value:",
    "Η διόρθωση χρησιμοποιείται στους υπολογισμούς· η αρχική τιμή διατηρείται και εμφανίζεται.":
        "The correction is used in calculations; the original value is kept and shown.",
    "Διορθωμένη αξία (χωρίς ΦΠΑ)": "Corrected value (excl. VAT)",
    "Αποθήκευση σημείωσης": "Save note",
    "✓ αποθηκεύτηκε": "✓ saved",
    "Άδειο πεδίο σε όλα → η τρέχουσα σημείωση αφαιρείται (το ιστορικό διατηρείται).":
        "All fields empty → the current note is removed (history is kept).",
    "διόρθωση ειδών ›": "correct items ›",
    "Ιστορικό αλλαγών": "Change history",
    "τρέχον": "current",
    "Καμία σημείωση ακόμη.": "No notes yet.",
    "π.χ. 6138.00 — άφησέ το κενό για καμία διόρθωση": "e.g. 6138.00 — leave empty for no correction",
    "π.χ. 4950.00 — άφησέ το κενό για καμία διόρθωση": "e.g. 4950.00 — leave empty for no correction",
    # _panel_fulltext
    "Αποθηκευμένο κείμενο": "Stored text",
    "ενημερώθηκε": "updated",
    "πηγή:": "source:",
    "Δεν υπάρχει αποθηκευμένο κείμενο ακόμη.": "No stored text yet.",
    "Εξαγωγή από συνημμένα": "Extraction from attachments",
    "Άντληση με ΑΔΑΜ": "Fetch by ΑΔΑΜ",
    "Άντληση εγγράφου": "Fetch document",
    "λήψη από ΚΗΜΔΗΣ…": "downloading from KHMDHS…",
    "ή": "or",
    "Μεταφόρτωση αρχείων": "Upload files",
    "Σύρετε εδώ τα αρχεία": "Drag files here",
    "Επιλογή αρχείων": "Choose files",
    "Φόρτωση αρχείων": "Load files",
    "επεξεργασία…": "processing…",
    # _panel_tables
    "π.χ. 24PROC… — ο ΑΔΑΜ της πράξης": "e.g. 24PROC… — the act's ΑΔΑΜ",
    "Άντληση & σάρωση": "Fetch & scan",
    "Το επίσημο έγγραφο αντλείται απευθείας από το ΚΗΜΔΗΣ — δεν χρειάζεται να το κατεβάσετε.":
        "The official document is fetched directly from KHMDHS — no need to download it.",
    "Σύρετε εδώ τα συνημμένα": "Drag the attachments here",
    "Σάρωση για πίνακες": "Scan for tables",
    'Το OCR για σαρωμένα PDF/εικόνες είναι ανενεργό — ορίστε <code>ANTHROPIC_API_KEY</code> και επανεκκινήστε για να ενεργοποιηθεί.':
        'OCR for scanned PDFs/images is off — set <code>ANTHROPIC_API_KEY</code> and restart to enable it.',
    "Αποθηκευμένοι πίνακες": "Saved tables",
}

# Act create/edit form (admin_act_form.html)
_ACTFORM = {
    "Νέα πράξη": "New act",
    "Επεξεργασία πράξης": "Edit act",
    "‹ πίσω στη διαχείριση πράξεων": "‹ back to act management",
    "Δημιουργία πράξης (χειροκίνητη)": "Create act (manual)",
    'Συμπληρώστε τα πεδία και αποθηκεύστε. Η πράξη δημιουργείται ως <strong>χειροκίνητη</strong> (origin = authored) και δεν επηρεάζεται ποτέ από αυτόματες εισαγωγές.':
        'Fill in the fields and save. The act is created as <strong>manual</strong> (origin = authored) and is never affected by automatic imports.',
    "· χειροκίνητη πράξη · πλήρως επεξεργάσιμη.": "· manual act · fully editable.",
    "Τελευταία επεξεργασία:": "Last edited:",
    "✓ Αποθηκεύτηκε.": "✓ Saved.",
    "✓ Η πράξη έγινε χειροκίνητη. Τα βασικά πεδία είναι πλέον πλήρως επεξεργάσιμα και οι αυτόματες εισαγωγές δεν θα την τροποποιούν.":
        "✓ The act is now manual. Its core fields are fully editable and automatic imports won't modify it.",
    "Αναγνωριστικό": "Identifier",
    "ΑΔΑΜ (προαιρετικό — αφήστε κενό για αυτόματο MANUAL-…)":
        "ΑΔΑΜ (optional — leave empty for an automatic MANUAL-…)",
    "αφήστε κενό για αυτόματη δημιουργία": "leave empty for automatic creation",
    "Δημιουργία πράξης": "Create act",
    "Πλήρες κείμενο & πίνακες": "Full text & tables",
    "Διαθέσιμα μετά την αποθήκευση. Δημιουργήστε πρώτα την πράξη, και στη συνέχεια θα μπορείτε να προσθέσετε πλήρες κείμενο και πίνακες από τη φόρμα επεξεργασίας.":
        "Available after saving. Create the act first, then you can add full text and tables from the edit form.",
}

# Legacy (pre-redesign) templates: act_stub, act_edit, _results, _explore_results,
# index, explore, notice, authority, contractor, analytics, *_index
_LEGACY = {
    "μη διαθέσιμη στη βάση": "not in the database",
    "‹ πίσω": "‹ back",
    "Δεν έχει συλλεχθεί στη βάση": "Not collected in the database",
    "Η πράξη με ΑΔΑΜ": "The act with ΑΔΑΜ",
    "αναφέρεται από άλλες πράξεις που έχουμε καταγράψει, αλλά η ίδια δεν έχει συλλεχθεί ακόμη — πιθανότατα γιατί η ημερομηνία της πέφτει εκτός των διαστημάτων που έχουν συλλεχθεί.":
        "is referenced by other acts we've recorded, but it hasn't been collected yet — probably because its date falls outside the collected windows.",
    "Παραπέμπεται από": "Referenced by",
    "Συσχετιζόμενες κατάντη": "Related downstream",
    "Πώς να το συμπληρώσετε": "How to fill it in",
    "Εκτελέστε εστιασμένη συλλογή για τον τύπο και την ημερομηνία της πράξης. Παράδειγμα για ένα ADAM που ξεκινά με":
        "Run a focused collection for the act's type and date. Example for an ADAM starting with",
    "(έτος 2024):": "(year 2024):",
    "είναι ασφαλές: παραλείπει διαστήματα που έχουν ήδη ολοκληρωθεί. Μετά τη συλλογή, επαναφορτώστε τη σελίδα.":
        "is safe: it skips windows already completed. After the collection, reload the page.",
    "Το": "The",
    # act_edit
    "Επεξεργασία": "Edit",
    "‹ πίσω στην επιμέλεια": "‹ back to curation",
    "Σημειώσεις, πλήρες κείμενο και πίνακες για αυτή την πράξη — όλα σε μία σελίδα.":
        "Notes, full text and tables for this act — all on one page.",
    "✓ Η πράξη έγινε χειροκίνητη — τα βασικά πεδία είναι πλέον επεξεργάσιμα.":
        "✓ The act is now manual — its core fields are now editable.",
    "Βασικά πεδία": "Core fields",
    "Σημειώσεις": "Notes",
    # _explore_results legacy
    "Ενεργά φίλτρα:": "Active filters:",
    "Κανένας ανάδοχος για αυτά τα φίλτρα (η ανάλυση αναδόχων αφορά συμβάσεις με νικητή).":
        "No contractor for these filters (contractor analysis covers contracts with a winner).",
    "Εμφανίζονται οι 100 κορυφαίοι ανά κατηγορία. Οι αξίες αναδόχων χρησιμοποιούν το ποσό ανάθεσης ανά οικονομικό φορέα όπου υπάρχει.":
        "Showing the top 100 per category. Contractor values use the per-operator award amount where available.",
    # _results legacy
    "Δημοσίευση / Λήξη": "Publication / Closing",
    "Διορθωμένη τιμή — αρχική από πηγή: €": "Corrected value — original from source: €",
    "Ύποπτη τιμή — εξαιρείται από τα στατιστικά": "Suspicious value — excluded from analytics",
    "ύποπτη τιμή": "suspicious value",
    "ανά σελίδα": "per page",
    "Καμία διακήρυξη δεν αντιστοιχεί στα φίλτρα. Δοκιμάστε να χαλαρώσετε τα κριτήρια.":
        "No tender matches the filters. Try relaxing the criteria.",
}

# Legacy index/explore search pages
_LEGACY2 = {
    "όλα": "all",
    "κανένα": "none",
    "τίτλος, λέξη-κλειδί ή ΑΔΑΜ…": "title, keyword or ΑΔΑΜ…",
    "Στο πλήρες κείμενο": "In the full text",
    "λέξεις στο κείμενο εγγράφων…": "words in the document text…",
    'Αναζήτηση μέσα στο κείμενο των συνημμένων. Χρησιμοποιήστε <code class="cpv">"εισαγωγικά"</code> για ακριβή φράση, <code class="cpv">OR</code> για εναλλακτικές, <code class="cpv">-λέξη</code> για εξαίρεση, <code class="cpv">λέξη*</code> για αναζήτηση με μέρος λέξης (ηλεκτρολογικ* → βρίσκει ηλεκτρολογικά, ηλεκτρολογικών κ.λπ.).':
        'Search within the attachment text. Use <code class="cpv">"quotes"</code> for an exact phrase, <code class="cpv">OR</code> for alternatives, <code class="cpv">-word</code> to exclude, <code class="cpv">word*</code> for partial-word search (ηλεκτρολογικ* → finds ηλεκτρολογικά, ηλεκτρολογικών etc.).',
    "Μέσα στους πίνακες": "Within the tables",
    "λέξεις στους εξαγμένους πίνακες…": "words in the extracted tables…",
    'Κρατά μόνο πράξεις με δημοσιευμένο πίνακα που περιέχει τους όρους. Ίδια σύνταξη με το πλήρες κείμενο (<code class="cpv">"φράση"</code>, <code class="cpv">OR</code>, <code class="cpv">-λέξη</code>, <code class="cpv">λέξη*</code> για μέρος λέξης).':
        'Keeps only acts with a published table containing the terms. Same syntax as the full text (<code class="cpv">"phrase"</code>, <code class="cpv">OR</code>, <code class="cpv">-word</code>, <code class="cpv">word*</code> for partial word).',
    "Ενεργές": "Active",
    "Ορθή επ.": "Corrected",
    "Αναθέτουσα Αρχή": "Contracting Authority",
    "Αντικείμενο": "Object",
    "CPV (κωδικός ή πρόθεμα)": "CPV (code or prefix)",
    "NUTS (πρόθεμα)": "NUTS (prefix)",
    "Ημερομηνία δημοσίευσης": "Publication date",
    "Καταληκτική ημ/νία υποβολής": "Submission deadline",
    "Αξία (€ με ΦΠΑ)": "Value (€ incl. VAT)",
    "ελάχ.": "min",
    "Αποτελέσματα": "Results",
    "φόρτωση…": "loading…",
    "σχετικότητα (στο κείμενο)": "relevance (in text)",
    "πιο πρόσφατη δημοσίευση": "most recent publication",
    "παλαιότερη δημοσίευση": "oldest publication",
    "πιο πρόσφατη υπογραφή": "most recent signing",
    "παλαιότερη υπογραφή": "oldest signing",
    "μεγαλύτερη αξία": "highest value",
    "μικρότερη αξία": "lowest value",
    "πλησιέστερη λήξη": "nearest deadline",
    # explore legacy
    "Σύνοψη · Δημόσιες Συμβάσεις": "Summary · Public Contracts",
    "Συγκεντρωτικά ανά αρχή & ανάδοχο": "Aggregated by authority & contractor",
    "Τα ίδια φίλτρα με την αναζήτηση, αλλά τα αποτελέσματα ομαδοποιημένα. Εξαιρούνται ακυρωμένες, ύποπτες και υπερβολικές τιμές, όπως στα στατιστικά.":
        "The same filters as search, but the results grouped. Cancelled, suspicious and excessive values are excluded, as in analytics.",
    "Όλες οι πράξεις": "All acts",
    "λέξη-κλειδί…": "keyword…",
    "— οποιοδήποτε —": "— any —",
    "— οποιαδήποτε —": "— any —",
    "↺ καθαρισμός φίλτρων": "↺ clear filters",
    "→ προβολή ως λίστα": "→ view as list",
    "Υπολογισμός…": "Calculating…",
}

# Legacy index lists + detail pages (notice/authority/contractor/analytics)
_LEGACY3 = {
    "Κατάλογος": "Directory",
    "Αναθέτουσες Αρχές · Κατάλογος": "Contracting Authorities · Directory",
    "Φορείς του δημοσίου που προκηρύσσουν και αναθέτουν συμβάσεις.":
        "Public bodies that publish and award contracts.",
    "Αναζήτηση ονόματος": "Search by name",
    "περισσότερες πράξεις": "most acts",
    "όνομα (Α→Ω)": "name (A→Z)",
    "όνομα (Ω→Α)": "name (Z→A)",
    "αρχές": "authorities",
    "Προκηρ.": "Notices",
    "◆ ενοποιημένο": "◆ merged",
    "Καμία αρχή δεν ταιριάζει.": "No authority matches.",
    "Ανάδοχοι / Προμηθευτές · Κατάλογος": "Contractors / Suppliers · Directory",
    "Ανάδοχοι & Προμηθευτές": "Contractors & Suppliers",
    "Οικονομικοί φορείς που έχουν αναδειχθεί ανάδοχοι ή έχουν πληρωθεί.":
        "Economic operators awarded contracts or paid.",
    "Αναζήτηση (επωνυμία ή ΑΦΜ)": "Search (name or Tax ID)",
    "φορείς": "operators",
    "Αγοραστές": "Buyers",
    "Κανένας φορέας δεν ταιριάζει.": "No operator matches.",
    # detail-page shared
    "Η αξία υπερβαίνει το όριο λογικού ελέγχου (€": "The value exceeds the sanity-check ceiling (€",
    ") και πιθανότατα είναι σφάλμα στα δεδομένα της πηγής. Εξαιρείται από τα συγκεντρωτικά στοιχεία.":
        ") and is most likely a source-data error. Excluded from the aggregate figures.",
    "Έχει επισημανθεί ως ύποπτη και εξαιρείται από τα συγκεντρωτικά στοιχεία.":
        "Flagged as suspicious and excluded from the aggregate figures.",
    "Δημοσίευση": "Publication",
    "Αριθμός σύμβασης": "Contract number",
    "Κωδικός δέσμευσης": "Commitment code",
    "Διορθωμένη τιμή· η αρχική τιμή από την πηγή ήταν €": "Corrected value; the original source value was €",
    "— προβολή κειμένου": "— view text",
    "Επίσημη πηγή": "Official source",
    "Δείτε το έγγραφο στο ΚΗΜΔΗΣ (PDF)": "View the document on KHMDHS (PDF)",
    "Το επίσημο έγγραφο της πράξης, απευθείας από το ΚΗΜΔΗΣ.":
        "The official act document, straight from KHMDHS.",
    "Εξαγωγή πινάκων από τα συνημμένα": "Extract tables from the attachments",
    "Αντλεί το έγγραφο και εξάγει πίνακες (προϋπολογισμού, ειδών) σε Excel.":
        "Fetches the document and extracts tables (budget, items) to Excel.",
    "Δεν εντοπίστηκαν κατάντη συνδεδεμένες πράξεις στη βάση.":
        "No downstream linked acts found in the database.",
    # authority
    "Ενοποιημένη οντότητα.": "Merged entity.",
    "Συγκεντρώνει": "Combines",
    "εγγραφές της πηγής:": "source records:",
    "διαχείριση ›": "manage ›",
    "Πλήθος": "Count",
    "Συνολική αξία (με ΦΠΑ)": "Total value (incl. VAT)",
    "συνολικά": "total",
    "Διαίρεση": "Division",
    "Διακηρ.": "Notices",
    "Συν. αξία": "Total value",
    "Πρόθεμα 2 ψηφίων CPV (επίπεδο division).": "2-digit CPV prefix (division level).",
    "Δεν εντοπίστηκαν CPVs στις διακηρύξεις αυτής της αρχής.":
        "No CPVs found in this authority's notices.",
    # contractor
    "Ελληνικός ΑΦΜ": "Greek Tax ID",
    "Αλλοδαπός": "Foreign",
    "πρώτη εμφάνιση": "first seen",
    "εγγραφές της πηγής (πιθανά διπλότυπα ΑΦΜ/ονόματος):":
        "source records (likely duplicate Tax ID/name):",
    "Κορυφαίοι αγοραστές": "Top buyers",
    "Αντικείμενα (CPV)": "Objects (CPV)",
    # analytics
    "Αναλυτικά · Δημόσιες Συμβάσεις": "Analytics · Public Contracts",
    "Αναλυτικά": "Analytics",
    'Αξία <strong>αναθέσεων</strong> — υπολογισμένη μόνο από <em>συμβάσεις</em> (όχι πληρωμές, όχι ακυρωμένες), με ενοποιημένες τις διπλότυπες οντότητες. Τα ποσά δεν διπλομετρώνται.':
        'Value of <strong>awards</strong> — computed only from <em>contracts</em> (no payments, no cancelled), with duplicate entities merged. Amounts are not double-counted.',
    "Τα αναλυτικά δεν έχουν δημιουργηθεί ακόμη. Εκτελέστε μία φορά:":
        "Analytics haven't been built yet. Run once:",
    "Συνολική αξία αναθέσεων": "Total award value",
    'Κατανομή ανά τομέα CPV (διψήφιος κωδικός). Οι αξίες προέρχονται από τις γραμμές ειδών <strong>χωρίς ΦΠΑ</strong>, οπότε δεν αθροίζουν στο συνολικό ποσό αναθέσεων· δείχνουν τη σχετική κατανομή ανά κατηγορία. Συμβάσεις και προκηρύξεις εμφανίζονται χωριστά.':
        'Distribution by CPV sector (two-digit code). Values come from line items <strong>excl. VAT</strong>, so they do not sum to the total award amount; they show the relative distribution per category. Contracts and notices are shown separately.',
}

# Tender-Tables tool (app/templates/tables/*) — shared with the sibling tool
_TABLES = {
    "Εξαγωγή πινάκων · Διαχείριση": "Table extraction · Administration",
    "Διαχείριση · Εργαλεία": "Administration · Tools",
    "Εξαγωγή πινάκων από συνημμένα": "Extract tables from attachments",
    "Αντλήστε το επίσημο έγγραφο μιας πράξης με τον ΑΔΑΜ της, ή ανεβάστε αρχεία, και εξάγετε τους πίνακες (προϋπολογισμού, ειδών κ.λπ.) σε Excel. Υποστηρίζονται PDF, Word, Excel, CSV, zip και εικόνες.":
        "Fetch an act's official document by its ΑΔΑΜ, or upload files, and extract the tables (budget, items, etc.) to Excel. PDF, Word, Excel, CSV, zip and images are supported.",
    # select / kinds
    "μορφές πινάκων": "table formats",
    "εικόνα — OCR αργότερα": "image — OCR later",
    "μη υποστηριζόμενο": "unsupported",
    "Όλα είναι επιλεγμένα — αφαιρέστε ό,τι δεν θέλετε να σαρωθεί, μετά συνεχίστε.":
        "All are selected — remove anything you don't want scanned, then continue.",
    "Προεπισκόπηση / επιλογή σελίδων": "Preview / select pages",
    "προεπισκόπηση": "preview",
    "κλικ για μεγέθυνση": "click to enlarge",
    "Επιλογή όλων": "Select all",
    "Επιλογή / αποεπιλογή όλων": "Select / deselect all",
    "Κανένα": "None",
    "Σάρωση επιλεγμένων": "Scan selected",
    "σάρωση…": "scanning…",
    # results / export
    "Ένα βιβλίο, ένα φύλλο ανά πίνακα": "One workbook, one sheet per table",
    "Ένα βιβλίο ανά αρχείο (.zip)": "One workbook per file (.zip)",
    "Εξαγωγή σε Excel": "Export to Excel",
    "Αποθήκευση στην πράξη": "Save to the act",
    "κείμενο/πίνακες": "text/tables",
    "εικόνα — χωρίς κείμενο": "image — no text",
    "χωρίς κείμενο": "no text",
    "Επιλέξτε από ποια θέλετε να εξαχθεί κείμενο.": "Select which files to extract text from.",
    "Εξαγωγή κειμένου": "Extract text",
    "εξαγωγή…": "extracting…",
    # file card statuses
    "βρέθηκαν πίνακες": "tables found",
    "σαρωμένο — χρειάζεται OCR": "scanned — needs OCR",
    "εικόνα — χρειάζεται OCR": "image — needs OCR",
    "Εκτέλεση OCR": "Run OCR",
    "αποστολή σελίδων στο Claude API — μπορεί να πάρει λίγο…":
        "sending pages to the Claude API — may take a moment…",
    'Το <code>ANTHROPIC_API_KEY</code> δεν είναι ορισμένο — το OCR είναι ανενεργό.':
        'The <code>ANTHROPIC_API_KEY</code> is not set — OCR is disabled.',
    "Λάθος ή αλλοιωμένο κείμενο (π.χ. ελληνικοί χαρακτήρες); Δοκιμάστε εξαγωγή μέσω Claude.":
        "Wrong or garbled text (e.g. Greek characters)? Try extraction via Claude.",
    "Επιλογή σελίδων (προαιρετικό)": "Select pages (optional)",
    "Εξαγωγή μέσω Claude": "Extract via Claude",
    'Το <code>ANTHROPIC_API_KEY</code> δεν είναι ορισμένο — η εξαγωγή μέσω Claude είναι ανενεργή.':
        'The <code>ANTHROPIC_API_KEY</code> is not set — extraction via Claude is disabled.',
    "ενωμένος": "stitched",
    "λίστα ειδών ✓": "item list ✓",
    "πιθανώς άσχετος": "likely irrelevant",
    # fulltext preview
    "↑ Χρήση αυτού του κειμένου": "↑ Use this text",
    "Το κείμενο μεταφέρεται στο πεδίο επάνω· πατήστε «Αποθήκευση κειμένου» για να αποθηκευτεί.":
        "The text is moved to the field above; press «Save text» to store it.",
    "Δεν εξήχθη κείμενο από τα επιλεγμένα αρχεία.": "No text was extracted from the selected files.",
    "Λάθος ή αλλοιωμένο κείμενο (π.χ. ελληνικοί χαρακτήρες); Δοκιμάστε ανάγνωση μέσω Claude (PDF/εικόνες).":
        "Wrong or garbled text (e.g. Greek characters)? Try reading via Claude (PDF/images).",
    "Ανάγνωση μέσω Claude": "Read via Claude",
    "Δίστηλη προβολή: κείμενο | πεδία": "Two-column view: text | fields",
    "Πεδία & κείμενο": "Fields & text",
    "Εξαγωγή κειμένου από αρχείο ή ΑΔΑΜ": "Extract text from a file or ΑΔΑΜ",
    "Ανεβάστε PDF/έγγραφο ή αντλήστε το με ΑΔΑΜ· το κείμενο εξάγεται και μπορείτε να το περάσετε στο πλήρες κείμενο πιο κάτω.":
        "Upload a PDF/document or fetch it by ΑΔΑΜ; the text is extracted and you can drop it into the full text below.",
    "ΑΔΑΜ / ΑΔΑ υπάρχουσας πράξης": "ΑΔΑΜ / ΑΔΑ of an existing act",
    "λήψη…": "downloading…",
    "Επικολλήστε, πληκτρολογήστε ή εξάγετε (από το εργαλείο πάνω) το πλήρες κείμενο· αποθηκεύεται μαζί με την πράξη, ώστε να συμπληρώσετε τα πεδία διαβάζοντάς το.":
        "Paste, type, or extract (from the tool above) the full text; it saves with the act so you can fill the fields by reading it.",
    "Εισαγόμενη πράξη — τα βασικά πεδία ανήκουν στην πηγή και ενημερώνονται από τις αυτόματες εισαγωγές. Το πλήρες κείμενο (αριστερά) είναι διαθέσιμο χωρίς ανάληψη. Για να επεξεργαστείτε τα βασικά πεδία, αναλάβετε την κυριότητα — μονόδρομη ενέργεια· έπειτα οι αυτόματες εισαγωγές δεν θα την τροποποιούν.":
        "Imported act — the core fields belong to the source and are updated by automatic imports. The full text (left) is available without ownership. To edit the core fields, take ownership — a one-way action; after that automatic imports won't touch it.",
    "Επικόλληση επιλογής σε πεδίο": "Paste selection into a field",
    "φιλτράρισμα πεδίου…": "filter field…",
    "➜ Κωδικοί CPV (αυτόματος εντοπισμός)": "➜ CPV codes (auto-detect)",
    "Δεν βρέθηκαν έγκυροι κωδικοί CPV στην επιλογή": "No valid CPV codes found in the selection",
    "CPV προστέθηκαν": "CPV added",
    "ήδη υπήρχαν": "already present",
    "Σφάλμα εντοπισμού CPV": "CPV detection failed",
    "Επικολλήστε εδώ το πλήρες κείμενο· αποθηκεύεται μαζί με την πράξη, ώστε να συμπληρώσετε τα πεδία διαβάζοντάς το.":
        "Paste the full text here — it's saved together with the act, so you can fill the fields by reading from it.",
    "+ Γραμμή": "+ Row",
    "Διαγραφή γραμμής": "Delete row",
    "Επεξεργαστείτε τα κελιά· η πρώτη γραμμή είναι η κεφαλίδα. Η «Αποθήκευση» ενημερώνει και την αναζήτηση.":
        "Edit the cells — the first row is the header. Saving also updates the search index.",
    "✓ Ανάγνωση μέσω τοπικού OCR (Tesseract).": "✓ Read via local OCR (Tesseract).",
    "Αν το κείμενο δεν είναι αρκετά καλό, δοκιμάστε ανάγνωση μέσω Claude (PDF/εικόνες).":
        "If the text isn't good enough, try reading via Claude (PDFs/images).",
    "Δωρεάν OCR (Tesseract)": "Free OCR (Tesseract)",
    "τοπικό OCR…": "local OCR…",
    "Ανάγνωση μέσω Claude (επί πληρωμή)": "Read via Claude (paid)",
    "Αν το κείμενο δεν είναι αρκετά καλό, δοκιμάστε ως έσχατη λύση την ανάγνωση μέσω Claude (επί πληρωμή· PDF/εικόνες).":
        "If the text still isn't good enough, as a last resort try reading via Claude (paid; PDFs/images).",
    "Αν το κείμενο είναι λάθος/αλλοιωμένο ή λείπει: δοκιμάστε πρώτα το δωρεάν τοπικό OCR (Tesseract) και μόνο ως έσχατη λύση το Claude (επί πληρωμή).":
        "If the text is wrong/garbled or missing: try the free local OCR (Tesseract) first, and Claude (paid) only as a last resort.",
    'Το <code>ANTHROPIC_API_KEY</code> δεν είναι ορισμένο — ανενεργό.':
        'The <code>ANTHROPIC_API_KEY</code> is not set — disabled.',
    "✓ Ανάγνωση μέσω Claude.": "✓ Read via Claude.",
    "Αποθήκευση κειμένου": "Save text",
    # extracted panel / row
    "Δεν υπάρχουν αποθηκευμένοι πίνακες για αυτή την πράξη. Χρησιμοποιήστε το εργαλείο":
        "No stored tables for this act. Use the",
    "για εξαγωγή και αποθήκευση.": "tool to extract and save.",
    "Δημοσιευμένοι εμφανίζονται στη δημόσια σελίδα της πράξης.":
        "Published ones appear on the act's public page.",
    "δημοσιευμένος": "published",
    "πρόχειρο": "draft",
    "Διαγραφή": "Delete",
    "Οριστική διαγραφή αυτού του πίνακα;": "Permanently delete this table?",
    # page picker / lightbox / assets
    "Όλες": "All",
    "Καμία": "None",
    "Εφαρμογή επιλογής": "Apply selection",
    "πρωτότυπο ↗": "original ↗",
    "‹ Προηγ.": "‹ Prev.",
    "Επόμ. ›": "Next ›",
    "να συμπεριληφθεί": "include",
    "✕ κλείσιμο": "✕ close",
    "προεπισκόπηση σελίδας": "page preview",
    "Εφαρμογή": "Apply",
}

# Procedure-family filter chips (proc.procurement_act.procedure_family — a
# normalised Greek vocabulary, distinct from the typeOfProcedure enum). Rendered
# via {{ p.label | t }} on the search/summary filter rails. Official EU/TED terms.
_PROC_FAMILY = {
    "Άλλο / Άγνωστο": "Other / Unknown",
    "Ανοιχτή διαδικασία": "Open procedure",
    "Ανταγωνιστική διαδικασία με διαπραγμάτευση": "Competitive procedure with negotiation",
    "Ανταγωνιστικός διάλογος": "Competitive dialogue",
    "Απευθείας ανάθεση": "Direct award",
    "Διαδικασία άρθρου 128": "Procedure under article 128",
    "Διαπραγμάτευση με προηγούμενη προκήρυξη": "Negotiated procedure with prior publication",
    "Διαπραγμάτευση χωρίς προηγούμενη δημοσίευση": "Negotiated procedure without prior publication",
    "Κλειστή διαδικασία": "Restricted procedure",
    "Συνοπτικός διαγωνισμός": "Simplified tender procedure",
}

# NUTS-2 region labels for the geography filter (NUTS_REGIONS in main.py — the
# 13 Greek περιφέρειες). Rendered via {{ r.label | t }} on the filter rails.
_NUTS_REGIONS = {
    "Αττική": "Attica",
    "Αν. Μακεδονία & Θράκη": "Eastern Macedonia & Thrace",
    "Κεντρική Μακεδονία": "Central Macedonia",
    "Δυτική Μακεδονία": "Western Macedonia",
    "Ήπειρος": "Epirus",
    "Θεσσαλία": "Thessaly",
    "Ιόνια Νησιά": "Ionian Islands",
    "Δυτική Ελλάδα": "Western Greece",
    "Στερεά Ελλάδα": "Central Greece",
    "Πελοπόννησος": "Peloponnese",
    "Βόρειο Αιγαίο": "North Aegean",
    "Νότιο Αιγαίο": "South Aegean",
    "Κρήτη": "Crete",
}

# Act-level CPV editor on imported acts (_panel_fields.html)
_CPV_EDIT = {
    "Οι κωδικοί προέρχονται από τα είδη της εισαγόμενης πράξης. Επεξεργαστείτε και αποθηκεύστε τους ως κωδικούς CPV επιπέδου πράξης.":
        "These codes come from the imported act's line items. Edit and save them as act-level CPV codes.",
}

_NUTS_FIELD = {
    "Αναζήτηση NUTS (κωδικός ή περιοχή)…": "Search NUTS (code or region)…",
    "π.χ. 11142": "e.g. 11142",
}

_DIAVGEIA = {
    "Διαύγεια": "Diavgeia",
    "Έγγραφο στη Διαύγεια": "Document on Diavgeia",
    "Η πράξη στη Διαύγεια": "The act on Diavgeia",
}

_ATTACHMENTS = {
    "Μεταφόρτωση συνημμένων": "Upload attachments",
    "Σύρετε εδώ αρχεία": "Drag files here",
    "Μεταφόρτωση & ευρετηρίαση": "Upload & index",
    "αναζητήσιμο": "searchable",
    "αρχεία μέσα στο zip": "files inside the zip",
    "Δεν υπάρχουν συνημμένα ακόμη.": "No attachments yet.",
    "Λήψη όλων (zip)": "Download all (zip)",
    "αρχεία": "files",
    "Διαγραφή συνημμένου;": "Delete this attachment?",
    "Τα συνημμένα είναι διαθέσιμα μόνο στο τοπικό περιβάλλον (η βάση παραγωγής δεν έχει διαθέσιμο χώρο).":
        "Attachments are available only on the local environment (the production DB has no spare space).",
    "Τα αρχεία αποθηκεύονται τοπικά· το κείμενό τους (και όσων περιέχονται σε zip) γίνεται αναζητήσιμο από την κύρια αναζήτηση.":
        "Files are stored locally; their text (and that of any zipped files) becomes searchable from the main search.",
}

_AUTH = {
    "Σύνδεση": "Sign in",
    "Εγγραφή": "Register",
    "Αποσύνδεση": "Sign out",
    "Όνομα χρήστη": "Username",
    "Κωδικός": "Password",
    "Η δημιουργία λογαριασμών γίνεται με πρόσκληση. Επικοινωνήστε με τον διαχειριστή για πρόσβαση.":
        "Accounts are created by invitation. Please contact the administrator for access.",
    "Κωδικός πρόσκλησης": "Invitation code",
    "Μη έγκυρος κωδικός πρόσκλησης.": "Invalid invitation code.",
    "Δεν έχετε λογαριασμό;": "Don't have an account?",
    "Έχετε ήδη λογαριασμό;": "Already have an account?",
    "Email (προαιρετικό)": "Email (optional)",
    "Επιβεβαίωση κωδικού": "Confirm password",
    "Δημιουργία λογαριασμού": "Create account",
    "Δημιουργήστε δωρεάν λογαριασμό για πλήρη πρόσβαση σε αναζήτηση, σελιδοποίηση και αναλυτικές πράξεις.":
        "Create a free account for full access to search, pagination and full act detail.",
    "Λάθος όνομα χρήστη ή κωδικός.": "Wrong username or password.",
    "Πολλές αποτυχημένες προσπάθειες — δοκιμάστε αργότερα.": "Too many failed attempts — try again later.",
    "Μη έγκυρο όνομα χρήστη (3–40 χαρακτήρες: γράμματα, αριθμοί, . _ - @).":
        "Invalid username (3–40 chars: letters, digits, . _ - @).",
    "Ο κωδικός πρέπει να έχει 8–200 χαρακτήρες.": "Password must be 8–200 characters.",
    "Οι κωδικοί δεν ταιριάζουν.": "Passwords don't match.",
    "Το όνομα χρήστη χρησιμοποιείται ήδη.": "Username already taken.",
    "Το όνομα χρήστη ή το email χρησιμοποιείται ήδη.": "Username or email already in use.",
    "Χρήστες": "Users",
    "Λογαριασμοί και ρόλοι. Admin = πλήρης πρόσβαση + διαχείριση· Customer = πλήρης ανάγνωση χωρίς επεξεργασία/διαχείριση.":
        "Accounts and roles. Admin = full access + management; Customer = full read, no editing/management.",
    "Λογαριασμοί": "Accounts",
    "Τελευταία σύνδεση": "Last login",
    "ενεργός": "active",
    "ανενεργός": "inactive",
    "Αλλαγή κατάστασης;": "Change status?",
    "απενεργοποίηση": "deactivate",
    "ενεργοποίηση": "activate",
    "νέος κωδικός": "new password",
    "Νέος χρήστης": "New user",
    "Δημιουργία": "Create",
    "Δεν μπορείτε να αφαιρέσετε τον τελευταίο διαχειριστή.": "You can't remove the last admin.",
    "Δεν μπορείτε να απενεργοποιήσετε τον εαυτό σας/τον τελευταίο διαχειριστή.":
        "You can't deactivate yourself / the last admin.",
    # Freemium teaser CTA (anonymous visitors).
    "Εγγραφείτε για να δείτε περισσότερα": "Register to see more",
    "Δημιουργήστε δωρεάν λογαριασμό για πλήρη πρόσβαση: πλοήγηση σε όλες τις σελίδες αποτελεσμάτων και πλήρη στοιχεία πράξεων, αναθετουσών και αναδόχων.":
        "Create a free account for full access: browse every page of results and see the full detail of acts, authorities and contractors.",
    "Έχετε ήδη λογαριασμό; Σύνδεση": "Already have an account? Sign in",
}

# Subscriptions / products (test & paid, statuses, admin grant UI, expired CTA).
_PRODUCTS = {
    # product names (proc.product.name)
    "Δοκιμαστικό": "Trial",
    "Συνδρομή": "Subscription",
    # derived customer statuses
    "Δοκιμαστής": "Trial user",
    "Συνδρομητής": "Subscriber",
    "Ληγμένος δοκιμαστής": "Expired trial",
    "Ληγμένη συνδρομή": "Expired subscription",
    "Χωρίς πρόσβαση": "No access",
    # admin users page
    "Πρόσβαση / Προϊόν": "Access / Product",
    "πλήρης (διαχειριστής)": "full (admin)",
    "λήγει": "expires",
    "ορισμός λήξης": "set expiry",
    "ημέρες": "days",
    "ανάθεση": "grant",
    "Προαιρετικό — παρακάμπτει την προεπιλογή": "Optional — overrides the default",
    "Προϊόντα": "Products",
    "Προεπιλεγμένη διάρκεια νέων αναθέσεων (ημέρες).":
        "Default length of new grants (days).",
    # admin flash errors
    "Δεν υπάρχει ενεργό προϊόν — αναθέστε πρώτα.":
        "No active product — grant one first.",
    "Μη έγκυρη ημερομηνία/διάρκεια.": "Invalid date/duration.",
    "Μη έγκυρη ανάθεση.": "Invalid grant.",
    "Μη έγκυρη διάρκεια.": "Invalid duration.",
    # expired-user teaser CTA
    "Η πρόσβασή σας έχει λήξει": "Your access has expired",
    "Η δοκιμαστική περίοδος ή η συνδρομή σας έληξε. Επικοινωνήστε με τον διαχειριστή για ανανέωση της πλήρους πρόσβασης.":
        "Your trial or subscription has ended. Contact the administrator to renew full access.",
}

# CRM (Phase 1): admins/customers split, customer profiles, segments.
_CRM = {
    "Διαχειριστές": "Administrators",
    "Λογαριασμοί διαχειριστών (πλήρης πρόσβαση + διαχείριση). Οι πελάτες διαχειρίζονται στο CRM.":
        "Administrator accounts (full access + management). Customers are managed in the CRM.",
    "Πελάτες": "Customers",
    "Πελάτης": "Customer",
    "Όλοι οι πελάτες με βάση την κατάσταση συνδρομής. Επιλέξτε πελάτη για λεπτομέρειες.":
        "All customers by subscription status. Select a customer for details.",
    "Κανένας πελάτης σε αυτή την κατηγορία.": "No customers in this category.",
    # segment tab labels
    "Συνδρομητές": "Subscribers",
    "Δοκιμαστές": "Trial users",
    "Ληγμένες συνδρομές": "Expired subscriptions",
    "Ληγμένοι δοκιμαστές": "Expired trials",
    # customer detail / profile fields
    "‹ πίσω στο CRM": "‹ back to CRM",
    "Στοιχεία πελάτη": "Customer details",
    "Ονοματεπώνυμο": "Full name",
    "Κινητό": "Mobile",
    "Θέση/Ρόλος": "Position/Role",
    "Εταιρεία": "Company",
    "Επωνυμία εταιρείας": "Company name",
    "Κλάδος": "Industry",
    "Πηγή/Προέλευση": "Lead source",
    "Σημειώσεις/Περιγραφή": "Notes/Description",
    "Αποθήκευση προφίλ": "Save profile",
    "Το email χρησιμοποιείται ήδη ή είναι μη έγκυρο.":
        "That email is already in use or invalid.",
    # subscription history + grant controls
    "Ιστορικό συνδρομών": "Subscription history",
    "Προϊόν": "Product",
    "Ανάθεση από": "Granted by",
    "ενεργή": "active",
    "έληξε": "expired",
    "εγγραφή": "signup",
    "Καμία συνδρομή.": "No subscriptions.",
    "Ανάθεση προϊόντος": "Grant product",
    "Τρέχουσα συνδρομή": "Current subscription",
    "παράταση": "extend",
}

# CRM Phase 2: notes / calls / tasks.
_CRM2 = {
    "Κλήσεις": "Calls",
    "Εργασίες": "Tasks",
    "Νέα σημείωση…": "New note…",
    "Προσθήκη": "Add",
    "Θέμα": "Subject",
    "Ακυρώθηκε": "Cancelled",
    "ενημέρωση": "update",
    "Έκβαση": "Outcome",
    "υπεύθυνος": "owner",
    "— ανάθεση —": "— assign —",
    "προθεσμία": "due",
    "προγρ.": "sched.",
    "Προγραμματισμός": "Schedule",
    "Καμία κλήση.": "No calls.",
    "Καμία εργασία.": "No tasks.",
    "(χωρίς θέμα)": "(no subject)",
    # call direction + statuses
    "Εξερχόμενη": "Outgoing",
    "Εισερχόμενη": "Incoming",
    "Προγραμματισμένη": "Planned",
    "Πραγματοποιήθηκε": "Held",
    "Δεν έγινε": "Not held",
    "Αναπάντητη": "No answer",
    # task statuses + deadline
    "Ανοιχτή": "Open",
    "Ολοκληρωμένη": "Done",
    "Προθεσμία": "Deadline",
    # ΑΦΜ -> contractor link
    "Συνδεδεμένος ανάδοχος": "Linked contractor",
    "Δεν βρέθηκε ανάδοχος με αυτό το ΑΦΜ.": "No contractor found with this VAT.",
}

# Admin console: grouped side-nav + CRM aggregate/search pages.
_CRMNAV = {
    "CRM": "CRM",
    "Διαχείριση Δεδομένων": "Data Management",
    "Διαχείριση Πύλης": "Portal Administration",
    "Υπεύθυνος": "Assignee",
    "Συντάκτης": "Author",
    "Προγρ.": "Sched.",
    "αποτελέσματα": "results",
    "εμφανίζονται τα πρώτα 200": "showing first 200",
    "Όλες οι κλήσεις σε όλους τους πελάτες.": "All calls across customers.",
    "Όλες οι εργασίες σε όλους τους πελάτες.": "All tasks across customers.",
    "Όλες οι σημειώσεις σε όλους τους πελάτες.": "All notes across customers.",
    "Αναζήτηση (όνομα / email / εταιρεία / ΑΦΜ)": "Search (name / email / company / VAT)",
    "Η προηγούμενη συνδρομή έληξε αυτόματα — μόνο ένα ενεργό προϊόν ανά πελάτη.":
        "The previous subscription was expired automatically — only one active product per customer.",
}

# Act Interconnection (condition-scored relating of related/duplicate acts).
_INTERCONNECT = {
    "Διασύνδεση Πράξεων": "Act interconnection",
    "Διασύνδεση": "Interconnection",
    "Συσχέτιση πράξεων του ίδιου διαγωνισμού με βάση βαθμολογία εμπιστοσύνης από ρυθμιζόμενες συνθήκες.":
        "Relate acts of the same tender by a confidence score from configurable conditions.",
    "✓ Δημιουργήθηκαν ομάδες:": "✓ Groups created:",
    "Εύρεση πράξης (ΑΔΑΜ ή τίτλος)": "Find an act (ΑΔΑΜ or title)",
    "Προεπισκόπηση σάρωσης": "Scan preview",
    "Ζεύγη με κοινό αναγνωριστικό + ίδια αναθέτουσα, βαθμολογία ≥ auto_min.":
        "Pairs sharing an identifier + the same authority, score ≥ auto_min.",
    "Σκορ": "Score",
    "Πράξη Α": "Act A", "Πράξη Β": "Act B", "Πράξη": "Act",
    "Δημιουργία ομάδων για όλα τα ζεύγη;": "Create groups for all pairs?",
    "Εφαρμογή σάρωσης": "Apply scan",
    "Δεν βρέθηκαν ζεύγη για αυτόματη ομαδοποίηση.": "No pairs found for auto-grouping.",
    "Ομάδες διασύνδεσης": "Interconnection groups",
    "Διπλότυπα": "Duplicates", "Δείγμα": "Sample",
    "Καμία ομάδα ακόμη.": "No groups yet.",
    "Συνθήκες & βάρη": "Conditions & weights", "βάρος": "weight",
    "Σάρωση": "Scan",
    "Εντοπισμός ζευγών υψηλής εμπιστοσύνης για αυτόματη ομαδοποίηση.":
        "Find high-confidence pairs to auto-group.",
    "Αριθμός πρωτοκόλλου": "Protocol number", "Αριθμός δέσμευσης": "Commitment number",
    "Ίδια αναθέτουσα": "Same authority",
    "‹ Διασύνδεση Πράξεων": "‹ Act interconnection",
    "αρ. σύμβασης": "contract no.", "αρ. πρωτ.": "protocol no.", "αρ. δέσμ.": "commitment no.",
    "ίδια αναθέτουσα": "same authority",
    "προβολή πράξης ›": "view act ›",
    "Υποψήφιες σχετικές πράξεις": "Candidate related acts",
    "Συνθήκες": "Conditions",
    "ήδη επίσημα συνδεδεμένη": "already officially linked", "σε ομάδα": "in group",
    "σύγκριση": "compare", "σχέτιση": "relate", "διπλότυπο": "duplicate",
    "Σήμανση της υποψήφιας ως διπλότυπο αυτής της πράξης;":
        "Mark the candidate as a duplicate of this act?",
    "Δεν βρέθηκαν υποψήφιες πράξεις (κοινό αναγνωριστικό) πάνω από το όριο. Μπορείτε να συγκρίνετε χειροκίνητα οποιαδήποτε πράξη.":
        "No candidate acts (shared identifier) above the threshold. You can compare any act manually.",
    "Ομάδα διασύνδεσης": "Interconnection group", "Ομάδα": "Group",
    "αφαίρεση από ομάδα": "remove from group",
    "Η πράξη δεν ανήκει σε ομάδα.": "This act is not in a group.",
    "Σύγκριση": "Comparison", "Σύγκριση πράξεων": "Compare acts",
    "Βαθμολογία εμπιστοσύνης": "Confidence score", "Σχέτιση πράξεων": "Relate acts",
    "Σήμανση της Β ως διπλότυπο της Α;": "Mark B as a duplicate of A?",
    "Β = διπλότυπο της Α": "B = duplicate of A",
    "ο κύκλος ζωής ενός διαγωνισμού": "one tender's lifecycle",
    "άρση διπλότυπου": "clear duplicate", "διπλότυπο →": "duplicate →",
    "Αφαίρεση από την ομάδα;": "Remove from the group?", "αφαίρεση": "remove",
    "Αφαίρεση αυτής της πράξης από την ομάδα;": "Remove this act from the group?",
    "Διασυνδεδεμένες πράξεις": "Interconnected acts",
    "σχετικές ›": "related ›",
    # TED source
    "Προκήρυξη στο TED": "Notice on TED",
    "Η προκήρυξη στην πύλη TED": "The notice on the TED portal",
    # TED admin Data-Collection panel
    "Τρίτη πηγή · ted.europa.eu": "Third source · ted.europa.eu",
    "Άντληση προκηρύξεων από το TED (EU) με βάση την ημερομηνία δημοσίευσης. Μετά την άντληση, εντάσσονται αυτόματα στην εφαρμογή.":
        "Fetch TED (EU) notices by publication date. After harvest they are projected into the app automatically.",
    "χώρα αγοραστή": "buyer country",
    "για κάθε προκήρυξη, αντλεί το XML από το TED και αποθηκεύει τη σύνοψη και το πλήρες κείμενο.":
        "for each notice, fetches the TED XML and stores the summary and full text.",
    "Πιο αργό & με πολλές λήψεις από το TED. Συμπληρώνει μόνο όσες δεν έχουν ήδη κείμενο.":
        "Slower & many downloads from TED. Only fills notices without text yet.",
    "Εκκίνηση συλλογής TED": "Start TED collection",
    "Για προκηρύξεις TED που <strong>υπάρχουν ήδη</strong> στη βάση αλλά δεν έχουν πλήρες κείμενο. Αντλεί το XML από το TED & αποθηκεύει το κείμενο σε παρτίδες (resumable — μπορείτε να το ξανατρέξετε για να συνεχίσει).":
        "For TED notices <strong>already</strong> in the database but without full text. Fetches the TED XML & stores the text in batches (resumable — re-run to continue).",
    "Πόσες προκηρύξεις θα επιχειρηθούν αυτή τη φορά.": "How many notices to attempt this run.",
    "Εκκίνηση εξαγωγής κειμένου TED": "Start TED text extraction",
    "Κάλυψη TED": "TED coverage",
}


# User manual / help page (beta_help.html). Greek-source keys -> English.
_HELP = {
    "Προκήρυξη": "Notice",
    "Σύμβαση": "Contract",
    "Εντολή Πληρωμής": "Payment order",
    "Αναθέτουσες αρχές": "Contracting authorities",
    "Ενοποίηση Αναδόχων / Αρχών": "Merge Contractors / Authorities",
    "Τι καλύπτει η αναζήτηση": "What search covers",
    "Η μία μπάρα αναζήτησης ταιριάζει ταυτόχρονα τον τίτλο, το πλήρες κείμενο, τους δημοσιευμένους εξαχθέντες πίνακες και τα ανεβασμένα συνημμένα μιας πράξης (όπου είναι ενεργά). Έτσι μια λέξη που υπάρχει μόνο μέσα σε έναν πίνακα ή ένα συνημμένο εξακολουθεί να βρίσκει την πράξη.":
        "The single search box matches an act's title, full text, published extracted tables, and uploaded attachments (where enabled) — all at once. So a word that appears only inside a table or an attachment still finds the act.",
    "Μια φιλική ταξινόμηση δύο επιπέδων, παραγόμενη αυτόματα από τους κωδικούς CPV των ειδών μιας πράξης — επιλέξτε ολόκληρη κατηγορία ή υποκατηγορία χωρίς να ξέρετε κωδικούς CPV.":
        "A friendly two-level taxonomy generated automatically from an act's line-item CPV codes — pick a whole category or a subcategory without knowing CPV codes.",
    "Παράγονται από τους κωδικούς CPV της πράξης· κάθε κατηγορία/υποκατηγορία οδηγεί σε αναζήτηση όλων των σχετικών πράξεων.":
        "Derived from the act's CPV codes; each category/subcategory links to a search for all related acts.",
    "Από κείμενο σε πεδία": "From text to fields",
    "Στον επεξεργαστή πράξης, επιλέγοντας κείμενο μέσα στο πλήρες κείμενο εμφανίζεται ένα μικρό μενού: διαλέξτε ένα δομημένο πεδίο και η επιλογή αντιγράφεται αυτόματα εκεί — γρήγορη συμπλήρωση χωρίς αντιγραφή-επικόλληση.":
        "In the act editor, selecting text inside the full text pops up a small menu: choose a structured field and the selection is copied straight into it — quick fill-in without copy-paste.",
    "Αναζητήσιμο": "Searchable",
    "Μόλις δημοσιευτεί μια εξαγωγή, το περιεχόμενο των κελιών της γίνεται αυτόματα αναζητήσιμο: μια λέξη-κλειδί στην κύρια αναζήτηση βρίσκει την πράξη μέσω του πίνακα. Το ίδιο ισχύει για τα ανεβασμένα συνημμένα μιας πράξης (όπου η δυνατότητα είναι ενεργή) — το κείμενό τους ευρετηριάζεται και είναι αναζητήσιμο.":
        "Once an extraction is published, its cell content becomes automatically searchable: a keyword in the main search finds the act via the table. The same holds for an act's uploaded attachments (where the feature is enabled) — their text is indexed and searchable.",
    "Εγχειρίδιο χρήσης": "User manual",
    "Οδηγός του Εξερευνητή": "The Explorer guide",
    "Τι κάνει η εφαρμογή και πώς να τη χρησιμοποιήσετε — αναζήτηση πράξεων, ανάγνωση ενός διαγωνισμού από άκρη σε άκρη, παρακολούθηση αρχών και αναδόχων, και η κονσόλα διαχείρισης.": "What the app does and how to use it — searching acts, reading a tender end to end, tracking authorities and contractors, and the admin console.",
    "Περιεχόμενα": "Contents",
    "Τι είναι": "What it is",
    "Λογαριασμοί & πρόσβαση": "Accounts & access",
    "Πλοήγηση": "Navigation",
    "Αναζήτηση πράξεων": "Searching acts",
    "Η σελίδα πράξης": "The act page",
    "Αρχές & ανάδοχοι": "Authorities & contractors",
    "Σύνοψη & στατιστικά": "Summary & statistics",
    "Πηγές δεδομένων": "Data sources",
    "Διαχείριση: συλλογή": "Admin: collection",
    "Διαχείριση: επιμέλεια": "Admin: curation",
    "Διαχείριση: διασύνδεση": "Admin: interconnection",
    "Διαχείριση: χρήστες & CRM": "Admin: users & CRM",
    "Γλωσσάρι": "Glossary",
    "Προσανατολισμός": "Orientation",
    "Ένα παράθυρο αναζήτησης στις ελληνικές δημόσιες συμβάσεις. Συγκεντρώνει επίσημες πράξεις — προκηρύξεις, αποτελέσματα, συμβάσεις και εντολές πληρωμής — σε ένα σημείο και σας επιτρέπει να δείτε ποιος αγοράζει τι, από ποιον και με πόσα.": "A search window onto Greek public procurement. It gathers official acts — notices, results, contracts and payment orders — in one place and lets you see who buys what, from whom, and for how much.",
    "Κάθε διαδικασία περνά από έναν κύκλο ζωής πράξεων. Η εφαρμογή τις συλλέγει από τρεις επίσημες πηγές, τις συνδέει σε ένα ενιαίο χρονολόγιο ανά διαδικασία, και τις εμπλουτίζει με στοιχεία επιχειρηματικού μητρώου και καταλόγου φορέων. Όλα είναι δίγλωσσα — ελληνικά και αγγλικά — και κάθε σελίδα με λίστα ή πίνακα απαντά και σε JSON αν το ζητήσετε.": "Every procedure passes through a lifecycle of acts. The app collects them from three official sources, links them into a single timeline per procedure, and enriches them with business-registry and org-directory data. Everything is bilingual — Greek and English — and every page with a list or table also answers as JSON if you ask.",
    "Αναζήτηση & φίλτρα": "Search & filters",
    "Βρείτε πράξεις ανά λέξη-κλειδί, κατηγορία (CPV), περιοχή (NUTS), αξία, ημερομηνία, αρχή ή ανάδοχο.": "Find acts by keyword, category (CPV), region (NUTS), value, date, authority or contractor.",
    "Ανάγνωση διαγωνισμού": "Reading a tender",
    "Μία σελίδα ανά πράξη: είδη, πλήρες κείμενο, συνημμένα και όλος ο κύκλος ζωής.": "One page per act: items, full text, attachments and the whole lifecycle.",
    "Παρακολούθηση μερών": "Following the parties",
    "Προφίλ για κάθε αναθέτουσα αρχή και κάθε ανάδοχο, με ιστορικό και σύνολα.": "A profile for each contracting authority and each contractor, with history and totals.",
    "Η συνολική εικόνα": "The big picture",
    "Συγκεντρωτικές αναλύσεις και στατιστικά πάνω σε οποιαδήποτε φιλτραρισμένη τομή.": "Aggregated breakdowns and statistics over any filtered slice.",
    "Ποιος τι μπορεί": "Who can do what",
    "Η εφαρμογή έχει τρία επίπεδα. Το επίπεδό σας υπολογίζεται σε κάθε αίτημα, ώστε μια αλλαγή (νέα συνδρομή, αλλαγή ρόλου, απενεργοποίηση) να ισχύει άμεσα — χωρίς να ξανασυνδεθείτε.": "The app has three tiers. Your tier is resolved on every request, so a change (a new subscription, a role change, a deactivation) takes effect immediately — no need to log back in.",
    "Επισκέπτης": "Visitor",
    "Ανώνυμος, ή συνδρομητής με ληγμένη πρόσβαση. Μπορεί να περιηγηθεί και να φιλτράρει, αλλά βλέπει μόνο μια προεπισκόπηση: την πρώτη σελίδα αποτελεσμάτων και περιορισμένη λεπτομέρεια.": "Anonymous, or a subscriber whose access has lapsed. Can browse and filter, but sees only a preview: the first page of results and reduced detail.",
    "Εγγεγραμμένος πελάτης με ενεργή συνδρομή. Πλήρης πρόσβαση ανάγνωσης — όλες οι σελίδες, πλήρης λεπτομέρεια, πλήρες κείμενο και λήψεις.": "A registered customer with an active subscription. Full read access — all pages, complete detail, full text and downloads.",
    "Διαχειριστής": "Administrator",
    "Ό,τι βλέπει ο συνδρομητής, συν όλη την κονσόλα": "Everything a subscriber sees, plus the entire console",
    "συλλογή, επιμέλεια, χρήστες και CRM.": "collection, curation, users and CRM.",
    "Σύνδεση & εγγραφή": "Signing in & registering",
    "Η": "The",
    "δημιουργεί λογαριασμό πελάτη.": "creates a customer account.",
    "δημιουργεί λογαριασμό πελάτη — αλλά η δημόσια εγγραφή είναι κλειστή από προεπιλογή (ρυθμιζόμενη: ανοιχτή / με πρόσκληση / κλειστή)· διαφορετικά οι διαχειριστές δημιουργούν λογαριασμούς.":
        "creates a customer account — but public sign-up is closed by default (configurable: open / invite / closed); otherwise admins create accounts.",
    "Σύνδεση / αποσύνδεση": "Sign in / out",
    "στο": "at",
    "και": "and",
    "Το όνομα και η κατάσταση πρόσβασής σας εμφανίζονται πάνω δεξιά μόλις συνδεθείτε.": "Your name and access status appear at the top-right once you sign in.",
    "Ο κανόνας προεπισκόπησης": "The preview rule",
    "Οι επισκέπτες μπορούν να εκτελέσουν κάθε αναζήτηση και να ρυθμίσουν κάθε φίλτρο — απλώς δεν μπορούν να προχωρήσουν πέρα από την πρώτη οθόνη αποτελεσμάτων, και η λεπτομέρεια πράξης κρύβει τα βαθύτερα πεδία. Είναι προεπισκόπηση, όχι τοίχος. Μια ενεργή συνδρομή (ή λογαριασμός διαχειριστή) αφαιρεί το όριο παντού.": "Visitors can run any search and set any filter — they just can't go past the first screen of results, and act detail hides the deeper fields. It's a preview, not a wall. An active subscription (or an admin account) removes the limit everywhere.",
    "Περιοχή διαχείρισης": "The admin area",
    "Οτιδήποτε κάτω από": "Anything under",
    "απαιτεί λογαριασμό διαχειριστή. Οι λογαριασμοί διαχειριστή δημιουργούνται από άλλον διαχειριστή, όχι μέσω δημόσιας εγγραφής.": "requires an admin account. Admin accounts are created by another admin, not through public registration.",
    "Η γραμμή πλοήγησης": "The navigation bar",
    "Έξι προορισμοί βρίσκονται στην κεφαλίδα, ίδιοι σε κάθε σελίδα. Ο τελευταίος εμφανίζεται μόνο σε διαχειριστές.": "Six destinations sit in the header, the same on every page. The last appears only for admins.",
    "Ετικέτα": "Label",
    "Οδηγεί στο": "Goes to",
    "Τι θα βρείτε": "What you'll find",
    "Η κύρια σελίδα αναζήτησης — η προεπιλεγμένη προβολή.": "The main search page — the default view.",
    "Κατάλογος αναθετουσών αρχών (οι αγοραστές).": "Directory of contracting authorities (the buyers).",
    "Κατάλογος προμηθευτών / αναδόχων.": "Directory of suppliers / contractors.",
    "Συγκεντρωτική ανάλυση της τρέχουσας φιλτραρισμένης τομής.": "Aggregated breakdown of the current filtered slice.",
    "Γραφήματα & σύνολα για όλο το σώμα δεδομένων.": "Charts & totals over the whole corpus.",
    "Η κονσόλα διαχείρισης (συλλογή, επιμέλεια, χρήστες, CRM).": "The admin console (collection, curation, users, CRM).",
    "Ο διακόπτης ΕΛ / EN (πάνω δεξιά) εναλλάσσει όλο το περιβάλλον μεταξύ ελληνικών και αγγλικών και θυμάται την επιλογή σας. Οι ετικέτες δεδομένων — τύποι πράξεων, είδη διαδικασίας, ονόματα CPV και NUTS — μεταφράζονται μαζί.": "The EL / EN switch (top-right) flips the whole interface between Greek and English and remembers your choice. Data labels — act types, procedure kinds, CPV and NUTS names — are translated too.",
    "Ο βασικός κύκλος": "The core loop",
    "Η αρχική σελίδα είναι ένα πλαίσιο αναζήτησης πάνω από μια στήλη φίλτρων πάνω από μια λίστα αποτελεσμάτων. Πληκτρολογήστε, περιορίστε με τα φίλτρα αριστερά, και τα αποτελέσματα ενημερώνονται επιτόπου.": "The home page is a search box over a column of filters over a list of results. Type, narrow with the filters on the left, and results update in place.",
    "Αναζήτηση πλήρους κειμένου & λέξεων-κλειδιών": "Full-text & keyword search",
    "Το πλαίσιο αναζήτησης ταιριάζει τον τίτλο και τα μέρη μιας πράξης. Όπου μια πράξη έχει εξαχθέν": "The search box matches an act's title and parties. Where an act has extracted",
    "πλήρες κείμενο": "full text",
    "οι όροι σας αναζητούν και μέσα σε αυτό — ώστε μια φράση κρυμμένη σε ένα έγγραφο διαγωνισμού να είναι εντοπίσιμη.": "your terms search inside it too — so a phrase buried in a tender document is findable.",
    "Φίλτρο": "Filter",
    "Λειτουργία": "Function",
    "Περιορισμός σε ΚΗΜΔΗΣ, Διαύγεια ή TED.": "Restrict to KHMDHS, Diavgeia or TED.",
    "Κατηγορία (CPV)": "Category (CPV)",
    "Κοινό Λεξιλόγιο Δημοσίων Συμβάσεων. Πληκτρολογήστε για υποδείξεις, ή ανοίξτε τον περιηγητή για να εξερευνήσετε το δέντρο CPV. Ταίριασμα προθέματος.": "Common Procurement Vocabulary. Type for suggestions, or open the browser to explore the CPV tree. Prefix matching.",
    "Περιοχή (NUTS)": "Region (NUTS)",
    "Οι 13 ελληνικές περιφέρειες συν όλη η χώρα. Ταίριασμα προθέματος, ώστε μια περιφέρεια να περιλαμβάνει τις υποπεριοχές της.": "The 13 Greek regions plus the whole country. Prefix matching, so a region includes its sub-areas.",
    "Προμήθειες, Έργα, Υπηρεσίες, Μελέτες και συναφή.": "Supplies, Works, Services, Studies and related.",
    "Εύρος αξίας": "Value range",
    "Ελάχιστη / μέγιστη αξία σύμβασης.": "Minimum / maximum contract value.",
    "Φιλτράρει στην ημερομηνία δημοσίευσης.": "Filters on the publication date.",
    "Αρχή / Ανάδοχος": "Authority / Contractor",
    "Καρφιτσώστε τη λίστα σε έναν αγοραστή ή έναν προμηθευτή.": "Pin the list to one buyer or one supplier.",
    "Τα αποτελέσματα ταξινομούνται εκ νέου, και κάθε κάρτα οδηγεί στη σελίδα της πράξης. Το ίδιο ερώτημα λειτουργεί ως δεδομένα: στείλτε": "Results can be re-sorted, and each card links to the act page. The same query works as data: send",
    "σε οποιαδήποτε διεύθυνση και παίρνετε JSON αντί για HTML.": "to any URL and you get JSON instead of HTML.",
    "Προβολή σύνοψης": "Summary view",
    "παίρνει τα ίδια ακριβώς φίλτρα και, αντί για λίστα, δείχνει τη συγκεντρωτική ανάλυση αυτής της τομής — σύνολα και κατανομές. Φιλτράρετε στην κύρια σελίδα, μετά περάστε στη Σύνοψη για να δείτε το σχήμα.": "takes the exact same filters and, instead of a list, shows the aggregated breakdown of that slice — totals and distributions. Filter on the main page, then switch to Summary to see the shape.",
    "Συμβουλή": "Tip",
    "CPV και NUTS χρησιμοποιούν ταίριασμα προθέματος. Επιλέξτε έναν ευρύ κωδικό για να ρίξετε πλατύ δίχτυ, και στενέψτε μόνο αν χρειαστεί.": "CPV and NUTS use prefix matching. Pick a broad code to cast a wide net, and narrow only if needed.",
    "Ένας διαγωνισμός, πλήρης": "One tender, in full",
    "Κάθε πράξη έχει σελίδα στο": "Every act has a page at",
    "όπου ΑΔΑΜ είναι ο μοναδικός αριθμός αναφοράς της πράξης. Είναι η πιο πλούσια προβολή της εφαρμογής.": "where ADAM is the act's unique reference number. It's the richest view in the app.",
    "Κεφαλίδα": "Header",
    "Τύπος, πηγή, τίτλος, ο αγοραστής, αξία προϋπολογισμού και ημ/νία δημοσίευσης.": "Type, source, title, the buyer, budget value and publication date.",
    "Βασικά στοιχεία": "Key facts",
    "ΑΔΑΜ, είδος διαδικασίας & κριτηρίου, σημαίες συμφωνίας-πλαισίου, ημ/νία υπογραφής.": "ADAM, procedure & criterion type, framework-agreement flags, signing date.",
    "Είδη / αντικείμενο": "Items / object",
    "Τα είδη με τους κωδικούς CPV, ποσότητες και αξίες τους.": "The line items with their CPV codes, quantities and values.",
    "Κύκλος ζωής": "Lifecycle",
    "Η συνδεδεμένη αλυσίδα πράξεων της διαδικασίας — αίτημα → προκήρυξη → αποτέλεσμα → σύμβαση → πληρωμή.": "The linked chain of acts for the procedure — request → notice → result → contract → payment.",
    "Το εξαχθέν κείμενο, με δυνατότητα αναζήτησης, σε πάνελ.": "The extracted text, searchable, in a panel.",
    "Σύνδεσμος προς το επίσημο έγγραφο στην πύλη προέλευσης.": "A link to the official document on its origin portal.",
    "Ο ΑΔΑΜ είναι το καθολικό κλειδί: έτσι βρίσκουν η προκήρυξη, το αποτέλεσμα, η σύμβαση και η πληρωμή του ίδιου διαγωνισμού η μία την άλλη, και πάνω του χτίζεται κάθε διεύθυνση": "The ADAM is the universal key: it's how the notice, result, contract and payment for the same tender find each other, and every",
    "Οι δύο πλευρές": "The two sides",
    "Πέρα από τις μεμονωμένες πράξεις, η εφαρμογή κρατά προφίλ για κάθε μέρος — τους δημόσιους φορείς που αγοράζουν και τις επιχειρήσεις που προμηθεύουν.": "Beyond individual acts, the app keeps a profile for each party — the public bodies that buy and the businesses that supply.",
    "Δείτε τους αγοραστές στο": "See the buyers at",
    "το καθένα ανοίγει προφίλ στο": "each opens a profile at",
    "με τη δραστηριότητα και τα σύνολά του. Οι αρχές εμπλουτίζονται από τον κατάλογο φορέων της Διαύγειας.": "with its activity and totals. Authorities are enriched from the Diavgeia organisation directory.",
    "Δείτε προμηθευτές στο": "See suppliers at",
    "με κλειδί το ΑΦΜ· κάθε προφίλ στο": "keyed by Tax ID; each profile at",
    "δείχνει τις συμβάσεις που κέρδισαν. Οι εγγραφές μπορούν να εμπλουτιστούν κατ' απαίτηση από το μητρώο ΓΕΜΗ μέσω ΑΦΜ.": "shows the contracts they won. Records can be enriched on demand from the GEMI registry by Tax ID.",
    "Ο ίδιος πραγματικός φορέας μπορεί να εμφανίζεται με ελαφρώς διαφορετικά ονόματα. Οι διαχειριστές μπορούν να ενοποιήσουν διπλότυπες αρχές ή αναδόχους σε μία εγγραφή· οι ενοποιημένες φέρουν σήμανση.": "The same real-world body can appear under slightly different names. Admins can merge duplicate authorities or contractors into one record; merged ones carry a badge.",
    "Η μεγάλη εικόνα": "The big picture",
    "Δύο προβολές δίνουν απόσταση από τις μεμονωμένες πράξεις.": "Two views step back from individual acts.",
    "η συγκεντρωτική ανάλυση της τρέχουσας φιλτραρισμένης τομής σας.": "the aggregated breakdown of your current filtered slice.",
    "γραφήματα και σύνολα για όλο το σώμα, από προϋπολογισμένες όψεις για ταχύτητα.": "charts and totals over the whole corpus, from precomputed views for speed.",
    "Υγιεινή δεδομένων": "Data hygiene",
    "Ορισμένες πράξεις εξαιρούνται σκόπιμα από τα στατιστικά — εγγραφές που έχουν επισημανθεί ως σφάλματα δεδομένων (το ΚΗΜΔΗΣ ενίοτε διογκώνει μια αξία ~1000×) και μη συγκρίσιμες πηγές. Έτσι τα σύνολα περιγράφουν μια καθαρισμένη, συγκρίσιμη τομή.": "Some acts are deliberately excluded from the statistics — records flagged as data errors (KHMDHS occasionally inflates a value ~1000×) and non-comparable sources. So the totals describe a cleaned, comparable slice.",
    "Τρεις επίσημες πηγές τροφοδοτούν την εφαρμογή. Κάθε πράξη δείχνει σήμα πηγής και μπορείτε να φιλτράρετε ανά πηγή. Καθεμία συνδέει πίσω στο έγγραφο προέλευσής της.": "Three official sources feed the app. Every act shows a source badge and you can filter by source. Each links back to its origin document.",
    "Συνεισφέρει": "Contributes",
    "Ο κορμός: αιτήματα, προκηρύξεις, αποτελέσματα, συμβάσεις και εντολές πληρωμής από το εθνικό μητρώο.": "The backbone: requests, notices, results, contracts and payment orders from the national registry.",
    "Από έγγραφα συνημμένων (προαιρετικά).": "From attachment documents (opt-in).",
    "Αποφάσεις διαφάνειας (με κλειδί ΑΔΑ), συν τον κατάλογο φορέων για τον εμπλουτισμό των αρχών.": "Transparency decisions (ADA-keyed), plus the organisation directory used to enrich authorities.",
    "Από έγγραφα αποφάσεων (προαιρετικά).": "From decision documents (opt-in).",
    "Προκηρύξεις επιπέδου ΕΕ για Έλληνες αγοραστές, μαζί με αποτελέσματα (νικητές και ανά τμήμα αξίες).": "EU-level notices for Greek buyers, with results (winners and per-lot values).",
    "Αποδίδεται από το eForms/UBL XML — σύνοψη, τμήματα, αποτελέσματα, οργανισμοί, με ονόματα CPV/NUTS στα ελληνικά.": "Rendered from the eForms/UBL XML — summary, lots, results, organisations, with CPV/NUTS names in Greek.",
    "Το πλήρες κείμενο συλλέγεται προαιρετικά — δεν κατεβαίνει σε κάθε εισαγωγή, μόνο όταν ενεργοποιηθεί ρητά — και μόλις υπάρξει τροφοδοτεί και το πάνελ και την αναζήτηση.": "Full text is collected opt-in — it isn't fetched on every import, only when explicitly switched on — and once present it feeds both the panel and search.",
    "Ένα γράφημα": "One graph",
    "Ανεξαρτήτως πηγής, οι πράξεις συνδέονται με τον αριθμό αναφοράς τους σε ένα ενιαίο γράφημα, ώστε ένα αποτέλεσμα TED και μια σύμβαση ΚΗΜΔΗΣ για τον ίδιο διαγωνισμό να εμφανίζονται μαζί στο χρονολόγιο.": "Regardless of source, acts are linked by their reference number into a single graph, so a TED result and a KHMDHS contract for the same tender appear together on the timeline.",
    "Κονσόλα διαχείρισης": "Admin console",
    "Η κονσόλα": "The console",
    "έχει ομαδοποιημένη πλαϊνή πλοήγηση: CRM,": "has a grouped side-nav: CRM,",
    "Η συλλογή βρίσκεται στο": "Collection lives at",
    "Κάθε πηγή έχει το δικό της πάνελ με το ίδιο σχήμα: εύρος ημερομηνιών (η TED παίρνει και χώρα αγοραστή, προεπιλογή GRC), προαιρετικά ο διακόπτης πλήρους κειμένου, και εκκίνηση. Η εργασία τρέχει ως ανεξάρτητη διεργασία παρασκηνίου, ώστε η σελίδα να επιστρέφει αμέσως και η δουλειά να επιβιώνει επανεκκίνησης. Τρέχει μία εργασία τη φορά.": "Each source has its own panel with the same shape: a date range (TED also takes a buyer country, default GRC), optionally the full-text switch, and start. The job runs as a detached background process, so the page returns at once and the work survives a restart. One job runs at a time.",
    "Ανοίξτε τη": "Open",
    "και επιλέξτε το πάνελ της πηγής.": "and choose the source panel.",
    "Ορίστε εύρος ημερομηνιών (και χώρα, για TED)· τσεκάρετε πλήρες κείμενο αν το θέλετε.": "Set a date range (and country, for TED); tick full text if you want it.",
    "Εκκινήστε — εμφανίζεται μια γραμμή εργασίας με ζωντανή κατάσταση.": "Start it — a job row appears with live status.",
    "Ανοίξτε την εργασία για πρόοδο ανά παράθυρο και το αρχείο καταγραφής· ακυρώστε αν χρειαστεί.": "Open the job for window-by-window progress and the log; cancel if needed.",
    "Ένα ξεχωριστό μαζικό πέρασμα πλήρους κειμένου συμπληρώνει κείμενο για ήδη εισηγμένες πράξεις.": "A separate mass full-text pass fills in text for already-imported acts.",
    "Διακόπτης κειμένου": "Full-text switch",
    "Το πλήρες κείμενο κατεβαίνει μόνο όταν το ζητήσετε. Ενεργοποιήστε το σε μια συλλογή, ή τρέξτε το μαζικό πέρασμα αργότερα για να εμπλουτίσετε υπάρχουσες εγγραφές χωρίς επανεισαγωγή.": "Full text is fetched only when you ask for it. Turn it on for a collection, or run the mass pass later to enrich existing records without re-importing.",
    "Πέρα από την εισαγωγή, οι διαχειριστές μπορούν να επεξεργαστούν και να εμπλουτίσουν τα δεδομένα. Η διαχείριση πράξεων βρίσκεται στο": "Beyond importing, admins can edit and enrich the data. Act management lives at",
    "με τα ίδια φίλτρα πολλαπλής επιλογής όπως η δημόσια αναζήτηση.": "with the same multi-select filters as the public search.",
    "Δημιουργία & επεξεργασία": "Create & edit",
    "Συντάξτε ή τροποποιήστε μια πράξη — πεδία, είδη, κείμενο, σημειώσεις — σε επεξεργαστή με καρτέλες.": "Author or amend an act — fields, items, text, notes — in a tabbed editor.",
    "Σημειώσεις & σημαίες": "Notes & flags",
    "Εσωτερικές σημειώσεις και σημαίες σε πράξεις·": "Internal notes and flags on acts;",
    "τις παραθέτει.": "lists them.",
    "Ανέβασμα και διαχείριση εγγράφων σε μια πράξη (όπου είναι ενεργό).": "Upload and manage documents on an act (where enabled).",
    "Εργαλεία κειμένου": "Text tools",
    "Λήψη, επικόλληση ή εξαγωγή πλήρους κειμένου ανά πράξη — με OCR για σαρωμένα.": "Fetch, paste or extract full text per act — with OCR for scanned files.",
    "Ανάληψη": "Take ownership",
    "Αναλάβετε μια εισηγμένη πράξη για χειροκίνητη επιμέλεια, ώστε οι αλλαγές να καταγράφονται.": "Claim an imported act for manual curation, so changes are tracked.",
    "Ενοποίηση μερών": "Merge parties",
    "συγχωνεύστε διπλότυπα σε μία εγγραφή.": "fold duplicates into one record.",
    "Οι ενοποιήσεις είναι αναστρέψιμες, και η σχέση παραμένει ορατή μέσω σήμανσης.": "Merges are reversible, and the relationship stays visible via a badge.",
    "εντοπίζει πράξεις που είναι συναφείς ή διπλότυπες μεταξύ τους — ο ίδιος διαγωνισμός σε διαφορετικές πηγές, ή επαναλαμβανόμενες καταχωρήσεις — και επιτρέπει την επιβεβαίωση ή απόρριψη του δεσμού.": "finds acts that are related or duplicates of each other — the same tender across sources, or repeated filings — and lets you confirm or dismiss the link.",
    "τρέχει τη μηχανή αντιστοίχισης και αναδεικνύει υποψήφια ζεύγη, βαθμολογημένα κατά ισχύ.": "runs the matching engine and surfaces candidate pairs, scored by strength.",
    "Συσχέτιση / διπλότυπο": "Relate / mark duplicate",
    "επιβεβαιώνει σύνδεση·": "confirms a connection;",
    "την αναιρεί.": "undoes it.",
    "Κανόνες": "Rules",
    "ρυθμίζουν τις συνθήκες της βαθμολόγησης.": "tune the conditions of the scoring.",
    "Στη σελίδα μιας πράξης, ένα πάνελ δείχνει με τι είναι συνδεδεμένη.": "On an act's page, a panel shows what it's connected to.",
    "Στο": "At",
    "οι διαχειριστές διαχειρίζονται άλλους διαχειριστές — δημιουργία, ρόλοι, ενεργοποίηση/απενεργοποίηση, επαναφορά κωδικών. (Οι πελάτες βρίσκονται στο CRM.)": "admins manage other admins — create, roles, activate/deactivate, reset passwords. (Customers live in the CRM.)",
    "Πελάτες & συνδρομές — CRM": "Customers & subscriptions — CRM",
    "Το CRM": "The CRM",
    "είναι όπου ζουν οι λογαριασμοί πελατών, καθένας με προφίλ. Από έναν πελάτη μπορείτε να:": "is where customer accounts live, each with a profile. From a customer you can:",
    "Χορηγήσετε προϊόν / συνδρομή και να ορίσετε λήξη — αυτό εναλλάσσει τον πελάτη μεταξύ προεπισκόπησης και πλήρους πρόσβασης. Ένα προϊόν ενεργό τη φορά· μια νέα χορήγηση αντικαθιστά την τρέχουσα.": "Grant a product / subscription and set an expiry — this flips the customer between preview and full access. One product active at a time; a new grant supersedes the current one.",
    "Καταγράψετε δραστηριότητα — σημειώσεις, κλήσεις (με κατάσταση) και εργασίες (με κατάσταση).": "Log activity — notes, calls (with status) and tasks (with status).",
    "Προβολές μεταξύ πελατών συγκεντρώνουν όλες τις": "Cross-customer views collect all the",
    "σε ένα σημείο, με αναζήτηση πελάτη.": "in one place, with a customer search.",
    "Πρόσβαση = συνδρομή": "Access = subscription",
    "Το επίπεδο πρόσβασης ενός πελάτη καθορίζεται πλήρως από την τρέχουσα συνδρομή του. Χορηγήστε μία για πλήρη πρόσβαση· αφήστε τη να λήξει για επιστροφή στην προεπισκόπηση. Οι αλλαγές ισχύουν στο επόμενο αίτημα.": "A customer's access tier is driven entirely by their current subscription. Grant one for full access; let it expire to return to preview. Changes apply on the next request.",
    "Ένα εργαλείο μέσα στο εργαλείο": "A tool within the tool",
    "υπάρχει εξαγωγέας εγγράφου-σε-φύλλο: βγάλτε τους πίνακες από τα συνημμένα ενός διαγωνισμού και πάρτε καθαρό Excel. Τροφοδοτήστε το είτε με ΑΔΑΜ (κατεβάζει τα έγγραφα) είτε ανεβάζοντας αρχεία.": "there's a document-to-spreadsheet extractor: pull the tables out of a tender's attachments and get clean Excel. Feed it either by ADAM (it downloads the documents) or by uploading files.",
    "Λήψη ή ανέβασμα": "Fetch or upload",
    "του/των εγγράφου/ων.": "the document(s).",
    "για ανίχνευση πινάκων· ένας ταξινομητής επισημαίνει ποιοι μοιάζουν με πραγματικούς πίνακες διαγωνισμού.": "to detect tables; a classifier flags which look like real tender tables.",
    "Για σαρωμένα PDF ή εικόνες, τρέξτε": "For scanned PDFs or images, run",
    "τοπικά, ή υψηλότερης ποιότητας μέσω του Claude API όπου υπάρχει κλειδί. Το OCR είναι προαιρετικό ανά αρχείο.": "locally, or higher-quality via the Claude API where a key is present. OCR is opt-in per file.",
    "Ελέγξτε τις σελίδες και": "Review the pages and",
    "εξάγετε σε": "export to",
    "Αποθηκευμένες εξαγωγές μπορούν να δημοσιευτούν για δημόσια προβολή ανά πράξη.": "Saved extractions can be published for a per-act public view.",
    "Διαθεσιμότητα": "Availability",
    "Ορισμένες δυνατότητες εξαρτώνται από τη διαμόρφωση: τα ανεβασμένα συνημμένα και μέρος των εργαλείων κειμένου ενεργοποιούνται με σημαίες, και το OCR μέσω Claude API λειτουργεί μόνο όταν υπάρχει κλειδί. Όπου μια δυνατότητα είναι ανενεργή, τα χειριστήριά της απλώς δεν εμφανίζονται.": "Some capabilities depend on configuration: uploaded attachments and part of the text tooling are enabled by flags, and Claude-API OCR only works when a key is present. Where a feature is off, its controls simply don't appear.",
    "Αναφορά": "Reference",
    "Όρος": "Term",
    "Σημασία": "Meaning",
    "Ο αριθμός αναφοράς που ταυτοποιεί μοναδικά μια πράξη και συνδέει τις πράξεις της ίδιας διαδικασίας μεταξύ πηγών.": "The reference number that uniquely identifies an act and links the acts of the same procedure across sources.",
    "Ο ελληνικός φορολογικός αριθμός — το κλειδί που ταυτοποιεί έναν ανάδοχο.": "The Greek tax number — the key that identifies a contractor.",
    "Κεντρικό Ηλεκτρονικό Μητρώο Δημοσίων Συμβάσεων — η κύρια πηγή.": "Central Electronic Registry of Public Procurement — the primary source.",
    "Το πρόγραμμα διαφάνειας· παρέχει αποφάσεις και τον κατάλογο φορέων.": "The transparency programme; supplies decisions and the organisation directory.",
    "η επίσημη εφημερίδα προμηθειών της ΕΕ.": "the EU's official procurement journal.",
    "Το Γενικό Εμπορικό Μητρώο, για εμπλουτισμό αναδόχων και αρχών μέσω ΑΦΜ.": "The General Commercial Registry, for enriching contractors and authorities by Tax ID.",
    "Κοινό Λεξιλόγιο Δημοσίων Συμβάσεων — ο κωδικός κατηγορίας της ΕΕ για το τι αγοράζεται.": "Common Procurement Vocabulary — the EU category code for what's being bought.",
    "Ο περιφερειακός γεωκωδικός της ΕΕ· εδώ, οι ελληνικές περιφέρειες για το φίλτρο τοποθεσίας.": "The EU regional geocode; here, the Greek regions for the location filter.",
    "Ο οδηγός περιγράφει την εφαρμογή όπως είναι σήμερα. Η διαθεσιμότητα δυνατοτήτων μπορεί να διαφέρει ανά διαμόρφωση.": "This guide describes the app as it stands today. Feature availability can vary with configuration.",
    "Τελευταία ενημέρωση": "Last updated",
}

# Merge into one flat catalog. Later groups override earlier ones on key clash
# (there should be none — keep keys unique across groups).
UI_EN: dict[str, str] = {}
for _grp in (_NAV, _ADMIN_TABS, _BASE_LEGACY, _COMMON, _SEARCH, _ACT, _PARTY,
             _PARTIALS, _EXPLORE_ANALYTICS, _ADMIN1, _ADMIN2, _ADMIN3, _FORMLABELS,
             _PANELS, _ACTFORM, _LEGACY, _LEGACY2, _LEGACY3, _TABLES, _PROC_FAMILY,
             _NUTS_REGIONS, _CPV_EDIT, _DIAVGEIA, _NUTS_FIELD, _ATTACHMENTS,
             _AUTH, _PRODUCTS, _CRM, _CRM2, _CRMNAV, _INTERCONNECT, _HELP):
    UI_EN.update(_grp)
