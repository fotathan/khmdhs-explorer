-- migrations/20261008145739_call_script.sql
-- The first-call sales script (docs/specs/call-script.md).
--
-- 1. proc.customer_call gets three columns for a call logged from the script:
--    script_version (the wording version), script_branch (origin|basis|hook,
--    FROZEN at call time: the brief changes daily, a result keeps the branch it
--    was given) and script_result (one of the fixed result codes). NULL on every
--    call logged any other way.
--
-- 2. proc.customer_profile gets do_not_call_at / do_not_call_by: the customer
--    asked us not to call again. Set only by the script's "do not call" result,
--    cleared only by an admin on the card. Never set automatically.
--
-- 3. creation_source backfill: an account holding a self-granted test product
--    (granted_by IS NULL — register_submit grants exactly that, admin grants
--    always name the admin) signed up on /register. From now on /register
--    writes 'register' itself and admin creation writes 'admin'. Only an EMPTY
--    creation_source is filled; 'OrgDB' (leads.py) is never touched.
--
-- No DO $$ blocks: the Supabase dashboard editor splits them.
BEGIN;

ALTER TABLE proc.customer_call
  ADD COLUMN IF NOT EXISTS script_version text,
  ADD COLUMN IF NOT EXISTS script_branch  text,
  ADD COLUMN IF NOT EXISTS script_result  text;

ALTER TABLE proc.customer_call
  DROP CONSTRAINT IF EXISTS customer_call_script_result_check;
ALTER TABLE proc.customer_call
  ADD CONSTRAINT customer_call_script_result_check CHECK (
    script_result IS NULL OR script_result = ANY (ARRAY[
      'no_answer', 'wrong_person', 'callback', 'not_interested',
      'send_material', 'demo_booked', 'trial_started', 'do_not_call']));

CREATE INDEX IF NOT EXISTS ix_customer_call_script
  ON proc.customer_call (script_branch, script_result)
  WHERE script_result IS NOT NULL;

ALTER TABLE proc.customer_profile
  ADD COLUMN IF NOT EXISTS do_not_call_at timestamptz,
  ADD COLUMN IF NOT EXISTS do_not_call_by bigint
    REFERENCES proc.app_user(id) ON DELETE SET NULL;

COMMENT ON COLUMN proc.customer_profile.do_not_call_at IS
  'The customer asked not to be called again (call script result). Cleared only by an admin.';

INSERT INTO proc.customer_profile (user_id, creation_source, updated_at)
SELECT u.id, 'register', now()
  FROM proc.app_user u
 WHERE u.role = 'customer'
   AND EXISTS (SELECT 1 FROM proc.user_subscription s
                WHERE s.user_id = u.id AND s.product_code = 'test'
                  AND s.granted_by IS NULL)
ON CONFLICT (user_id) DO UPDATE
   SET creation_source = 'register'
 WHERE proc.customer_profile.creation_source IS NULL
    OR btrim(proc.customer_profile.creation_source) = '';

COMMIT;
