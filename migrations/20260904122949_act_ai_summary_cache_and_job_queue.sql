-- migrations/20260904122949_act_ai_summary_cache_and_job_queue.sql
-- act ai summary cache and job queue
--
-- The AI Summary panel (docs/specs/ai-summary.md) reads a notice's full text and
-- published tables ONCE, extracts the things the feed does not carry — the
-- deadlines that are not final_submission_date, the weights behind a MEAT
-- criterion, the bid security, the specifications — and stores the result so
-- every later reader of that act is served from here and costs nothing.
--
-- THE CACHE KEY IS THE WHOLE DESIGN. A cached row is served only when its
-- input_hash equals the hash recomputed now, over: the full text, the published
-- extracted tables, the attachment set (empty until attachments reach prod),
-- the prompt version, the schema version, the model and the language. That one
-- rule invalidates on re-ingest, on a curator publishing a table, on a prompt
-- change and on a model switch — and on nothing else, so a page view is free.
-- It also protects the stored character offsets: they point into the exact text
-- the hash covers, so text and offsets can never drift apart.
--
-- Superseded payloads are NOT deleted. proc.act_ai_summary_history keeps what a
-- reader was shown last week; a customer who asks "your summary said X" needs an
-- answer, and a prompt change with no before/after is unreviewable.
--
-- The job table carries the same queue columns as proc.ingest_job and
-- proc.table_extract_job (see 20260710045935_job_queue_worker_columns.sql), so
-- worker.py drains it with the machinery it already has — claim with FOR UPDATE
-- SKIP LOCKED, heartbeat, cancel, exit code — and a 60-second model call never
-- runs inside a web request.
--
-- RUN ON BOTH local AND Supabase before pushing any code that reads these:
--   DATABASE_URL=...        python3 migrate.py up
--   DATABASE_URL=<supabase> python3 migrate.py up

BEGIN;

-- --------------------------------------------------------------------------
-- The cache: at most one live payload per act.
-- --------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS proc.act_ai_summary (
    adam            text PRIMARY KEY
                    REFERENCES proc.procurement_act(adam) ON DELETE CASCADE,
    input_hash      text        NOT NULL,   -- sha256 over every input; see header
    model           text        NOT NULL,
    prompt_version  integer     NOT NULL,
    schema_version  integer     NOT NULL,
    lang            text        NOT NULL DEFAULT 'el',
    -- {sections:[...], conflicts:[...], not_found:[...], truncated:{...}|null,
    --  rejected_n:int}. Section shape is app/ai_summary.py's catalogue, which is
    --  versioned by schema_version above — never widen it without bumping that.
    payload         jsonb       NOT NULL,
    n_sections      integer     NOT NULL DEFAULT 0,
    n_items         integer     NOT NULL DEFAULT 0,
    rejected_n      integer     NOT NULL DEFAULT 0,   -- items dropped by the quote gate
    input_tokens    integer,
    output_tokens   integer,
    -- Integer micro-DOLLARS, never a float: Anthropic bills in USD, and any
    -- euro figure stored here would bake in a conversion rate that ages badly.
    -- "What has this feature cost us" has to be an exact SUM.
    cost_micro_usd  bigint,
    generated_by    text,                              -- app_user.username
    generated_at    timestamptz NOT NULL DEFAULT now()
);

-- The only read path the detail page has: one act, then compare input_hash.
CREATE INDEX IF NOT EXISTS ix_act_ai_summary_generated
    ON proc.act_ai_summary (generated_at DESC);

-- --------------------------------------------------------------------------
-- Every payload ever served, including the ones now superseded.
-- --------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS proc.act_ai_summary_history (
    id              bigserial PRIMARY KEY,
    adam            text        NOT NULL,
    input_hash      text        NOT NULL,
    model           text        NOT NULL,
    prompt_version  integer     NOT NULL,
    schema_version  integer     NOT NULL,
    lang            text        NOT NULL DEFAULT 'el',
    payload         jsonb       NOT NULL,
    rejected_n      integer     NOT NULL DEFAULT 0,
    input_tokens    integer,
    output_tokens   integer,
    cost_micro_usd  bigint,
    generated_by    text,
    generated_at    timestamptz NOT NULL DEFAULT now()
);

-- No FK to procurement_act: history outlives the act row it describes.
CREATE INDEX IF NOT EXISTS ix_act_ai_summary_history_adam
    ON proc.act_ai_summary_history (adam, id DESC);

-- --------------------------------------------------------------------------
-- The queue. Same columns worker.py already knows how to drain.
-- --------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS proc.ai_summary_job (
    id               bigserial PRIMARY KEY,
    adam             text NOT NULL
                     REFERENCES proc.procurement_act(adam) ON DELETE CASCADE,
    status           text NOT NULL DEFAULT 'queued',   -- queued|running|done|error|cancelled|stale
    -- The input_hash the job was enqueued FOR. If the act's text changes while
    -- the job sits in the queue, the result answers a question nobody asked —
    -- the runner compares and gives up rather than caching a stale payload.
    input_hash       text,
    requested_by     text,                              -- app_user.username
    command          text[],                            -- db.py argv
    job_env          jsonb,
    log_text         text,
    worker_id        text,
    heartbeat_at     timestamptz,
    cancel_requested boolean NOT NULL DEFAULT false,
    exit_code        integer,
    last_error       text,
    queued_at        timestamptz NOT NULL DEFAULT now(),
    started_at       timestamptz,
    finished_at      timestamptz
);

-- The worker's claim scan (oldest queued row, FOR UPDATE SKIP LOCKED).
CREATE INDEX IF NOT EXISTS ix_ai_summary_job_queued
    ON proc.ai_summary_job (id) WHERE status = 'queued';

-- "Is a job already pending for this act?" — the guard that stops one act being
-- enqueued twice by two readers clicking at the same time.
CREATE INDEX IF NOT EXISTS ix_ai_summary_job_adam_live
    ON proc.ai_summary_job (adam) WHERE status IN ('queued', 'running');

-- The daily cap (AI_SUMMARY_DAILY_CAP) counts jobs started today.
CREATE INDEX IF NOT EXISTS ix_ai_summary_job_queued_at
    ON proc.ai_summary_job (queued_at DESC);

COMMIT;
