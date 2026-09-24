-- certificate_reminders
--
-- Evaluation layer, slice 2 (docs/specs/evaluation-layer.md §10): customers
-- manage their own certificates on /account/certificates, and may ask to be
-- emailed before one expires.
--
--   * certificate_alert: the customer's opt-in. OPT-IN, default off (no row =
--     off; lang = the UI language when it was switched on, the language
--     the reminder is written in): deliverability (SPF/DKIM/DMARC, unsubscribe) is not done, so no
--     customer gets this mail without having asked for it.
--   * certificate_expiry_notice: the ledger of reminders that actually LEFT —
--     one row per certificate × mark (days before expiry) × the valid_until it
--     was sent for. Written only after a send, so a failed run retries; the
--     date in the key means a renewal (new valid_until) re-arms every mark.
--     The same pattern as proc.digest_deadline_notice.
--   * email_template 'cert_expiry' (el/en): the admin-editable wording. The
--     list of certificates is placed by email_cert_expiry.html, never by the
--     fragment.
--
-- No DO $$ blocks: the Supabase dashboard editor splits them.
-- Do NOT tick "Run and enable RLS" for these tables in Supabase: with no
-- policies, every app INSERT is refused (proc.onboarding, 2026-09-22).

BEGIN;

CREATE TABLE IF NOT EXISTS proc.certificate_alert (
    user_id     bigint PRIMARY KEY
                REFERENCES proc.app_user(id) ON DELETE CASCADE,
    enabled     boolean NOT NULL DEFAULT false,
    lang        text    NOT NULL DEFAULT 'el' CHECK (lang IN ('el', 'en')),
    updated_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS proc.certificate_expiry_notice (
    certificate_id  bigint  NOT NULL
                    REFERENCES proc.company_certificate(id) ON DELETE CASCADE,
    mark_days       integer NOT NULL CHECK (mark_days > 0),
    valid_until     date    NOT NULL,
    sent_at         timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (certificate_id, mark_days, valid_until)
);

COMMENT ON TABLE proc.certificate_alert IS
    'Opt-in to certificate expiry emails. No row or enabled=false = no mail.';
COMMENT ON TABLE proc.certificate_expiry_notice IS
    'Certificate expiry reminders that were SENT: one per certificate, mark and valid_until. A new valid_until re-arms.';

INSERT INTO proc.email_template (slug, lang, name, subject, body_html) VALUES
  ('cert_expiry', 'el', 'Λήξη πιστοποιητικού',
   'Πιστοποιητικό σας λήγει σύντομα',
   '<p>Καλημέρα [[full_name]],</p>'
   '<p>Ένα ή περισσότερα πιστοποιητικά στο προφίλ σας λήγουν σύντομα. '
   'Ένας διαγωνισμός απαιτεί συνήθως να ισχύουν κατά την καταληκτική '
   'ημερομηνία υποβολής.</p>'),
  ('cert_expiry', 'en', 'Certificate expiry',
   'A certificate of yours expires soon',
   '<p>Hello [[full_name]],</p>'
   '<p>One or more certificates in your profile expire soon. A tender '
   'usually requires them to be valid on its closing date.</p>')
ON CONFLICT (slug, lang) DO NOTHING;

COMMIT;
