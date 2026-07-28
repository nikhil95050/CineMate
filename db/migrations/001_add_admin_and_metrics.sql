-- =============================================================================
-- Migration 001: Admin website + metrics tables + performance indexes
-- Safe to run repeatedly (all statements use IF NOT EXISTS / IF EXISTS guards)
-- =============================================================================

-- Record this migration
INSERT INTO public.db_migrations (version) VALUES ('001_admin_and_metrics')
ON CONFLICT (version) DO NOTHING;

-- ---------------------------------------------------------------------------
-- New columns on existing tables
-- ---------------------------------------------------------------------------

ALTER TABLE public.movie_metadata
    ADD COLUMN IF NOT EXISTS source text DEFAULT NULL,
    ADD COLUMN IF NOT EXISTS popularity numeric DEFAULT NULL,
    ADD COLUMN IF NOT EXISTS updated_at timestamp with time zone DEFAULT now();

ALTER TABLE public.sessions
    ADD COLUMN IF NOT EXISTS last_activity_at timestamp with time zone DEFAULT now();

-- ---------------------------------------------------------------------------
-- New tables
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS public.admin_sessions (
    session_id text PRIMARY KEY,
    created_at timestamp with time zone NOT NULL DEFAULT now(),
    expires_at timestamp with time zone NOT NULL,
    ip_address text
);

CREATE TABLE IF NOT EXISTS public.provider_metrics (
    provider text NOT NULL,
    date date NOT NULL,
    calls integer NOT NULL DEFAULT 0,
    tokens integer NOT NULL DEFAULT 0,
    tokens_prompt integer NOT NULL DEFAULT 0,
    tokens_completion integer NOT NULL DEFAULT 0,
    cost_usd numeric NOT NULL DEFAULT 0,
    errors integer NOT NULL DEFAULT 0,
    latency_avg_ms numeric,
    latency_p50_ms numeric,
    PRIMARY KEY (provider, date)
);

CREATE TABLE IF NOT EXISTS public.user_activity_daily (
    chat_id text NOT NULL,
    date date NOT NULL,
    interactions integer NOT NULL DEFAULT 0,
    recs_received integer NOT NULL DEFAULT 0,
    recs_liked integer NOT NULL DEFAULT 0,
    recs_disliked integer NOT NULL DEFAULT 0,
    searches integer NOT NULL DEFAULT 0,
    PRIMARY KEY (chat_id, date)
);

CREATE TABLE IF NOT EXISTS public.db_migrations (
    version text PRIMARY KEY,
    applied_at timestamp with time zone NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- Indexes
-- ---------------------------------------------------------------------------

CREATE INDEX IF NOT EXISTS idx_history_chat_rec
    ON public.history (chat_id, recommended_at DESC);

CREATE INDEX IF NOT EXISTS idx_feedback_chat_ts
    ON public.feedback (chat_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_api_usage_ts_provider
    ON public.api_usage (timestamp, provider);

CREATE INDEX IF NOT EXISTS idx_api_usage_chat_ts
    ON public.api_usage (chat_id, timestamp);

CREATE INDEX IF NOT EXISTS idx_error_logs_ts
    ON public.error_logs (timestamp DESC);

CREATE INDEX IF NOT EXISTS idx_users_taste_vector
    ON public.users USING GIN (user_taste_vector);

CREATE INDEX IF NOT EXISTS idx_user_interactions_chat_ts
    ON public.user_interactions (chat_id, timestamp DESC);

CREATE INDEX IF NOT EXISTS idx_movie_metadata_source_ts
    ON public.movie_metadata (source, updated_at);

-- ---------------------------------------------------------------------------
-- RPC Function: Atomic stat increment
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION public.increment_stat(stat_name text, increment_by integer DEFAULT 1)
RETURNS void
LANGUAGE sql
AS $$
  INSERT INTO public.bot_stats (metric_name, metric_value)
  VALUES (stat_name, increment_by)
  ON CONFLICT (metric_name)
  DO UPDATE SET metric_value = public.bot_stats.metric_value + increment_by;
$$;

-- ---------------------------------------------------------------------------
-- RPC Function: Aggregate API usage into provider_metrics
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION public.aggregate_provider_metrics(since_hours integer DEFAULT 24)
RETURNS void
LANGUAGE plpgsql
AS $$
DECLARE
    today date := CURRENT_DATE;
    rec record;
BEGIN
    FOR rec IN
        SELECT
            provider,
            today AS metric_date,
            COUNT(*) AS call_count,
            COALESCE(SUM(total_tokens), 0) AS total_toks,
            COALESCE(SUM(prompt_tokens), 0) AS prompt_toks,
            COALESCE(SUM(completion_tokens), 0) AS completion_toks,
            COUNT(*) FILTER (
                WHERE (SELECT COUNT(*) FROM public.error_logs e
                       WHERE e.error_type LIKE provider || '%'
                         AND e.timestamp >= (NOW() - (since_hours || ' hours')::interval)) > 0
            ) AS err_count
        FROM public.api_usage
        WHERE timestamp >= (NOW() - (since_hours || ' hours')::interval)
        GROUP BY provider
    LOOP
        INSERT INTO public.provider_metrics
            (provider, date, calls, tokens, tokens_prompt, tokens_completion, errors)
        VALUES
            (rec.provider, rec.metric_date, rec.call_count, rec.total_toks,
             rec.prompt_toks, rec.completion_toks, rec.err_count)
        ON CONFLICT (provider, date)
        DO UPDATE SET
            calls = public.provider_metrics.calls + EXCLUDED.calls,
            tokens = public.provider_metrics.tokens + EXCLUDED.tokens,
            tokens_prompt = public.provider_metrics.tokens_prompt + EXCLUDED.tokens_prompt,
            tokens_completion = public.provider_metrics.tokens_completion + EXCLUDED.tokens_completion,
            errors = public.provider_metrics.errors + EXCLUDED.errors;
    END LOOP;
END;
$$;
