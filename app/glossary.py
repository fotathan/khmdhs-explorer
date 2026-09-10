"""app/glossary.py — the plain-language glossary of Greek public procurement.

Why this lives in the app rather than a CMS: it is the one page whose job is to
be *found*. Someone who does not yet know what ΑΔΑΜ means is not searching our
corpus — they are searching the term, and this is where they land. Every entry
therefore ends in a link into the real data, so the page is a doorway and not a
dead end.

The text is data, not translation keys. UI chrome goes through i18n_catalog
(short strings, reused everywhere); a two-paragraph definition does not — it
would bury the catalog and make the Greek and English versions of one
explanation impossible to read side by side. Each entry carries both languages
in one place, which is also how the author checks that they still say the same
thing.

**Accuracy is the whole point.** These are summaries for orientation, written
against ν. 4412/2016 as amended (principally by ν. 4782/2021); thresholds and
percentages move, and the page says so. Nothing here is legal advice, and the
`disclaimer` below is rendered on every glossary page rather than being left to
the reader's good sense.
"""

from __future__ import annotations

# --------------------------------------------------------------------------- #
# Sections — the index page groups terms rather than listing 30 alphabetically,
# because a newcomer does not know which word they need yet.
# --------------------------------------------------------------------------- #
SECTIONS = [
    {"slug": "sources",    "el": "Πηγές & μητρώα",
                           "en": "Sources & registers"},
    {"slug": "acts",       "el": "Είδη πράξεων",
                           "en": "Kinds of act"},
    {"slug": "procedures", "el": "Διαδικασίες ανάθεσης",
                           "en": "Award procedures"},
    {"slug": "codes",      "el": "Κωδικοί & ταξινομήσεις",
                           "en": "Codes & classifications"},
    {"slug": "money",      "el": "Αξίες, εγγυήσεις & πληρωμές",
                           "en": "Values, guarantees & payments"},
    {"slug": "law",        "el": "Νομικό πλαίσιο & προσφυγές",
                           "en": "Legal framework & remedies"},
]

DISCLAIMER_EL = ("Επεξηγηματικό υλικό για προσανατολισμό, όχι νομική συμβουλή. "
                 "Οι ορισμοί συνοψίζουν τον ν. 4412/2016 όπως ισχύει· τα όρια "
                 "και τα ποσοστά μεταβάλλονται — για κάθε επίσημη χρήση "
                 "ανατρέξτε στο ισχύον κείμενο του νόμου.")
DISCLAIMER_EN = ("Explanatory material for orientation, not legal advice. The "
                 "definitions summarise Law 4412/2016 as in force; thresholds "
                 "and percentages change — for any official use, consult the "
                 "law itself.")


def _t(slug, section, el_term, en_term, abbr, el_short, en_short,
       el_body, en_body, see=(), related=()):
    return {"slug": slug, "section": section, "abbr": abbr,
            "el": {"term": el_term, "short": el_short, "body": list(el_body)},
            "en": {"term": en_term, "short": en_short, "body": list(en_body)},
            "see": list(see), "related": list(related)}


TERMS = [

    # ---------------------------------------------------------------- sources
    _t("kimdis", "sources", "ΚΗΜΔΗΣ", "KIMDIS — Central Electronic Registry of Public Contracts", "ΚΗΜΔΗΣ",
       "Το κεντρικό μητρώο όπου αναρτώνται υποχρεωτικά οι δημόσιες συμβάσεις και τα στάδιά τους.",
       "The central registry where public contracts and every stage of them must be published.",
       ["Το Κεντρικό Ηλεκτρονικό Μητρώο Δημοσίων Συμβάσεων είναι το επίσημο μητρώο "
        "δημοσιότητας των δημοσίων συμβάσεων. Κάθε στάδιο — το αίτημα, η προκήρυξη, "
        "η κατακύρωση, η σύμβαση, η πληρωμή — αναρτάται ως ξεχωριστή πράξη και "
        "παίρνει τον δικό του ΑΔΑΜ.",
        "Η ανάρτηση δεν είναι διεκπεραιωτική λεπτομέρεια: αποτελεί προϋπόθεση "
        "νομιμότητας για την εκτέλεση της δαπάνης. Γι' αυτό το ΚΗΜΔΗΣ αποτυπώνει "
        "τον πλήρη κύκλο ζωής μιας σύμβασης και όχι μόνο την προκήρυξή της."],
       ["The Central Electronic Registry of Public Contracts (ΚΗΜΔΗΣ) is the official "
        "publication registry for public contracts. Every stage — the request, the "
        "notice, the award, the contract, the payment — is posted as a separate act "
        "and receives its own ΑΔΑΜ.",
        "Publication is not administrative housekeeping: it is a precondition for "
        "the expenditure to be lawfully executed. That is why the registry records "
        "the full lifecycle of a contract, not just its announcement."],
       see=[{"href": "/?source=khmdhs", "el": "Πράξεις από το ΚΗΜΔΗΣ",
             "en": "Acts from KIMDIS"}],
       related=["adam", "diavgeia", "esidis"]),

    _t("adam", "sources", "ΑΔΑΜ", "ADAM — registry publication number", "ΑΔΑΜ",
       "Ο μοναδικός αριθμός που παίρνει κάθε πράξη κατά την ανάρτησή της στο ΚΗΜΔΗΣ.",
       "The unique number each act receives when it is posted to KIMDIS.",
       ["Ο Αριθμός Διαδικτυακής Ανάρτησης Μητρώου ταυτοποιεί μία και μόνο πράξη. "
        "Η δομή του διαβάζεται: δύο ψηφία για το έτος, ένα σύντομο αλφαβητικό "
        "τμήμα για το είδος της πράξης (PROC για προκήρυξη, AWRD για αποτέλεσμα, "
        "SYMV για σύμβαση, REQ για πρωτογενές αίτημα) και ένας αύξων αριθμός — "
        "π.χ. 25SYMV016143474.",
        "Επειδή ο ΑΔΑΜ είναι μοναδικός, είναι ο ασφαλέστερος τρόπος αναζήτησης: "
        "επικολλήστε τον στο πεδίο αναζήτησης και θα βρείτε ακριβώς τη μία πράξη."],
       ["The registry publication number identifies one act and one act only. Its "
        "shape is readable: two digits for the year, a short alphabetic part for the "
        "kind of act (PROC for a notice, AWRD for a result, SYMV for a contract, REQ "
        "for a primary request) and a serial number — e.g. 25SYMV016143474.",
        "Because it is unique, the ΑΔΑΜ is the safest thing to search on: paste it "
        "into the search box and you get exactly that one act."],
       related=["kimdis", "ada"]),

    _t("diavgeia", "sources", "Διαύγεια", "Diavgeia — the transparency programme", None,
       "Το πρόγραμμα διαφάνειας όπου αναρτώνται οι αποφάσεις της διοίκησης.",
       "The transparency programme where administrative decisions are published.",
       ["Το πρόγραμμα «Διαύγεια» υποχρεώνει τους φορείς του δημοσίου να αναρτούν "
        "τις αποφάσεις τους στο διαδίκτυο. Καλύπτει πολύ περισσότερα από τις "
        "δημόσιες συμβάσεις, αλλά οι αποφάσεις ανάληψης υποχρέωσης, οι κατακυρώσεις "
        "και οι αναθέσεις περνούν κι από εκεί.",
        "Κάθε ανάρτηση παίρνει ΑΔΑ. Μια πράξη μπορεί έτσι να έχει και ΑΔΑ και ΑΔΑΜ: "
        "το ίδιο γεγονός καταγράφεται σε δύο μητρώα με διαφορετικό σκοπό — "
        "διοικητική διαφάνεια το ένα, μητρώο συμβάσεων το άλλο."],
       ["The Diavgeia (“Clarity”) programme obliges public bodies to post their "
        "decisions online. It covers far more than procurement, but budget "
        "commitments, award decisions and direct awards pass through it too.",
        "Every posting receives an ΑΔΑ. One event can therefore carry both an ΑΔΑ and "
        "an ΑΔΑΜ: the same act recorded in two registries with different purposes — "
        "administrative transparency in one, a contracts registry in the other."],
       see=[{"href": "/?source=diavgeia", "el": "Πράξεις από τη Διαύγεια",
             "en": "Acts from Diavgeia"}],
       related=["ada", "kimdis"]),

    _t("ada", "sources", "ΑΔΑ", "ADA — Diavgeia publication number", "ΑΔΑ",
       "Ο μοναδικός αριθμός ανάρτησης μιας απόφασης στη Διαύγεια.",
       "The unique number of a decision posted on Diavgeia.",
       ["Ο Αριθμός Διαδικτυακής Ανάρτησης είναι το αναγνωριστικό μιας απόφασης στη "
        "Διαύγεια. Μια διοικητική πράξη που δεν έχει αναρτηθεί — άρα δεν έχει ΑΔΑ — "
        "δεν εκτελείται.",
        "Στα δεδομένα μας ο ΑΔΑ είναι το κλειδί με το οποίο συνδέονται οι πράξεις "
        "που προέρχονται από τη Διαύγεια με τις αντίστοιχες εγγραφές του ΚΗΜΔΗΣ."],
       ["The publication number identifies a decision on Diavgeia. An administrative "
        "act that has not been posted — and therefore has no ΑΔΑ — cannot be executed.",
        "In our data the ΑΔΑ is the key that ties acts sourced from Diavgeia to the "
        "corresponding KIMDIS records."],
       related=["diavgeia", "adam"]),

    _t("esidis", "sources", "ΕΣΗΔΗΣ", "ESIDIS — the national e-procurement system", "ΕΣΗΔΗΣ",
       "Η πλατφόρμα στην οποία διενεργούνται ηλεκτρονικά οι διαγωνισμοί.",
       "The platform on which tender procedures are actually run.",
       ["Το Εθνικό Σύστημα Ηλεκτρονικών Δημοσίων Συμβάσεων είναι ο τόπος όπου "
        "διεξάγεται ο διαγωνισμός: εκεί αναρτώνται τα τεύχη, υποβάλλονται "
        "ηλεκτρονικά οι προσφορές, αποσφραγίζονται και αξιολογούνται.",
        "Δεν πρέπει να συγχέεται με το ΚΗΜΔΗΣ. Το ΕΣΗΔΗΣ είναι η πλατφόρμα "
        "συναλλαγής — εκεί «τρέχει» η διαδικασία· το ΚΗΜΔΗΣ είναι το μητρώο "
        "δημοσιότητας — εκεί καταγράφεται ότι έγινε."],
       ["The National Electronic Public Procurement System is where a tender actually "
        "happens: the documents are posted there, tenders are submitted "
        "electronically, then unsealed and evaluated.",
        "It should not be confused with KIMDIS. ESIDIS is the transaction platform — "
        "the procedure runs there; KIMDIS is the publication registry — the record "
        "that it happened lives there."],
       related=["kimdis", "diakiryxi"]),

    _t("ted", "sources", "TED", "TED — Tenders Electronic Daily", "TED",
       "Η ευρωπαϊκή βάση δημοσίευσης των συμβάσεων που υπερβαίνουν τα ενωσιακά κατώφλια.",
       "The EU-wide publication database for contracts above the European thresholds.",
       ["Το Tenders Electronic Daily είναι το συμπλήρωμα της Επίσημης Εφημερίδας της "
        "ΕΕ για τις δημόσιες συμβάσεις. Όταν η εκτιμώμενη αξία μιας σύμβασης "
        "υπερβαίνει το ενωσιακό κατώφλι, η προκήρυξη δημοσιεύεται υποχρεωτικά και "
        "εκεί, ώστε να μπορεί να τη δει προμηθευτής από κάθε κράτος μέλος.",
        "Οι ευρωπαϊκές προκηρύξεις έχουν διαφορετική δομή από τις εθνικές: τα "
        "στοιχεία είναι τυποποιημένα πεδία (eForms) και όχι ελεύθερο κείμενο."],
       ["Tenders Electronic Daily is the supplement to the Official Journal of the EU "
        "for public contracts. When the estimated value of a contract exceeds the EU "
        "threshold, the notice must also be published there, so that a supplier in "
        "any member state can see it.",
        "European notices are shaped differently from national ones: the content is "
        "typed fields (eForms) rather than free prose."],
       see=[{"href": "/?source=ted", "el": "Πράξεις από το TED",
             "en": "Acts from TED"}],
       related=["katofli", "cpv"]),

    # ------------------------------------------------------------------- acts
    _t("prokiryxi", "acts", "Προκήρυξη", "Contract notice", None,
       "Η δημόσια ανακοίνωση ότι ένας φορέας προτίθεται να αναθέσει μια σύμβαση.",
       "The public announcement that a body intends to award a contract.",
       ["Η προκήρυξη ανοίγει τη διαδικασία: ανακοινώνει το αντικείμενο, την "
        "εκτιμώμενη αξία, τη διαδικασία, τα κριτήρια και — το κρισιμότερο για όποιον "
        "σκέφτεται να συμμετάσχει — την καταληκτική ημερομηνία υποβολής προσφορών.",
        "Στον Εξερευνητή η προκήρυξη είναι ξεχωριστό είδος πράξης και συνδέεται με "
        "το αποτέλεσμα και τη σύμβαση που θα ακολουθήσουν, ώστε να διαβάζεται όλη η "
        "αλυσίδα."],
       ["The notice opens the procedure: it announces the subject matter, the "
        "estimated value, the procedure, the criteria and — most important for anyone "
        "considering bidding — the deadline for submitting tenders.",
        "In the Explorer a notice is its own kind of act, linked to the result and "
        "the contract that follow, so the whole chain can be read at once."],
       see=[{"href": "/?type=notice", "el": "Όλες οι προκηρύξεις",
             "en": "All contract notices"}],
       related=["diakiryxi", "symvasi", "apotelesma"]),

    _t("diakiryxi", "acts", "Διακήρυξη", "Tender documents", None,
       "Το πλήρες σώμα των όρων του διαγωνισμού — τεύχη, προδιαγραφές, κριτήρια, σχέδιο σύμβασης.",
       "The full body of tender terms — specifications, criteria and the draft contract.",
       ["Η προκήρυξη ανακοινώνει· η διακήρυξη δεσμεύει. Είναι το έγγραφο (συνήθως "
        "δεκάδες σελίδες) που ορίζει ποιος δικαιούται να συμμετάσχει, τι ακριβώς "
        "ζητείται, πώς βαθμολογείται, τι εγγυήσεις απαιτούνται και υπό ποιους όρους "
        "θα εκτελεστεί η σύμβαση.",
        "Οι πραγματικές απαιτήσεις — τεχνικές προδιαγραφές, πιστοποιητικά, "
        "ποινικές ρήτρες — βρίσκονται εδώ και όχι στην περίληψη της προκήρυξης."],
       ["The notice announces; the tender documents bind. This is the document — "
        "usually dozens of pages — that sets who may take part, exactly what is being "
        "asked for, how it will be scored, what guarantees are required and on what "
        "terms the contract will be performed.",
        "The real requirements — technical specifications, certificates, penalty "
        "clauses — live here, not in the notice's summary."],
       related=["prokiryxi", "eees", "eggyitiki"]),

    _t("apotelesma", "acts", "Αποτέλεσμα / Κατακύρωση", "Award decision", None,
       "Η απόφαση με την οποία ο φορέας ανακηρύσσει τον ανάδοχο.",
       "The decision by which the authority declares the winning tenderer.",
       ["Μετά την αξιολόγηση των προσφορών, ο φορέας κατακυρώνει τη σύμβαση στον "
        "προσωρινό ανάδοχο. Η κατακύρωση οριστικοποιείται αφού ελεγχθούν τα "
        "δικαιολογητικά του και παρέλθουν οι προθεσμίες προσφυγής.",
        "Η πράξη αυτή είναι το σημείο όπου εμφανίζεται για πρώτη φορά όνομα "
        "αναδόχου και τελικό τίμημα — γι' αυτό είναι η πιο χρήσιμη πράξη για "
        "ανάλυση ανταγωνισμού."],
       ["After the tenders are evaluated, the authority awards the contract to the "
        "provisional contractor. The award becomes final once that contractor's "
        "supporting documents are checked and the appeal periods have passed.",
        "This is the act where a contractor's name and a final price appear for the "
        "first time — which makes it the most useful act for competitive analysis."],
       see=[{"href": "/?type=auction", "el": "Όλα τα αποτελέσματα",
             "en": "All award decisions"}],
       related=["anadoxos", "symvasi", "prodikastiki"]),

    _t("symvasi", "acts", "Σύμβαση", "Contract", None,
       "Το υπογεγραμμένο συμφωνητικό μεταξύ φορέα και αναδόχου.",
       "The signed agreement between the authority and the contractor.",
       ["Η σύμβαση υπογράφεται μετά την οριστική κατακύρωση και αναρτάται στο "
        "ΚΗΜΔΗΣ. Περιέχει το τελικό τίμημα, τη διάρκεια, τον τρόπο πληρωμής και τις "
        "εγγυήσεις καλής εκτέλεσης.",
        "Η διαφορά μεταξύ της εκτιμώμενης αξίας της προκήρυξης και της τελικής αξίας "
        "της σύμβασης είναι η έκπτωση που πέτυχε ο διαγωνισμός — ένα από τα πιο "
        "διαφωτιστικά μεγέθη σε όλη την αλυσίδα."],
       ["The contract is signed once the award is final and is posted to KIMDIS. It "
        "carries the final price, the duration, the payment terms and the performance "
        "guarantee.",
        "The gap between the notice's estimated value and the contract's final value "
        "is the discount the competition produced — one of the most revealing figures "
        "in the whole chain."],
       see=[{"href": "/?type=contract", "el": "Όλες οι συμβάσεις",
             "en": "All contracts"}],
       related=["apotelesma", "ektimomeni-axia", "eggyitiki"]),

    _t("entalma", "acts", "Εντολή πληρωμής", "Payment order", None,
       "Η πράξη με την οποία εγκρίνεται και εκτελείται η πληρωμή του αναδόχου.",
       "The act by which a payment to the contractor is approved and executed.",
       ["Η πληρωμή δεν είναι αυτονόητη συνέπεια της σύμβασης: εκδίδεται χωριστή "
        "πράξη, με δικό της ΑΔΑΜ, για κάθε καταβολή.",
        "Οι πράξεις πληρωμής είναι ο λόγος για τον οποίο τα δεδομένα δείχνουν όχι "
        "μόνο ποιος κέρδισε αλλά και πόσο και πότε πληρώθηκε — στοιχείο που λείπει "
        "από τις περισσότερες βάσεις προκηρύξεων."],
       ["Payment does not follow automatically from the contract: a separate act, "
        "with its own ΑΔΑΜ, is issued for each disbursement.",
        "Payment acts are the reason this data shows not only who won but how much "
        "they were actually paid and when — something most tender databases do not "
        "have at all."],
       see=[{"href": "/?type=payment", "el": "Όλες οι εντολές πληρωμής",
             "en": "All payment orders"}],
       related=["analipsi", "symvasi"]),

    _t("protogenes-aitima", "acts", "Πρωτογενές αίτημα", "Primary request", None,
       "Η αρχική καταγραφή μιας ανάγκης, πριν δεσμευτεί οποιαδήποτε πίστωση.",
       "The first record of a need, before any budget is committed.",
       ["Πριν από κάθε διαδικασία, η υπηρεσία που έχει την ανάγκη καταχωρεί "
        "πρωτογενές αίτημα στο ΚΗΜΔΗΣ και παίρνει ΑΔΑΜ. Το αίτημα περιγράφει τι "
        "χρειάζεται και με ποια εκτιμώμενη δαπάνη.",
        "Είναι η πρωιμότερη ορατή ένδειξη ότι κάτι πρόκειται να προκηρυχθεί — "
        "συχνά εβδομάδες ή μήνες πριν από την προκήρυξη."],
       ["Before any procedure, the department with the need registers a primary "
        "request in KIMDIS and receives an ΑΔΑΜ. The request describes what is needed "
        "and at what estimated cost.",
        "It is the earliest visible sign that something is about to be tendered — "
        "often weeks or months ahead of the notice."],
       related=["analipsi", "prokiryxi"]),

    _t("orthi-epanalipsi", "acts", "Ορθή επανάληψη", "Corrected re-issue", None,
       "Η επαναδημοσίευση μιας πράξης με διόρθωση σφάλματος.",
       "The republication of an act with an error corrected.",
       ["Όταν μια δημοσιευμένη πράξη περιέχει σφάλμα — λάθος ποσό, ημερομηνία ή "
        "στοιχείο αναδόχου — επαναδημοσιεύεται ως ορθή επανάληψη.",
        "Στον Εξερευνητή αυτές οι πράξεις φέρουν σχετική σήμανση, ώστε να μη "
        "διαβαστεί ως δύο διαφορετικές συμβάσεις αυτό που είναι μία, δημοσιευμένη "
        "δύο φορές."],
       ["When a published act contains an error — a wrong amount, date or contractor "
        "detail — it is republished as a corrected re-issue.",
        "The Explorer marks such acts, so that one contract published twice is not "
        "read as two different contracts."],
       related=["mataiosi"]),

    # ------------------------------------------------------------- procedures
    _t("apeftheias-anathesi", "procedures", "Απευθείας ανάθεση", "Direct award", None,
       "Ανάθεση χωρίς διαγωνισμό, κάτω από συγκεκριμένο όριο αξίας.",
       "An award made without a competition, below a set value threshold.",
       ["Για μικρές δαπάνες ο νόμος επιτρέπει στον φορέα να αναθέσει απευθείας, "
        "χωρίς διαγωνιστική διαδικασία. Το όριο ορίζεται στο άρθρο 118 του ν. "
        "4412/2016 και έχει αναπροσαρμοστεί — σήμερα 30.000 ευρώ χωρίς ΦΠΑ.",
        "Η απευθείας ανάθεση δεν σημαίνει απουσία δημοσιότητας: η απόφαση αναρτάται "
        "κανονικά και είναι πλήρως ορατή. Είναι, σε πλήθος πράξεων, η συνηθέστερη "
        "μορφή ανάθεσης."],
       ["For small expenditures the law lets an authority award directly, with no "
        "competitive procedure. The threshold is set by article 118 of Law 4412/2016 "
        "and has been revised over time — currently €30,000 excluding VAT.",
        "A direct award is not an unpublished one: the decision is posted normally and "
        "is fully visible. By number of acts it is the commonest form of award."],
       see=[{"href": "/?procedure_type=6", "el": "Απευθείας αναθέσεις στα δεδομένα",
             "en": "Direct awards in the data"}],
       related=["ektimomeni-axia", "n4412"]),

    _t("anoikti-diadikasia", "procedures", "Ανοικτή διαδικασία", "Open procedure", None,
       "Διαγωνισμός στον οποίο μπορεί να υποβάλει προσφορά κάθε ενδιαφερόμενος.",
       "A tender in which any interested operator may submit a bid.",
       ["Στην ανοικτή διαδικασία δεν υπάρχει στάδιο προεπιλογής: όποιος πληροί τα "
        "κριτήρια καταλληλότητας υποβάλλει απευθείας προσφορά μέσα στην προθεσμία.",
        "Είναι η προεπιλεγμένη διαδικασία για συμβάσεις άνω των ορίων και η "
        "διαφανέστερη, γι' αυτό και η συνηθέστερη στις μεγάλες αξίες."],
       ["In an open procedure there is no pre-selection stage: anyone meeting the "
        "suitability criteria submits a tender directly, within the deadline.",
        "It is the default procedure above the thresholds and the most transparent, "
        "which is why it dominates at higher values."],
       see=[{"href": "/?procedure_type=1", "el": "Ανοικτές διαδικασίες στα δεδομένα",
             "en": "Open procedures in the data"}],
       related=["kleisti-diadikasia", "kritirio-anathesis"]),

    _t("kleisti-diadikasia", "procedures", "Κλειστή διαδικασία", "Restricted procedure", None,
       "Διαδικασία δύο σταδίων: πρώτα εκδήλωση ενδιαφέροντος, μετά προσφορά κατόπιν πρόσκλησης.",
       "A two-stage procedure: expression of interest first, then a tender by invitation.",
       ["Στο πρώτο στάδιο υποβάλλονται αιτήσεις συμμετοχής και ελέγχεται η "
        "καταλληλότητα των υποψηφίων. Στο δεύτερο, μόνο όσοι επιλεγούν καλούνται να "
        "υποβάλουν προσφορά.",
        "Χρησιμοποιείται όταν το αντικείμενο είναι σύνθετο και η αξιολόγηση πολλών "
        "πλήρων προσφορών θα ήταν δυσανάλογα δαπανηρή."],
       ["In the first stage, requests to participate are submitted and candidates' "
        "suitability is checked. In the second, only those selected are invited to "
        "tender.",
        "It is used where the subject matter is complex and evaluating many full "
        "tenders would be disproportionately costly."],
       see=[{"href": "/?procedure_type=2", "el": "Κλειστές διαδικασίες στα δεδομένα",
             "en": "Restricted procedures in the data"}],
       related=["anoikti-diadikasia"]),

    _t("diapragmatefsi", "procedures",
       "Διαπραγμάτευση χωρίς δημοσίευση", "Negotiated procedure without prior publication", None,
       "Απευθείας διαπραγμάτευση με έναν ή λίγους φορείς, μόνο σε ρητά προβλεπόμενες περιπτώσεις.",
       "Direct negotiation with one or few operators, only on strictly listed grounds.",
       ["Είναι η εξαίρεση με τη στενότερη ερμηνεία: επιτρέπεται μόνο όταν συντρέχει "
        "λόγος που απαριθμεί ο νόμος — κατεπείγουσα ανάγκη από απρόβλεπτο γεγονός, "
        "τεχνική μοναδικότητα του προμηθευτή, άγονος προηγούμενος διαγωνισμός.",
        "Επειδή παρακάμπτει τον ανταγωνισμό, η αιτιολόγηση της επιλογής της "
        "ελέγχεται αυστηρά και αποτελεί συχνό αντικείμενο προσφυγών."],
       ["This is the narrowest exception: it is allowed only where a ground listed in "
        "the law applies — extreme urgency from an unforeseeable event, technical "
        "uniqueness of the supplier, a prior procedure that produced no valid tenders.",
        "Because it bypasses competition, the justification is scrutinised strictly "
        "and is a frequent subject of appeals."],
       see=[{"href": "/?procedure_type=12", "el": "Διαπραγματεύσεις στα δεδομένα",
             "en": "Negotiated procedures in the data"}],
       related=["prodikastiki", "apeftheias-anathesi"]),

    _t("symfonia-plaisio", "procedures", "Συμφωνία-πλαίσιο", "Framework agreement", None,
       "Συμφωνία που καθορίζει εκ των προτέρων τους όρους των συμβάσεων που θα ανατεθούν μέσα σε ορισμένο διάστημα.",
       "An agreement setting in advance the terms of contracts to be awarded over a set period.",
       ["Η συμφωνία-πλαίσιο δεν είναι η ίδια σύμβαση προμήθειας: ορίζει τιμές, όρους "
        "και συμμετέχοντες, και στη συνέχεια οι επιμέρους ανάγκες καλύπτονται με "
        "εκτελεστικές συμβάσεις χωρίς νέο πλήρη διαγωνισμό.",
        "Η διάρκειά της είναι κατά κανόνα έως τέσσερα έτη. Είναι το εργαλείο επιλογής "
        "για επαναλαμβανόμενες προμήθειες — φάρμακα, αναλώσιμα, καύσιμα."],
       ["A framework agreement is not itself a supply contract: it fixes prices, terms "
        "and participants, after which individual needs are met through call-off "
        "contracts without a new full tender.",
        "Its duration is as a rule up to four years. It is the instrument of choice "
        "for recurring supplies — medicines, consumables, fuel."],
       related=["dynamiko-systima", "ektimomeni-axia"]),

    _t("dynamiko-systima", "procedures", "Δυναμικό σύστημα αγορών", "Dynamic purchasing system", "ΔΣΑ",
       "Πλήρως ηλεκτρονικό σύστημα προμηθειών, ανοικτό σε νέες εντάξεις καθ' όλη τη διάρκειά του.",
       "A fully electronic purchasing system, open to new entrants throughout its life.",
       ["Σε αντίθεση με τη συμφωνία-πλαίσιο, το δυναμικό σύστημα αγορών δεν κλείνει: "
        "οποιοσδήποτε φορέας πληροί τα κριτήρια μπορεί να ενταχθεί οποτεδήποτε και να "
        "διεκδικήσει τις επόμενες παραγγελίες.",
        "Αυτό το καθιστά το πιο προσιτό σημείο εισόδου για μια επιχείρηση που δεν "
        "πρόλαβε τον αρχικό διαγωνισμό."],
       ["Unlike a framework agreement, a dynamic purchasing system never closes: any "
        "operator meeting the criteria may join at any time and compete for subsequent "
        "orders.",
        "That makes it the most accessible entry point for a firm that missed the "
        "original tender."],
       related=["symfonia-plaisio"]),

    _t("tmimata", "procedures", "Τμήματα", "Lots", None,
       "Η υποδιαίρεση μιας σύμβασης σε μέρη που ανατίθενται χωριστά.",
       "The division of a contract into parts that are awarded separately.",
       ["Μια προκήρυξη μπορεί να χωρίζεται σε τμήματα — ανά είδος, ανά γεωγραφική "
        "περιοχή, ανά μονάδα παράδοσης. Κάθε τμήμα αξιολογείται και κατακυρώνεται "
        "ξεχωριστά, συχνά σε διαφορετικό ανάδοχο.",
        "Για μια μικρή επιχείρηση τα τμήματα είναι το σημείο όπου ένας απρόσιτος "
        "διαγωνισμός γίνεται προσιτός: δεν χρειάζεται να καλύψει όλο το αντικείμενο."],
       ["A notice may be divided into lots — by item, by geography, by delivery point. "
        "Each lot is evaluated and awarded separately, often to a different contractor.",
        "For a small firm, lots are where an out-of-reach tender becomes reachable: "
        "there is no need to cover the whole scope."],
       related=["prokiryxi", "ektimomeni-axia"]),

    # ------------------------------------------------------------------ codes
    _t("cpv", "codes", "CPV", "CPV — Common Procurement Vocabulary", "CPV",
       "Το ευρωπαϊκό λεξιλόγιο κωδικών που περιγράφει το αντικείμενο κάθε σύμβασης.",
       "The European code vocabulary describing the subject matter of every contract.",
       ["Το Κοινό Λεξιλόγιο για τις Δημόσιες Συμβάσεις αποδίδει σε κάθε αντικείμενο "
        "έναν οκταψήφιο κωδικό με ελεγκτικό ψηφίο — π.χ. 45000000 για κατασκευαστικές "
        "εργασίες. Οι κωδικοί είναι ιεραρχικοί: τα αρχικά ψηφία δηλώνουν την ευρύτερη "
        "κατηγορία και τα επόμενα εξειδικεύουν.",
        "Ο κωδικός CPV είναι το ακριβέστερο φίλτρο που διαθέτετε, γιατί δεν εξαρτάται "
        "από τη διατύπωση του τίτλου: μια «προμήθεια ειδών καθαριότητας» και μια "
        "«αγορά απορρυπαντικών» μοιράζονται τον ίδιο κωδικό."],
       ["The Common Procurement Vocabulary gives every subject matter an eight-digit "
        "code plus a check digit — e.g. 45000000 for construction work. The codes are "
        "hierarchical: the leading digits give the broad category, the later ones "
        "narrow it down.",
        "CPV is the most precise filter available, because it does not depend on how a "
        "title was worded: a “supply of cleaning items” and a “purchase of "
        "detergents” share the same code."],
       related=["nuts", "ted"]),

    _t("nuts", "codes", "NUTS", "NUTS — territorial units", "NUTS",
       "Η ευρωπαϊκή κωδικοποίηση γεωγραφικών ενοτήτων — για την Ελλάδα, οι περιφέρειες και οι νομοί.",
       "The European coding of territorial units — for Greece, regions and prefectures.",
       ["Οι κωδικοί NUTS δηλώνουν πού εκτελείται η σύμβαση. Είναι ιεραρχικοί: EL "
        "είναι όλη η χώρα, EL30 η Αττική, EL303 μια επιμέρους ενότητά της.",
        "Επειδή το φίλτρο λειτουργεί με πρόθεμα, η επιλογή μιας περιφέρειας φέρνει "
        "και όλες τις υποενότητές της."],
       ["NUTS codes state where a contract is performed. They are hierarchical: EL is "
        "the whole country, EL30 is Attica, EL303 one of its sub-units.",
        "Because the filter matches on prefix, choosing a region also brings in every "
        "unit beneath it."],
       see=[{"href": "/?nuts=EL30", "el": "Πράξεις στην Αττική",
             "en": "Acts in Attica"}],
       related=["cpv"]),

    _t("anathetousa-arxi", "codes", "Αναθέτουσα αρχή", "Contracting authority", None,
       "Ο δημόσιος φορέας που προκηρύσσει και αναθέτει τη σύμβαση.",
       "The public body that tenders and awards the contract.",
       ["Αναθέτουσες αρχές είναι το δημόσιο, οι ΟΤΑ, τα νοσοκομεία, τα πανεπιστήμια "
        "και γενικά οι οργανισμοί δημοσίου δικαίου. Ο όρος «αναθέτων φορέας» "
        "χρησιμοποιείται χωριστά για τους φορείς κοινής ωφέλειας (ενέργεια, ύδρευση, "
        "μεταφορές), που υπάγονται σε ελαφρώς διαφορετικούς κανόνες.",
        "Στον Εξερευνητή κάθε αναθέτουσα αρχή έχει δική της σελίδα με το ιστορικό "
        "της: τι προκηρύσσει, σε ποιους αναθέτει και με τι αξίες."],
       ["Contracting authorities are the state, local government, hospitals, "
        "universities and bodies governed by public law generally. The separate term "
        "“contracting entity” covers utilities (energy, water, transport), which "
        "follow slightly different rules.",
        "In the Explorer every authority has its own page with its history: what it "
        "tenders, whom it awards to and at what values."],
       see=[{"href": "/authorities", "el": "Κατάλογος αναθετουσών αρχών",
             "en": "Directory of contracting authorities"}],
       related=["anadoxos"]),

    _t("anadoxos", "codes", "Ανάδοχος / Οικονομικός φορέας", "Contractor / economic operator", None,
       "Ο οικονομικός φορέας στον οποίο κατακυρώνεται η σύμβαση.",
       "The economic operator to whom the contract is awarded.",
       ["«Οικονομικός φορέας» είναι κάθε επιχείρηση, φυσικό πρόσωπο ή ένωση που "
        "μπορεί να προσφέρει έργα, αγαθά ή υπηρεσίες· «ανάδοχος» είναι εκείνος που "
        "τελικά κέρδισε.",
        "Οι φορείς ταυτοποιούνται με ΑΦΜ, γεγονός που επιτρέπει να συγκεντρωθεί όλη "
        "η δραστηριότητα μιας επιχείρησης σε μία σελίδα, ανεξάρτητα από το πώς "
        "γράφτηκε η επωνυμία της σε κάθε πράξη."],
       ["An “economic operator” is any firm, individual or consortium able to offer "
        "works, goods or services; the “contractor” is the one that won.",
        "Operators are identified by tax number (ΑΦΜ), which makes it possible to "
        "gather all of a firm's activity onto one page regardless of how its name was "
        "spelled in each act."],
       see=[{"href": "/contractors", "el": "Κατάλογος αναδόχων",
             "en": "Directory of contractors"}],
       related=["anathetousa-arxi", "afm"]),

    _t("afm", "codes", "ΑΦΜ & ΓΕΜΗ", "Tax number & business registry number", "ΑΦΜ / ΓΕΜΗ",
       "Τα δύο αναγνωριστικά μιας επιχείρησης: το φορολογικό και το εμπορικό μητρώο.",
       "The two identifiers of a company: the tax number and the business registry.",
       ["Ο Αριθμός Φορολογικού Μητρώου ταυτοποιεί μοναδικά κάθε επιχείρηση και είναι "
        "το κλειδί με το οποίο συνδέονται οι πράξεις ενός αναδόχου μεταξύ τους.",
        "Το Γενικό Εμπορικό Μητρώο (ΓΕΜΗ) προσθέτει τα εταιρικά στοιχεία — νομική "
        "μορφή, έδρα, κατάσταση, δραστηριότητα — που δεν υπάρχουν στις πράξεις "
        "προμηθειών."],
       ["The tax number (ΑΦΜ) uniquely identifies a business and is the key that links "
        "a contractor's acts to one another.",
        "The General Commercial Registry (ΓΕΜΗ) adds the corporate detail — legal "
        "form, seat, status, activity — that procurement acts do not carry."],
       related=["anadoxos"]),

    # ------------------------------------------------------------------ money
    _t("ektimomeni-axia", "money", "Εκτιμώμενη αξία σύμβασης", "Estimated contract value", None,
       "Η προϋπολογισθείσα αξία χωρίς ΦΠΑ, που καθορίζει ποιο καθεστώς εφαρμόζεται.",
       "The budgeted value excluding VAT, which determines which regime applies.",
       ["Υπολογίζεται χωρίς ΦΠΑ και περιλαμβάνει κάθε προβλεπόμενο δικαίωμα "
        "προαίρεσης ή παράτασης. Δεν επιτρέπεται η τεχνητή κατάτμηση μιας ανάγκης σε "
        "μικρότερες συμβάσεις ώστε να αποφευχθεί ένα κατώφλι.",
        "Από αυτό το ποσό εξαρτάται σχεδόν το καθετί: αν χωρεί απευθείας ανάθεση, αν "
        "απαιτείται δημοσίευση στο TED, τι εγγυήσεις ζητούνται."],
       ["It is calculated net of VAT and includes every foreseen option or extension. "
        "Splitting a single need into smaller contracts to stay under a threshold is "
        "not permitted.",
        "Almost everything follows from this figure: whether a direct award is "
        "possible, whether publication in TED is required, what guarantees apply."],
       related=["katofli", "apeftheias-anathesi", "eggyitiki"]),

    _t("katofli", "money", "Κατώφλια", "Thresholds", None,
       "Τα όρια αξίας που καθορίζουν ποιοι κανόνες δημοσιότητας και διαδικασίας ισχύουν.",
       "The value limits that decide which publication and procedure rules apply.",
       ["Υπάρχουν δύο επίπεδα. Τα εθνικά όρια κρίνουν, μεταξύ άλλων, αν επιτρέπεται "
        "απευθείας ανάθεση. Τα ενωσιακά κατώφλια κρίνουν αν η σύμβαση πρέπει να "
        "δημοσιευθεί σε ευρωπαϊκό επίπεδο και να ακολουθήσει τους κανόνες των "
        "οδηγιών.",
        "Τα ενωσιακά ποσά αναθεωρούνται από την Επιτροπή ανά διετία και διαφέρουν "
        "ανά είδος σύμβασης (έργα, προμήθειες, υπηρεσίες) και ανά κατηγορία φορέα — "
        "γι' αυτό δεν αναφέρονται εδώ ως σταθερά νούμερα."],
       ["There are two levels. National limits decide, among other things, whether a "
        "direct award is allowed. EU thresholds decide whether the contract must be "
        "published Europe-wide and follow the directives' rules.",
        "The EU figures are revised by the Commission every two years and differ by "
        "contract type (works, supplies, services) and by category of authority — "
        "which is why they are not quoted here as fixed numbers."],
       related=["ted", "ektimomeni-axia", "apeftheias-anathesi"]),

    _t("kritirio-anathesis", "money", "Κριτήριο ανάθεσης", "Award criterion", None,
       "Η βάση στην οποία επιλέγεται η καλύτερη προσφορά — τιμή ή σχέση ποιότητας-τιμής.",
       "The basis on which the best tender is chosen — price, or quality-to-price.",
       ["Ο νόμος ορίζει ως κριτήριο την πλέον συμφέρουσα από οικονομική άποψη "
        "προσφορά. Αυτή προσδιορίζεται είτε αποκλειστικά βάσει τιμής, είτε βάσει "
        "της βέλτιστης σχέσης ποιότητας-τιμής με σταθμισμένα κριτήρια.",
        "Όταν ισχύει το δεύτερο, η τεχνική προσφορά βαθμολογείται και ο συνδυασμός "
        "βαθμού και τιμής καθορίζει τον νικητή — μια χαμηλότερη τιμή δεν αρκεί."],
       ["The law sets the criterion as the most economically advantageous tender. That "
        "is determined either on price alone, or on the best price-quality ratio using "
        "weighted criteria.",
        "Where the latter applies, the technical offer is scored and the combination of "
        "score and price decides the winner — a lower price alone is not enough."],
       related=["anoikti-diadikasia", "diakiryxi"]),

    _t("eggyitiki", "money", "Εγγυητική επιστολή", "Guarantee (bond)", None,
       "Τραπεζική ή ασφαλιστική εγγύηση που εξασφαλίζει τον φορέα σε κάθε στάδιο.",
       "A bank or insurance guarantee securing the authority at each stage.",
       ["Οι συνηθέστερες είναι η εγγυητική συμμετοχής, που κατατίθεται με την "
        "προσφορά, και η εγγυητική καλής εκτέλεσης, που κατατίθεται με την υπογραφή "
        "της σύμβασης· ακολουθούν, κατά περίπτωση, η εγγυητική προκαταβολής και η "
        "εγγυητική καλής λειτουργίας.",
        "Τα ποσοστά ορίζονται στο άρθρο 72 του ν. 4412/2016 όπως ισχύει και "
        "υπολογίζονται επί της εκτιμώμενης ή της συμβατικής αξίας χωρίς ΦΠΑ. Για "
        "μικρή επιχείρηση, το κόστος και η δέσμευση κεφαλαίου των εγγυήσεων είναι "
        "συχνά ο πραγματικός φραγμός συμμετοχής."],
       ["The commonest are the tender guarantee, lodged with the bid, and the "
        "performance guarantee, lodged when the contract is signed; an advance-payment "
        "guarantee and a warranty guarantee follow where applicable.",
        "The percentages are set by article 72 of Law 4412/2016 as in force and are "
        "calculated on the estimated or contract value net of VAT. For a small firm, "
        "the cost and the tied-up capital of these guarantees is often the real "
        "barrier to bidding."],
       related=["diakiryxi", "symvasi"]),

    _t("analipsi", "money", "Ανάληψη υποχρέωσης", "Budget commitment", None,
       "Η απόφαση που δεσμεύει πίστωση στον προϋπολογισμό πριν γίνει οποιαδήποτε δαπάνη.",
       "The decision reserving budget appropriation before any expenditure is made.",
       ["Καμία δαπάνη δεν εκτελείται χωρίς προηγούμενη δέσμευση της αντίστοιχης "
        "πίστωσης. Η απόφαση ανάληψης υποχρέωσης αναρτάται και αυτή, και είναι το "
        "σημείο όπου η πρόθεση γίνεται δημοσιονομικά δεσμευτική.",
        "Στην αλυσίδα των πράξεων βρίσκεται ανάμεσα στο αίτημα και στην προκήρυξη."],
       ["No expenditure is executed without the corresponding appropriation being "
        "committed first. The commitment decision is itself published, and is the "
        "point at which intention becomes fiscally binding.",
        "In the chain of acts it sits between the request and the notice."],
       related=["protogenes-aitima", "entalma"]),

    # -------------------------------------------------------------------- law
    _t("n4412", "law", "Ν. 4412/2016", "Law 4412/2016", None,
       "Ο βασικός νόμος για τις δημόσιες συμβάσεις έργων, προμηθειών και υπηρεσιών.",
       "The principal Greek statute on public works, supply and service contracts.",
       ["Ο ν. 4412/2016 ενσωμάτωσε τις ευρωπαϊκές οδηγίες 2014/24/ΕΕ και 2014/25/ΕΕ "
        "και αποτελεί το ενιαίο πλαίσιο: ποιος υπάγεται, ποιες διαδικασίες "
        "επιτρέπονται, πώς αξιολογούνται οι προσφορές, πώς εκτελούνται οι συμβάσεις.",
        "Έχει τροποποιηθεί επανειλημμένα — η εκτενέστερη αναμόρφωση έγινε με τον ν. "
        "4782/2021. Όπου εδώ αναφέρονται άρθρα, εννοείται το κείμενο όπως ισχύει."],
       ["Law 4412/2016 transposed EU directives 2014/24/EU and 2014/25/EU and provides "
        "the single framework: who is covered, which procedures are permitted, how "
        "tenders are evaluated, how contracts are performed.",
        "It has been amended repeatedly — the most extensive reform came with Law "
        "4782/2021. Where articles are cited here, the text as currently in force is "
        "meant."],
       related=["apeftheias-anathesi", "katofli"]),

    _t("eees", "law", "ΕΕΕΣ", "ESPD — European Single Procurement Document", "ΕΕΕΣ / ESPD",
       "Η υπεύθυνη δήλωση που αντικαθιστά τα δικαιολογητικά κατά την υποβολή προσφοράς.",
       "The self-declaration that replaces supporting documents at bidding time.",
       ["Το Ευρωπαϊκό Ενιαίο Έγγραφο Σύμβασης είναι μια τυποποιημένη υπεύθυνη δήλωση: "
        "ο προσφέρων βεβαιώνει ότι δεν συντρέχουν λόγοι αποκλεισμού και ότι πληροί τα "
        "κριτήρια επιλογής, χωρίς να προσκομίσει πιστοποιητικά.",
        "Τα πραγματικά δικαιολογητικά ζητούνται μόνο από τον προσωρινό ανάδοχο, πριν "
        "την οριστική κατακύρωση. Ο σκοπός είναι να μη χρειάζεται κάθε συμμετέχων να "
        "συγκεντρώνει φάκελο πιστοποιητικών για κάθε διαγωνισμό."],
       ["The European Single Procurement Document is a standardised self-declaration: "
        "the tenderer states that no exclusion grounds apply and that the selection "
        "criteria are met, without producing certificates.",
        "The actual documents are demanded only from the provisional contractor, "
        "before the final award. The point is that not every participant has to "
        "assemble a certificate file for every tender."],
       related=["diakiryxi", "apotelesma"]),

    _t("prodikastiki", "law", "Προδικαστική προσφυγή", "Pre-contractual appeal", None,
       "Η προσφυγή κατά πράξης του διαγωνισμού, πριν την υπογραφή της σύμβασης.",
       "An appeal against a tender act, lodged before the contract is signed.",
       ["Όποιος θίγεται από όρο της διακήρυξης ή από απόφαση αποκλεισμού ή "
        "κατακύρωσης μπορεί να προσφύγει ενώπιον της Ενιαίας Αρχής Δημοσίων "
        "Συμβάσεων (ΕΑΔΗΣΥ), η οποία απορρόφησε την ΑΕΠΠ.",
        "Οι προθεσμίες είναι σύντομες και αποκλειστικές, και η προσφυγή αναστέλλει "
        "τη διαδικασία. Γι' αυτό μια καθυστερημένη κατακύρωση δεν σημαίνει "
        "απαραίτητα αδράνεια του φορέα."],
       ["Anyone harmed by a term of the tender documents, or by an exclusion or award "
        "decision, may appeal to the Single Public Procurement Authority (ΕΑΔΗΣΥ), "
        "which absorbed the former ΑΕΠΠ.",
        "The deadlines are short and strict, and an appeal suspends the procedure. A "
        "late award therefore does not necessarily mean the authority is idle."],
       related=["apotelesma", "diapragmatefsi"]),

    _t("mataiosi", "law", "Ματαίωση διαδικασίας", "Cancellation of a procedure", None,
       "Η απόφαση του φορέα να μην ολοκληρώσει τον διαγωνισμό.",
       "The authority's decision not to complete a tender procedure.",
       ["Ένας διαγωνισμός μπορεί να ματαιωθεί για λόγους που ορίζει ο νόμος — καμία "
        "παραδεκτή προσφορά, μεταβολή των αναγκών, παρατυπία της διαδικασίας. Η "
        "ματαίωση αφορά τη διαδικασία, όχι μια ήδη υπογεγραμμένη σύμβαση.",
        "Στον Εξερευνητή οι ακυρωμένες πράξεις σημαίνονται ρητά και εξαιρούνται από "
        "τις υπενθυμίσεις προθεσμιών: δεν έχει νόημα να προετοιμάζεται προσφορά για "
        "διαγωνισμό που δεν υπάρχει πια."],
       ["A tender may be cancelled on grounds set out in the law — no admissible "
        "tenders, a change in requirements, an irregularity in the procedure. "
        "Cancellation concerns the procedure, not a contract already signed.",
        "In the Explorer cancelled acts are marked explicitly and excluded from "
        "deadline reminders: there is no sense preparing a bid for a tender that no "
        "longer exists."],
       related=["orthi-epanalipsi", "prokiryxi"]),
]

# --------------------------------------------------------------------------- #
# Lookups
# --------------------------------------------------------------------------- #
BY_SLUG = {t["slug"]: t for t in TERMS}


def get(slug: str) -> dict | None:
    return BY_SLUG.get(slug)


def sections_with_terms() -> list[dict]:
    """Sections in declared order, each with its terms in declared order.

    Declared order, not alphabetical: the sequence teaches — a reader who works
    down the page meets the registries before the acts and the acts before the
    procedures that produce them.
    """
    out = []
    for s in SECTIONS:
        terms = [t for t in TERMS if t["section"] == s["slug"]]
        if terms:
            out.append({**s, "terms": terms})
    return out


def related_of(term: dict) -> list[dict]:
    return [BY_SLUG[s] for s in term.get("related", []) if s in BY_SLUG]


def slugs() -> list[str]:
    return [t["slug"] for t in TERMS]
