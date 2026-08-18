-- migrations/20260818120000_call_recording_transcript_summary.sql
-- call recording, transcript and AI summary columns
--
-- Post-call AI summarisation (see TELEPHONY_RUNBOOK.md → "Call summarisation").
-- A call is recorded by Asterisk (MixMonitor → recording_path), transcribed
-- (speech-to-text), then summarised by the Claude API. The result is stored on
-- the existing proc.customer_call row and shown on the CRM call views.
--
-- summary_status drives the CRM UI: NULL = never run, 'queued'/'running' =
-- in flight (a background thread owns it), 'done' = summary present,
-- 'error' = failed (summary_error has the reason). Everything is additive and
-- idempotent; the feature is inert until TELEPHONY recording + an STT backend +
-- ANTHROPIC_API_KEY are configured.
--
-- Wrap the body so a failure leaves nothing half-applied. Idempotent throughout.

BEGIN;

ALTER TABLE proc.customer_call
    ADD COLUMN IF NOT EXISTS recording_path  text,         -- server path/URI to the recorded audio
    ADD COLUMN IF NOT EXISTS transcript      text,         -- full speech-to-text transcript
    ADD COLUMN IF NOT EXISTS summary         text,         -- Claude-generated summary
    ADD COLUMN IF NOT EXISTS summary_model   text,         -- model id that produced the summary
    ADD COLUMN IF NOT EXISTS summary_status  text,         -- NULL|queued|running|done|error
    ADD COLUMN IF NOT EXISTS summary_error   text,         -- failure reason when status='error'
    ADD COLUMN IF NOT EXISTS transcribed_at  timestamptz,
    ADD COLUMN IF NOT EXISTS summarized_at   timestamptz;

COMMIT;
