"""call_script_text.py — the words of the first-call sales script.

docs/specs/call-script.md §7. Content, not UI chrome (same reasoning as
glossary.py): the language here is the language of the CALL, chosen on the
script itself, not the admin's interface language.

Tokens are written [[name]]. call_script.fill() refuses a text whose token has
no value, and the branch logic then offers a different text. An empty number
must never drop out of a spoken sentence («κερδίσατε  αναθέσεις»).

Changing a spoken text? Bump SCRIPT_VERSION: every logged result records the
version it was given, so the results of two wordings never mix.
"""
from __future__ import annotations

SCRIPT_VERSION = "1"

BRANDS = ("Promitheies.gr", "Tender Service")

TEXT: dict[str, dict[str, str]] = {
    "el": {
        # --- chrome --------------------------------------------------------
        "title": "Σενάριο πρώτης κλήσης",
        "h_stops": "Πριν καλέσετε",
        "h_opener": "1. Άνοιγμα",
        "h_hook": "2. Το ένα στοιχείο που λέτε",
        "h_discovery": "3. Ερωτήσεις",
        "h_close": "4. Επόμενο βήμα",
        "h_objections": "Αν σας πουν…",
        "h_donts": "Μην πείτε",
        "kind_say": "Πείτε",
        "kind_ask": "Ρωτήστε",
        "kind_confirm": "Επιβεβαιώστε",
        "kind_note": "Για εσάς",
        "ans_yes": "Ναι",
        "ans_no": "Όχι",
        "agent_placeholder": "(το όνομά σας)",
        "blocked": "Δεν εμφανίζεται σενάριο πώλησης.",

        # --- stops (red) ---------------------------------------------------
        "s_subscriber": "Ο πελάτης είναι ενεργός συνδρομητής: αυτή δεν είναι κλήση πώλησης.",
        "s_dnc": "Ο πελάτης ζήτησε στις [[dnc_date]] να μην τον ξανακαλέσουμε.",
        "s_gemi": "Στο ΓΕΜΗ η εταιρεία είναι «[[gemi_status]]». Μην προχωρήσετε σε πώληση.",

        # --- warnings (amber) ----------------------------------------------
        "w_register": ("Πριν καλέσετε: ελέγξτε τον αριθμό στο μητρώο του άρθρου 11 "
                       "του ν. 3471/2006 (όσοι έχουν δηλώσει ότι δεν θέλουν "
                       "τηλεφωνικές κλήσεις προώθησης)."),
        "w_nophone": "Δεν υπάρχει καταχωρισμένο τηλέφωνο.",
        "w_prior": "Δεν είναι η πρώτη κλήση: τελευταία στις [[prior_date]] — [[prior_outcome]].",
        "w_prior_plain": "Δεν είναι η πρώτη κλήση: τελευταία στις [[prior_date]].",
        "w_expired_sub": "Πρώην συνδρομητής: το σενάριο είναι γραμμένο για πρώτη κλήση.",
        "w_generated_email": ("Το email του λογαριασμού είναι προσωρινό "
                              "(@prospective.com): ζητήστε το πραγματικό."),

        # --- opener --------------------------------------------------------
        "intro": "Καλημέρα σας, [[agent]] από [[brand]].",
        "greet_contact": "Μιλάω με [[contact]];",
        "greet_company": "Μιλάω με την εταιρεία [[company]];",
        "greet_unknown": "Με ποιον μιλάω, παρακαλώ;",
        "body_self": ("Σας καλώ επειδή κάνατε εγγραφή στην πλατφόρμα μας στις "
                      "[[registered_on]]. Έχετε δύο λεπτά;"),
        "trial_soon": ("Η δοκιμαστική σας πρόσβαση λήγει στις [[trial_ends]], "
                       "γι' αυτό ήθελα να σας προλάβω."),
        "body_contractor": ("Παρακολουθούμε τους δημόσιους διαγωνισμούς όλης της "
                            "χώρας, και από τα δημόσια στοιχεία βλέπω ότι "
                            "συμμετέχετε σε διαγωνισμούς για [[top_group]]. "
                            "Έχετε δύο λεπτά να σας πω τι βλέπουμε ανοιχτό αυτή "
                            "τη στιγμή για εσάς;"),
        "body_contractor_plain": ("Παρακολουθούμε τους δημόσιους διαγωνισμούς όλης "
                                  "της χώρας, και από τα δημόσια στοιχεία βλέπω ότι "
                                  "η εταιρεία σας εμφανίζεται σε δημόσιες αναθέσεις. "
                                  "Έχετε δύο λεπτά να σας πω τι βλέπουμε ανοιχτό "
                                  "αυτή τη στιγμή για εσάς;"),
        "body_external": "Μια σύντομη ερώτηση: συμμετέχετε σε δημόσιους διαγωνισμούς;",
        "ext_yes": "Ωραία. Σε ένα λεπτό θα σας πω τι βλέπουμε ανοιχτό για εσάς.",
        "ext_no_note": "Πηγαίνετε στην ερώτηση «Έχετε σκεφτεί να συμμετάσχετε…».",
        "ext_wrong_label": "Δεν είναι αρμόδιος",
        "ext_wrong": "Με ποιον θα μπορούσα να μιλήσω γι' αυτό; Ποιο είναι το καλύτερο τηλέφωνο;",
        "ext_wrong_note": "Αποτέλεσμα: «Λάθος αριθμός / όχι αρμόδιος», με το σωστό όνομα στη σημείωση.",
        "confirm_afm": ("Στην εγγραφή σας δώσατε ένα ΑΦΜ. Μπορώ να επιβεβαιώσω σε "
                        "ποια εταιρεία αντιστοιχεί;"),
        "confirm_afm_note": ("Γράψτε την επωνυμία στη σημείωση. Η σύνδεση με το "
                             "ΓΕΜΗ γίνεται από την καρτέλα, όχι αυτόματα."),

        # --- hook ----------------------------------------------------------
        "h1": ("Αυτή τη στιγμή υπάρχει ανοιχτός διαγωνισμός από [[top_authority]] "
               "για «[[top_title]]», που κλείνει στις [[top_deadline]]. Ταιριάζει "
               "με όσα έχετε ήδη κερδίσει."),
        "h2_many": ("Μετρήσαμε [[n_good]] ανοιχτούς διαγωνισμούς που ταιριάζουν "
                    "με όσα έχετε κερδίσει."),
        "h2_one": "Βρήκαμε έναν ανοιχτό διαγωνισμό που ταιριάζει με όσα έχετε κερδίσει.",
        "h2_value_many": "Η συνολική τους αξία είναι [[value_good]].",
        "h2_value_one": "Η αξία του είναι [[value_good]].",
        "h2_soon_many": "[[n_soon]] από αυτούς κλείνουν μέσα σε 14 ημέρες.",
        "h2_soon_one": "Ένας από αυτούς κλείνει μέσα σε 14 ημέρες.",
        "h2_soon_only": "Κλείνει μέσα σε 14 ημέρες.",
        "h3": ("Από τα δημόσια στοιχεία, η αναθέτουσα με την οποία συνεργάζεστε "
               "πιο συχνά είναι: [[top_buyer]]. Θα θέλατε να βλέπετε κάθε νέο "
               "διαγωνισμό της την ημέρα που δημοσιεύεται;"),
        "h4": ("Στο ΓΕΜΗ η εταιρεία σας είναι καταχωρισμένη με ΚΑΔ «[[kad_label]]». "
               "Σωστά καταλαβαίνω ότι πουλάτε [[est_group]];"),
        "h4_yes_many": ("Τότε αυτή τη στιγμή υπάρχουν [[n_good_kad]] ανοιχτοί "
                        "διαγωνισμοί που μάλλον σας ενδιαφέρουν."),
        "h4_yes_one": "Τότε αυτή τη στιγμή υπάρχει ένας ανοιχτός διαγωνισμός που μάλλον σας ενδιαφέρει.",
        "h4_yes_none": ("Τότε, μόλις δημοσιευθεί νέος διαγωνισμός σε αυτό το "
                        "αντικείμενο, μπορείτε να τον βλέπετε την ίδια μέρα."),
        "h4_no": "Τι πουλάτε κυρίως, και σε ποιους πελάτες;",
        "h4_no_note": "Γράψτε την απάντηση στη σημείωση: διορθώνει την εκτίμηση του προφίλ.",
        "h5": "Τι πουλάτε κυρίως, και σε ποιους πελάτες;",

        # --- discovery -----------------------------------------------------
        "q1": "Πώς βρίσκετε σήμερα τους διαγωνισμούς που σας αφορούν;",
        "q1_self": "Μόνοι μας, στο ΕΣΗΔΗΣ / ΚΗΜΔΗΣ",
        "q1_other": "Με άλλη υπηρεσία",
        "q1_consultant": "Μέσω συμβούλου",
        "q1_none": "Δεν ψάχνουμε συστηματικά",
        "q2": "Σας έχει τύχει να δείτε έναν διαγωνισμό αργά, ή να χάσετε μια προθεσμία;",
        "q3": "Θέλετε να βρείτε νέες αναθέτουσες αρχές ή να επεκταθείτε σε νέες περιοχές;",
        "q4": "Πόσοι άνθρωποι ασχολούνται στην εταιρεία με τις προσφορές;",
        "q4_one": "Ένας",
        "q4_more": "Περισσότεροι",
        "q5": ("Έχετε σκεφτεί να συμμετάσχετε σε δημόσιους διαγωνισμούς; Τι σας "
               "έχει κρατήσει μέχρι τώρα;"),
        "q5_start": "Δεν ξέρουμε από πού να ξεκινήσουμε",
        "q5_yes": "Μας ενδιαφέρει",
        "q5_no": "Δεν μας ενδιαφέρει",
        "next_note": "Συνεχίστε στην επόμενη ερώτηση.",
        "see_o1_note": "Δείτε δίπλα: «Έχουμε ήδη άλλη υπηρεσία».",

        # --- pitch lines (one feature, in the customer's terms) -------------
        "p_search": ("Εμείς συγκεντρώνουμε σε ένα μέρος τους διαγωνισμούς από το "
                     "ΚΗΜΔΗΣ, τη Διαύγεια και το TED, και βλέπετε μόνο όσους "
                     "αφορούν το αντικείμενό σας."),
        "p_consultant": ("Πολλοί δουλεύουν με σύμβουλο. Με την πλατφόρμα βλέπετε κι "
                         "εσείς τους ίδιους διαγωνισμούς την ίδια μέρα, και "
                         "αποφασίζετε μαζί του πιο γρήγορα."),
        "p_alert": ("Μπορούμε να σας στέλνουμε με email τους νέους διαγωνισμούς "
                    "στο αντικείμενό σας, ώστε να μη χρειάζεται να ψάχνετε."),
        "p_deadline": ("Βάζετε τους διαγωνισμούς που σας ενδιαφέρουν στο ημερολόγιό "
                       "σας, και σας έρχεται υπενθύμιση πριν κλείσουν."),
        "p_fit": ("Για κάθε ανοιχτό διαγωνισμό σας δείχνουμε πόσο ταιριάζει με όσα "
                  "έχετε ήδη κερδίσει, και πόσες προσφορές παίρνει συνήθως η "
                  "αναθέτουσα."),
        "p_checklist": ("Για κάθε διαγωνισμό σας δίνουμε μια λίστα ελέγχου με όσα "
                        "ζητά η διακήρυξη — δικαιολογητικά, προθεσμίες, εγγυήσεις — "
                        "ώστε να μη σας ξεφεύγει τίποτα."),
        "p_team": ("Μπορούμε να στέλνουμε τις ειδοποιήσεις και στους συναδέλφους "
                   "σας, και ο καθένας έχει τη λίστα ελέγχου κάθε διαγωνισμού."),
        "p_start": ("Γι' αυτό υπάρχει η λίστα ελέγχου σε κάθε διαγωνισμό, και ένα "
                    "γλωσσάρι με τους όρους των δημόσιων συμβάσεων σε απλά "
                    "ελληνικά. Οι περισσότεροι ξεκινούν από έναν μικρό διαγωνισμό "
                    "στο αντικείμενό τους."),
        "close_polite": ("Κατανοητό. Σας ευχαριστώ πολύ για τον χρόνο σας· αν "
                         "αλλάξει κάτι, είμαστε εδώ."),
        "close_polite_note": "Αποτέλεσμα: «Δεν ενδιαφέρεται».",

        # --- close ---------------------------------------------------------
        "c1": ("Να κλείσουμε 20 λεπτά να σας δείξω την πλατφόρμα με τους δικούς "
               "σας διαγωνισμούς; Ποια μέρα σας βολεύει;"),
        "c1_note": "Αποτέλεσμα: «Κλείστηκε παρουσίαση», με την ημερομηνία.",
        "c2": ("Μπορώ να σας ανοίξω τώρα δοκιμαστική πρόσβαση, για να τη δείτε "
               "μόνοι σας. Σε ποιο email να έρχεται;"),
        "c2_note": ("Στην καρτέλα: γράψτε το email στα «Στοιχεία» και δώστε το "
                    "δοκιμαστικό προϊόν. Μετά πείτε: «Στη σελίδα εισόδου πατήστε "
                    "\"Στείλτε μου σύνδεσμο σύνδεσης με email\" και θα σας έρθει ο "
                    "σύνδεσμος.» Αποτέλεσμα: «Ξεκίνησε δοκιμή»."),
        "c3": ("Μπορώ να σας επεκτείνω τη δοκιμαστική πρόσβαση, για να τη δείτε "
               "με την ησυχία σας."),
        "c3_note": "Στην καρτέλα: «Προϊόν» → επέκταση.",
        "c4": ("Μπορώ τώρα, όσο μιλάμε, να σας ρυθμίσω μια ειδοποίηση, ώστε να σας "
               "έρχονται με email οι νέοι διαγωνισμοί στο αντικείμενό σας. Πείτε "
               "μου δύο-τρεις λέξεις για το τι πουλάτε."),
        "c4_note": "Στην καρτέλα «Ειδοποιήσεις»: αποθηκευμένη αναζήτηση και ειδοποίηση.",

        # --- objections ----------------------------------------------------
        "o1_q": "«Έχουμε ήδη άλλη υπηρεσία»",
        "o1": "Πολύ ωραία. Ποια χρησιμοποιείτε; Τι θα θέλατε να κάνει καλύτερα;",
        "o1_note": ("Μετά πείτε ΜΙΑ διαφορά που ταιριάζει στην απάντηση: λίστα "
                    "ελέγχου, ημερολόγιο, πόσες προσφορές παίρνει συνήθως η αναθέτουσα."),
        "o2_q": "«Δεν έχω χρόνο τώρα»",
        "o2": "Κανένα πρόβλημα. Πότε να σας ξανακαλέσω;",
        "o2_note": "Αποτέλεσμα: «Ξανακαλέστε», με την ημερομηνία.",
        "o3_q": "«Στείλτε μου ένα email»",
        "o3": "Βεβαίως. Σε ποια διεύθυνση να σας το στείλω;",
        "o3_note": "Αποτέλεσμα: «Θέλει υλικό με email». Το email γράφεται από την καρτέλα «Σύνταξη email».",
        "o4_q": "«Πόσο κοστίζει;»",
        "o4": ("Την τιμή θα σας τη στείλω γραπτώς, μαζί με την προσφορά, για να την "
               "έχετε μπροστά σας. Πρώτα θα ήθελα να δείτε αν σας είναι χρήσιμη — "
               "να κλείσουμε μια σύντομη παρουσίαση;"),
        "o4_note": "Μην αναφέρετε τιμή στο τηλέφωνο.",
        "o5_q": "«Πού βρήκατε το τηλέφωνό μου;»",
        "o5_self": "Από την εγγραφή σας στην πλατφόρμα μας.",
        "o5_contractor": ("Η εταιρεία σας εμφανίζεται σε δημόσιες αναθέσεις στο "
                          "ΚΗΜΔΗΣ, που είναι δημόσια στοιχεία, και τα στοιχεία "
                          "επικοινωνίας είναι από τη βάση αναδόχων μας."),
        "o5_external": "Από [[lead_source]].",
        "o5_external_unknown": "Θα το ελέγξω και θα σας ενημερώσω.",
        "o5_external_unknown_note": ("Η πηγή δεν είναι καταχωρισμένη: συμπληρώστε "
                                     "το «Πηγή/Προέλευση» στα Στοιχεία. Μην μαντέψετε."),
        "o5_optout": ("Αν δεν θέλετε να σας ξανακαλέσουμε, πείτε μου το και σας "
                      "βγάζουμε από τη λίστα κλήσεων."),
        "o6_q": "«Μη με ξανακαλέσετε»",
        "o6": ("Κατανοητό, σας ζητώ συγγνώμη για την ενόχληση. Δεν θα σας "
               "ξανακαλέσουμε."),
        "o6_note": "Αποτέλεσμα: «Να μην ξανακληθεί». Μην επιμείνετε.",

        # --- don'ts --------------------------------------------------------
        "d_price": "Τιμή. Η προσφορά στέλνεται γραπτώς.",
        "d_activity": ("Τι έχει κάνει ο πελάτης στην πλατφόρμα (συνδέσεις, "
                       "αναζητήσεις, ειδοποιήσεις)."),
        "d_competitors": "Ονόματα ανταγωνιστών, στην πρώτη κλήση.",
        "d_watching": "«Σας παρακολουθούμε». Τα στοιχεία αναθέσεων είναι δημόσια.",
        "d_estimate": ("Όσα βγαίνουν από τον ΚΑΔ ως γεγονός. Είναι εκτίμηση: "
                       "ρωτήστε, μην το δηλώσετε."),
        "d_address": "«Κύριε» ή «κυρία» από το όνομα μόνο. Χρησιμοποιήστε το όνομα όπως είναι.",
    },

    "en": {
        "title": "First-call script",
        "h_stops": "Before you call",
        "h_opener": "1. Opening",
        "h_hook": "2. The one fact you say",
        "h_discovery": "3. Questions",
        "h_close": "4. Next step",
        "h_objections": "If they say…",
        "h_donts": "Don't say",
        "kind_say": "Say",
        "kind_ask": "Ask",
        "kind_confirm": "Confirm",
        "kind_note": "For you",
        "ans_yes": "Yes",
        "ans_no": "No",
        "agent_placeholder": "(your name)",
        "blocked": "No sales script is shown.",

        "s_subscriber": "The customer is an active subscriber: this is not a sales call.",
        "s_dnc": "On [[dnc_date]] the customer asked not to be called again.",
        "s_gemi": "In ΓΕΜΗ the company is «[[gemi_status]]». Do not go ahead with a sale.",

        "w_register": ("Before calling: check the number against the register of "
                       "article 11 of Law 3471/2006 (people who opted out of "
                       "marketing calls)."),
        "w_nophone": "No phone number on record.",
        "w_prior": "Not the first call: the last one was on [[prior_date]] — [[prior_outcome]].",
        "w_prior_plain": "Not the first call: the last one was on [[prior_date]].",
        "w_expired_sub": "Former subscriber: this script is written for a first call.",
        "w_generated_email": ("The account email is a placeholder "
                              "(@prospective.com): ask for the real one."),

        "intro": "Good morning, this is [[agent]] from [[brand]].",
        "greet_contact": "Am I speaking with [[contact]]?",
        "greet_company": "Is this [[company]]?",
        "greet_unknown": "Who am I speaking with, please?",
        "body_self": ("I'm calling because you signed up on our platform on "
                      "[[registered_on]]. Do you have two minutes?"),
        "trial_soon": ("Your trial ends on [[trial_ends]], so I wanted to catch "
                       "you before then."),
        "body_contractor": ("We follow public tenders across the whole country, and "
                            "the public records show you bid for [[top_group]]. Do "
                            "you have two minutes for me to tell you what is open "
                            "for you right now?"),
        "body_contractor_plain": ("We follow public tenders across the whole "
                                  "country, and the public records show your company "
                                  "in public contract awards. Do you have two "
                                  "minutes for me to tell you what is open for you "
                                  "right now?"),
        "body_external": "One quick question: do you take part in public tenders?",
        "ext_yes": "Great. In a minute I'll tell you what we see open for you.",
        "ext_no_note": "Go to the question «Have you thought about taking part…».",
        "ext_wrong_label": "Not the right person",
        "ext_wrong": "Who could I speak to about this? What is the best number?",
        "ext_wrong_note": "Result: «Wrong number / not the right person», with the right name in the note.",
        "confirm_afm": ("When you signed up you gave us a VAT number (ΑΦΜ). Can I "
                        "confirm which company it belongs to?"),
        "confirm_afm_note": ("Write the company name in the note. Linking it to "
                             "ΓΕΜΗ is done on the card, never automatically."),

        "h1": ("Right now there is an open tender from [[top_authority]] for "
               "«[[top_title]]», closing on [[top_deadline]]. It matches what you "
               "have already won."),
        "h2_many": "We counted [[n_good]] open tenders that match what you have won.",
        "h2_one": "We found one open tender that matches what you have won.",
        "h2_value_many": "Together they are worth [[value_good]].",
        "h2_value_one": "It is worth [[value_good]].",
        "h2_soon_many": "[[n_soon]] of them close within 14 days.",
        "h2_soon_one": "One of them closes within 14 days.",
        "h2_soon_only": "It closes within 14 days.",
        "h3": ("According to the public records, the authority you work with most "
               "often is [[top_buyer]]. Would you like to see each new tender of "
               "theirs on the day it is published?"),
        "h4": ("In ΓΕΜΗ your company is registered under the activity code "
               "«[[kad_label]]». Am I right that you sell [[est_group]]?"),
        "h4_yes_many": "Then right now there are [[n_good_kad]] open tenders that are probably of interest to you.",
        "h4_yes_one": "Then right now there is one open tender that is probably of interest to you.",
        "h4_yes_none": ("Then, as soon as a new tender in that line is published, "
                        "you can see it the same day."),
        "h4_no": "What do you mainly sell, and to which customers?",
        "h4_no_note": "Write the answer in the note: it corrects the profile estimate.",
        "h5": "What do you mainly sell, and to which customers?",

        "q1": "How do you find the tenders that concern you today?",
        "q1_self": "Ourselves, on ΕΣΗΔΗΣ / ΚΗΜΔΗΣ",
        "q1_other": "Another service",
        "q1_consultant": "Through a consultant",
        "q1_none": "We don't search systematically",
        "q2": "Have you ever seen a tender too late, or missed a deadline?",
        "q3": "Would you like to find new contracting authorities or expand into new regions?",
        "q4": "How many people in the company work on bids?",
        "q4_one": "One",
        "q4_more": "More than one",
        "q5": ("Have you thought about taking part in public tenders? What has held "
               "you back so far?"),
        "q5_start": "We don't know where to start",
        "q5_yes": "We are interested",
        "q5_no": "Not interested",
        "next_note": "Go on to the next question.",
        "see_o1_note": "See alongside: «We already use another service».",

        "p_search": ("We bring together the tenders from ΚΗΜΔΗΣ, Diavgeia and TED in "
                     "one place, and you see only the ones in your line of business."),
        "p_consultant": ("Many companies work with a consultant. With the platform you "
                         "see the same tenders on the same day, and decide together "
                         "faster."),
        "p_alert": ("We can email you the new tenders in your line of business, so "
                    "you don't have to search."),
        "p_deadline": ("You put the tenders you care about in your own calendar, and "
                       "get a reminder before they close."),
        "p_fit": ("For every open tender we show how well it matches what you have "
                  "already won, and how many bids that authority usually receives."),
        "p_checklist": ("For every tender we give you a checklist of what the notice "
                        "asks for — documents, deadlines, guarantees — so nothing "
                        "slips."),
        "p_team": ("We can send the alerts to your colleagues too, and each of them "
                   "has the checklist of every tender."),
        "p_start": ("That is what the checklist on every tender is for, and a "
                    "glossary of public procurement terms in plain language. Most "
                    "companies start with one small tender in their line of business."),
        "close_polite": ("Understood. Thank you very much for your time; if anything "
                         "changes, we are here."),
        "close_polite_note": "Result: «Not interested».",

        "c1": ("Shall we book 20 minutes so I can show you the platform with your "
               "own tenders? Which day suits you?"),
        "c1_note": "Result: «Presentation booked», with the date.",
        "c2": ("I can open a trial for you right now, so you can look for yourself. "
               "Which email should it go to?"),
        "c2_note": ("On the card: enter the email under «Details» and grant the test "
                    "product. Then say: «On the sign-in page, click \"Email me a "
                    "sign-in link\" and the link will arrive.» Result: «Trial started»."),
        "c3": "I can extend your trial so you can look at it at your own pace.",
        "c3_note": "On the card: «Product» → extend.",
        "c4": ("While we are talking I can set up an alert, so new tenders in your "
               "line of business arrive by email. Give me two or three words for "
               "what you sell."),
        "c4_note": "On the «Alerts» tab: a saved search and an alert.",

        "o1_q": "«We already use another service»",
        "o1": "Great. Which one do you use? What would you like it to do better?",
        "o1_note": ("Then mention ONE difference that fits the answer: checklist, "
                    "calendar, how many bids the authority usually receives."),
        "o2_q": "«I don't have time now»",
        "o2": "No problem. When should I call you back?",
        "o2_note": "Result: «Call back», with the date.",
        "o3_q": "«Send me an email»",
        "o3": "Of course. Which address should I send it to?",
        "o3_note": "Result: «Wants material by email». Write it from the «Compose email» tab.",
        "o4_q": "«How much does it cost?»",
        "o4": ("I'll send you the price in writing, with the offer, so you have it in "
               "front of you. First I'd like you to see whether it's useful — shall "
               "we book a short presentation?"),
        "o4_note": "Do not quote a price on the phone.",
        "o5_q": "«Where did you get my number?»",
        "o5_self": "From your sign-up on our platform.",
        "o5_contractor": ("Your company appears in public contract awards on ΚΗΜΔΗΣ, "
                          "which are public records, and the contact details come "
                          "from our contractor database."),
        "o5_external": "From [[lead_source]].",
        "o5_external_unknown": "I'll check and let you know.",
        "o5_external_unknown_note": ("The source is not recorded: fill in "
                                     "«Source/Origin» under Details. Do not guess."),
        "o5_optout": ("If you'd rather we didn't call again, just tell me and we'll "
                      "take you off the call list."),
        "o6_q": "«Don't call me again»",
        "o6": "Understood, and I'm sorry for the trouble. We won't call you again.",
        "o6_note": "Result: «Do not call again». Do not insist.",

        "d_price": "A price. The offer is sent in writing.",
        "d_activity": "What the customer has done on the platform (logins, searches, alerts).",
        "d_competitors": "Competitors' names, on a first call.",
        "d_watching": "«We've been watching you». Award records are public.",
        "d_estimate": ("Anything from the activity code (ΚΑΔ) as a fact. It is an "
                       "estimate: ask, don't state."),
        "d_address": "A title (Mr / Ms) guessed from a name. Use the name as it is.",
    },
}
