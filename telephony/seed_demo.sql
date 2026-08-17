-- seed_demo.sql — wire two existing app_users to the demo extensions.
--
-- Edit the two usernames below to real accounts in your DB, then:
--     psql "$DATABASE_URL" -f telephony/seed_demo.sql
--
-- Roles in the demo:
--   caller_username  -> ext 1001. Its customer_profile.phone is set to match
--                       ext 1001's callerid in pjsip.conf (2101234567), so when
--                       it calls the agent, the screen-pop resolves to a name.
--   agent_username   -> ext 1002. Log in as this user in the browser to receive
--                       the incoming call + screen-pop.
--
-- SIP secrets here must match pjsip.conf (1001pass / 1002pass).

\set caller_username 'alice'
\set agent_username  'bob'

-- caller  -> ext 1001
INSERT INTO proc.sip_extension (user_id, extension, sip_user, sip_secret, display_name)
SELECT id, '1001', '1001', '1001pass', 'Demo Customer'
  FROM proc.app_user WHERE username = :'caller_username'
ON CONFLICT (user_id) DO UPDATE
   SET extension = EXCLUDED.extension, sip_user = EXCLUDED.sip_user,
       sip_secret = EXCLUDED.sip_secret, display_name = EXCLUDED.display_name,
       is_active = true, updated_at = now();

-- agent  -> ext 1002
INSERT INTO proc.sip_extension (user_id, extension, sip_user, sip_secret, display_name)
SELECT id, '1002', '1002', '1002pass', 'Agent B'
  FROM proc.app_user WHERE username = :'agent_username'
ON CONFLICT (user_id) DO UPDATE
   SET extension = EXCLUDED.extension, sip_user = EXCLUDED.sip_user,
       sip_secret = EXCLUDED.sip_secret, display_name = EXCLUDED.display_name,
       is_active = true, updated_at = now();

-- Give the caller a CRM profile whose phone matches ext 1001's callerid so the
-- screen-pop shows a named record. (Any app_user can hold the profile; here the
-- caller holds their own.)
INSERT INTO proc.customer_profile (user_id, full_name, phone, company)
SELECT id, 'Demo Customer', '2101234567', 'Demo Co Ltd'
  FROM proc.app_user WHERE username = :'caller_username'
ON CONFLICT (user_id) DO UPDATE
   SET phone = EXCLUDED.phone,
       full_name = COALESCE(proc.customer_profile.full_name, EXCLUDED.full_name),
       company   = COALESCE(proc.customer_profile.company,   EXCLUDED.company);

-- Confirm
SELECT e.extension, u.username, p.full_name, p.phone
  FROM proc.sip_extension e
  JOIN proc.app_user u ON u.id = e.user_id
  LEFT JOIN proc.customer_profile p ON p.user_id = e.user_id
 ORDER BY e.extension;
