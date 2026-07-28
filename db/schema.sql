-- =============================================================================
-- CineMate Database Schema
-- Generated: 2026-07-28
-- Database: Supabase (PostgreSQL 15+)
-- =============================================================================

-- Users table: Telegram user profiles and taste data
CREATE TABLE IF NOT EXISTS public.users (
    chat_id text NOT NULL,
    username text NOT NULL DEFAULT 'User'::text,
    preferred_genres jsonb NOT NULL DEFAULT '[]'::jsonb,
    disliked_genres jsonb NOT NULL DEFAULT '[]'::jsonb,
    preferred_language text,
    preferred_era text,
    watch_context text,
    avg_rating_preference numeric,
    subscriptions jsonb NOT NULL DEFAULT '[]'::jsonb,
    user_taste_vector jsonb,
    updated_at timestamp with time zone NOT NULL DEFAULT now(),
    CONSTRAINT users_pkey PRIMARY KEY (chat_id)
);

-- Sessions table: per-user conversation state and questionnaire answers
CREATE TABLE IF NOT EXISTS public.sessions (
    chat_id text NOT NULL,
    session_state text NOT NULL DEFAULT 'idle'::text,
    question_index integer NOT NULL DEFAULT 0,
    pending_question text,
    answers_mood text,
    answers_genre text,
    answers_language text,
    answers_era text,
    answers_context text,
    answers_time text,
    answers_avoid text,
    answers_favorites text,
    answers_rating text,
    last_question_msg_id text,
    last_recs_json text NOT NULL DEFAULT '[]'::text,
    overflow_buffer_json text NOT NULL DEFAULT '[]'::text,
    sim_depth integer NOT NULL DEFAULT 0,
    updated_at timestamp with time zone NOT NULL DEFAULT now(),
    last_activity_at timestamp with time zone DEFAULT now(),
    CONSTRAINT sessions_pkey PRIMARY KEY (chat_id)
);

-- History table: movies recommended to each user
CREATE TABLE IF NOT EXISTS public.history (
    chat_id text NOT NULL,
    movie_id text NOT NULL,
    title text NOT NULL,
    year text NOT NULL DEFAULT ''::text,
    genres text NOT NULL DEFAULT ''::text,
    language text NOT NULL DEFAULT ''::text,
    rating text NOT NULL DEFAULT ''::text,
    recommended_at timestamp with time zone NOT NULL DEFAULT now(),
    watched boolean NOT NULL DEFAULT false,
    watched_at timestamp with time zone,
    CONSTRAINT history_pkey PRIMARY KEY (chat_id, movie_id)
);

-- Watchlist table: user-saved movies
CREATE TABLE IF NOT EXISTS public.watchlist (
    chat_id text NOT NULL,
    movie_id text NOT NULL,
    title text NOT NULL,
    year text NOT NULL DEFAULT ''::text,
    language text NOT NULL DEFAULT ''::text,
    rating text NOT NULL DEFAULT ''::text,
    genres text NOT NULL DEFAULT ''::text,
    added_at timestamp with time zone NOT NULL DEFAULT now(),
    CONSTRAINT watchlist_pkey PRIMARY KEY (chat_id, movie_id)
);

-- Feedback table: user likes/dislikes on recommendations
CREATE TABLE IF NOT EXISTS public.feedback (
    chat_id text NOT NULL,
    movie_id text NOT NULL,
    reaction_type text NOT NULL CHECK (reaction_type = ANY (ARRAY['like'::text, 'dislike'::text])),
    created_at timestamp with time zone NOT NULL DEFAULT now(),
    CONSTRAINT feedback_pkey PRIMARY KEY (chat_id, movie_id)
);

-- Movie metadata cache: enriched movie data from OMDb/TMDB
CREATE TABLE IF NOT EXISTS public.movie_metadata (
    movie_id text NOT NULL,
    data_json jsonb NOT NULL,
    source text DEFAULT NULL,
    popularity numeric,
    updated_at timestamp with time zone DEFAULT now(),
    CONSTRAINT movie_metadata_pkey PRIMARY KEY (movie_id)
);

-- User interactions log: every message exchange
CREATE TABLE IF NOT EXISTS public.user_interactions (
    id bigint NOT NULL DEFAULT nextval('user_interactions_id_seq'::regclass),
    chat_id text NOT NULL,
    username text,
    input_text text,
    bot_response text,
    intent text,
    latency_ms integer,
    request_id text,
    user_sent_at timestamp with time zone NOT NULL DEFAULT now(),
    bot_replied_at timestamp with time zone NOT NULL DEFAULT now(),
    timestamp timestamp with time zone NOT NULL DEFAULT now(),
    CONSTRAINT user_interactions_pkey PRIMARY KEY (id)
);

-- Error logs: structured error tracking
CREATE TABLE IF NOT EXISTS public.error_logs (
    id bigint NOT NULL DEFAULT nextval('error_logs_id_seq'::regclass),
    chat_id text,
    error_type text,
    error_message text,
    workflow_step text,
    intent text,
    request_id text,
    raw_payload text,
    timestamp timestamp with time zone NOT NULL DEFAULT now(),
    CONSTRAINT error_logs_pkey PRIMARY KEY (id)
);

-- Bot statistics: key-value counters
CREATE TABLE IF NOT EXISTS public.bot_stats (
    metric_name text NOT NULL,
    metric_value bigint NOT NULL DEFAULT 0,
    CONSTRAINT bot_stats_pkey PRIMARY KEY (metric_name)
);

-- API usage tracking: per-call external API accounting
CREATE TABLE IF NOT EXISTS public.api_usage (
    id bigint NOT NULL DEFAULT nextval('api_usage_id_seq'::regclass),
    chat_id text NOT NULL DEFAULT 'system'::text,
    provider text NOT NULL,
    action text NOT NULL,
    timestamp timestamp with time zone NOT NULL DEFAULT now(),
    prompt_tokens integer,
    completion_tokens integer,
    total_tokens integer,
    CONSTRAINT api_usage_pkey PRIMARY KEY (id)
);

-- Admins table: Telegram chat IDs with admin access
CREATE TABLE IF NOT EXISTS public.admins (
    chat_id text NOT NULL,
    username text,
    added_at timestamp with time zone NOT NULL DEFAULT now(),
    CONSTRAINT admins_pkey PRIMARY KEY (chat_id)
);

-- App config: feature flags and runtime configuration
CREATE TABLE IF NOT EXISTS public.app_config (
    key text NOT NULL,
    value text NOT NULL,
    updated_at timestamp with time zone NOT NULL DEFAULT now(),
    CONSTRAINT app_config_pkey PRIMARY KEY (key)
);

-- Admin website sessions
CREATE TABLE IF NOT EXISTS public.admin_sessions (
    session_id text PRIMARY KEY,
    created_at timestamp with time zone NOT NULL DEFAULT now(),
    expires_at timestamp with time zone NOT NULL,
    ip_address text
);

-- Aggregated daily provider metrics for admin dashboard
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

-- Per-user daily activity rollup
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

-- Schema version tracking
CREATE TABLE IF NOT EXISTS public.db_migrations (
    version text PRIMARY KEY,
    applied_at timestamp with time zone NOT NULL DEFAULT now()
);

-- =============================================================================
-- Indexes
-- =============================================================================

-- History: fast paginated lookups per user
CREATE INDEX IF NOT EXISTS idx_history_chat_rec
    ON public.history (chat_id, recommended_at DESC);

-- Feedback: efficient taste recompute queries
CREATE INDEX IF NOT EXISTS idx_feedback_chat_ts
    ON public.feedback (chat_id, created_at DESC);

-- API usage: date-range queries for admin dashboard
CREATE INDEX IF NOT EXISTS idx_api_usage_ts_provider
    ON public.api_usage (timestamp, provider);

CREATE INDEX IF NOT EXISTS idx_api_usage_chat_ts
    ON public.api_usage (chat_id, timestamp);

-- Error logs: recent error lookups
CREATE INDEX IF NOT EXISTS idx_error_logs_ts
    ON public.error_logs (timestamp DESC);

-- Users: taste vector queries for user segmentation
CREATE INDEX IF NOT EXISTS idx_users_taste_vector
    ON public.users USING GIN (user_taste_vector);

-- User interactions: per-user lookups
CREATE INDEX IF NOT EXISTS idx_user_interactions_chat_ts
    ON public.user_interactions (chat_id, timestamp DESC);

-- Movie metadata: source-based queries
CREATE INDEX IF NOT EXISTS idx_movie_metadata_source_ts
    ON public.movie_metadata (source, updated_at);

-- =============================================================================
-- RPC Functions
-- =============================================================================

-- Atomic stat increment (eliminates read-then-write race condition)
CREATE OR REPLACE FUNCTION public.increment_stat(stat_name text, increment_by integer DEFAULT 1)
RETURNS void
LANGUAGE sql
AS $$
  INSERT INTO public.bot_stats (metric_name, metric_value)
  VALUES (stat_name, increment_by)
  ON CONFLICT (metric_name)
  DO UPDATE SET metric_value = public.bot_stats.metric_value + increment_by;
$$;

-- Aggregate API usage into provider_metrics for the last N hours
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
            COUNT(*) FILTER (WHERE error_type IS NOT NULL) AS err_count
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
