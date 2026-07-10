-- migrations/20260710114711_seed_code_list_from_official_docs.sql
-- seed code list from official docs
--
-- Authoritative KHMDHS enumerations from the official Open Data API docs
-- (https://cerpp.eprocurement.gov.gr/khmdhs-opendata/help). The app resolves
-- stored codes to these labels so coded fields render as text, not numbers.
-- domain names match the DB columns' concept. Idempotent (upsert on domain+code);
-- khmdhs_ingest also upserts here from live {key,value} pairs going forward.

BEGIN;

CREATE UNIQUE INDEX IF NOT EXISTS code_list_domain_code_uidx
    ON proc.code_list (domain, code);

INSERT INTO proc.code_list (domain, code, label_el) VALUES
  -- contractType
  ('contract_type','9','Υπηρεσίες'),
  ('contract_type','10','Έργα'),
  ('contract_type','12','Μελέτες'),
  ('contract_type','13','Προμήθειες'),
  ('contract_type','14','Τεχνικές ή λοιπές συναφείς Υπηρεσίες'),
  -- additionalContractTypes (different code space than contractType)
  ('additional_contract_type','1','Έργα'),
  ('additional_contract_type','2','Προμήθειες'),
  ('additional_contract_type','3','Υπηρεσίες'),
  ('additional_contract_type','4','Μελέτες τεχνικών έργων'),
  ('additional_contract_type','5','Τεχνικές ή λοιπές συναφείς Υπηρεσίες'),
  -- procedureType / typeOfProcedure
  ('procedure_type','1','Ανοιχτή διαδικασία'),
  ('procedure_type','2','Κλειστή διαδικασία'),
  ('procedure_type','4','Ανταγωνιστικός διάλογος'),
  ('procedure_type','6','Απευθείας ανάθεση'),
  ('procedure_type','7','Ανταγωνιστική διαδικασία με διαπραγμάτευση'),
  ('procedure_type','11','Σύμπραξη καινοτομίας'),
  ('procedure_type','12','Διαπραγμάτευση χωρίς προηγούμενη δημοσίευση'),
  ('procedure_type','13','Διαπραγμάτευση με προηγούμενη προκήρυξη διαγωνισμού'),
  ('procedure_type','18','Διαδικασία άρθρου 128 του ν.4412/16'),
  -- awardProcedure (justification)
  ('award_procedure','51','Οι ανάγκες δεν μπορούν να ικανοποιηθούν χωρίς προσαρμογή'),
  ('award_procedure','52','Περιλαμβάνει σχεδιασμό ή καινοτόμες λύσεις'),
  ('award_procedure','53','Συντρέχουν ειδικές περιστάσεις'),
  ('award_procedure','54','Οι τεχνικές προδιαγραφές δεν είναι δυνατόν να προκαθοριστούν'),
  ('award_procedure','55','Άγονη διαδικασία'),
  ('award_procedure','56','Άγονη διαδικασία'),
  ('award_procedure','57','Μοναδικό έργο τέχνης'),
  ('award_procedure','58','Απουσία ανταγωνισμού για τεχνικούς λόγους'),
  ('award_procedure','59','Προστασία αποκλειστικών δικαιωμάτων'),
  ('award_procedure','60','Κατεπείγουσα ανάγκη απρόβλεπτη'),
  -- criteriaCode (award criteria)
  ('criteria','1','Βάσει κόστους – βέλτιστη σχέση ποιότητας τιμής'),
  ('criteria','2','Βάσει τιμής'),
  ('criteria','3','Βάσει κόστους – κοστολόγηση κύκλου ζωής'),
  ('criteria','4','Βάσει τιμής – άλλο'),
  -- legalContext
  ('legal_context','4','ν.4412/2016 - Βιβλίο Ι - άνω των ορίων'),
  ('legal_context','5','ν.4412/2016 - Βιβλίο Ι - κάτω των ορίων'),
  ('legal_context','6','ν.4412/2016 - Βιβλίο ΙΙ - άνω των ορίων'),
  ('legal_context','7','ν.4412/2016 - Βιβλίο ΙΙ - κάτω των ορίων'),
  ('legal_context','10','ν.4413/2016 - Παραχωρήσεις'),
  -- contractingAuthority (type)
  ('contracting_authority','8','ΕΚΑΑ ή ΚΑΑ'),
  ('contracting_authority','9','ΝΠΔΔ'),
  ('contracting_authority','10','ΝΠΙΔ'),
  ('contracting_authority','12','Κεντρική Διοίκηση'),
  ('contracting_authority','13','Ανεξάρτητη αρχή'),
  -- classificationOfPublicLawOrganization
  ('classification_public_law_org','1','Κεντρική Κυβέρνηση'),
  ('classification_public_law_org','2','ΟΤΑ'),
  ('classification_public_law_org','3','ΟΚΑ'),
  ('classification_public_law_org','4','Εκτός Γενικής Κυβέρνησης'),
  -- noticeType
  ('notice_type','2','Προκήρυξη'),
  ('notice_type','3','Διακήρυξη'),
  ('notice_type','4','Πρόσκληση'),
  -- conductingProceedings
  ('conducting_proceedings','1','Ηλεκτρονική Διαδικασία'),
  ('conducting_proceedings','2','Μη ηλεκτρονική διαδικασία'),
  -- digitalPlatform
  ('digital_platform','3','ΕΣΗΔΗΣ Π&Υ'),
  ('digital_platform','4','ΕΣΗΔΗΣ ΔΕ'),
  ('digital_platform','5','CosmoOne'),
  ('digital_platform','6','iSupplies'),
  ('digital_platform','7','Άλλη'),
  -- duration unit of measure
  ('duration_unit','1','Ημέρες'),
  ('duration_unit','2','Εβδομάδες'),
  ('duration_unit','3','Μήνες'),
  ('duration_unit','4','Έτη'),
  -- centralizedMarkets / tools
  ('centralized_markets','1','Συμφωνία-πλαίσιο'),
  ('centralized_markets','2','Δυναμικό σύστημα αγορών'),
  ('centralized_markets','3','Ηλεκτρονικός Κατάλογος'),
  ('centralized_markets','4','Ηλεκτρονικός Πλειστηριασμός'),
  ('centralized_markets','8','Δεν χρησιμοποιείται'),
  -- yes/no (assignedContract, socialContract, centralGovernmentAuthority)
  ('yes_no','1','Ναι'),
  ('yes_no','2','Όχι'),
  -- contractingAuthorityActivity
  ('authority_activity','1','Γενικές δημόσιες υπηρεσίες'),
  ('authority_activity','2','Άμυνα'),
  ('authority_activity','3','Δημόσια τάξη και ασφάλεια'),
  ('authority_activity','4','Περιβάλλον'),
  ('authority_activity','5','Οικονομικές και δημοσιονομικές υποθέσεις'),
  ('authority_activity','6','Υγεία'),
  ('authority_activity','7','Στέγαση και υποδομές κοινής ωφέλειας'),
  ('authority_activity','8','Κοινωνική προστασία'),
  ('authority_activity','9','Αναψυχή'),
  ('authority_activity','10','Πολιτισμός και θρησκεία'),
  ('authority_activity','11','Εκπαίδευση'),
  ('authority_activity','12','Τυχόν άλλη δραστηριότητα'),
  ('authority_activity','13','Εξόρυξη άνθρακα και στερεών καυσίμων'),
  ('authority_activity','14','Ηλεκτροδότηση'),
  ('authority_activity','15','Ύδρευση'),
  ('authority_activity','16','Αερολιμένες'),
  ('authority_activity','17','Λιμένες'),
  ('authority_activity','18','Φυσικό αέριο/πετρέλαιο'),
  ('authority_activity','19','Αστικός σιδηρόδρομος/τραμ/τρόλεϊ/λεωφορείο'),
  ('authority_activity','20','Αέριο/θερμότητα'),
  ('authority_activity','21','Σιδηροδρομικές υπηρεσίες'),
  ('authority_activity','22','Ταχυδρομικές υπηρεσίες'),
  -- greenContracts
  ('green_contracts','1','Δεν εμπίπτει στο ΕΣΔ'),
  ('green_contracts','2','Εμπίπτει στο ΕΣΔ - Δεν έχουν υιοθετηθεί τα ΠΚ'),
  ('green_contracts','3','Εμπίπτει στο ΕΣΔ - Έχουν υιοθετηθεί τα ΠΚ'),
  -- goodServices (green categories)
  ('good_services','101','Χαρτί από ανακτημένες ίνες'),
  ('good_services','102','Χαρτί από παρθένες ίνες αειφόρου συγκομιδής'),
  ('good_services','103','Ηλεκτρονικοί υπολογιστές'),
  ('good_services','104','Οθόνες'),
  ('good_services','105','Εξοπλισμός απεικόνισης (εκτυπωτές/σαρωτές/πολυμηχανήματα)'),
  ('good_services','106','Εσωτερικός φωτισμός - Λαμπτήρες LED'),
  ('good_services','107','Κλιματιστικά μηχανήματα'),
  ('good_services','108','Λιπαντικά Αναγεννημένα'),
  ('good_services','109','Λιπαντικά Βιοαποικοδομήσιμα'),
  ('good_services','110','Μεταφορικά μέσα'),
  ('good_services','111','Οδοφωτισμός και σηματοδότες κυκλοφορίας'),
  ('good_services','112','Έπιπλα'),
  ('good_services','113','Προϊόντα κλωστοϋφαντουργίας'),
  ('good_services','114','Προϊόντα και υπηρεσίες συντήρησης δημόσιων χώρων'),
  ('good_services','115','Υποδομές διαχείρισης λυμάτων'),
  ('good_services','116','Ηλεκτρικός και ηλεκτρονικός εξοπλισμός υγειονομικής περίθαλψης'),
  ('good_services','117','Σχεδιασμός οδοποιίας, κατασκευή και συντήρηση'),
  ('good_services','118','Σχεδιασμός κτιρίων γραφείων, κατασκευή και διαχείριση')
ON CONFLICT (domain, code) DO UPDATE SET label_el = EXCLUDED.label_el;

COMMIT;
