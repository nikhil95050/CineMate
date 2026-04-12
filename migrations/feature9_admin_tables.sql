-- Feature 9: Admin Tables Migration
-- Run this in your Supabase SQL editor before deploying Feature 9.

-- 1. Admin registry
--    Stores chat_ids that are allowed to use admin commands.
--    Seed with your ADMIN_CHAT_IDS env values after running.
CREATE TABLE IF NOT EXISTS public.admins (
  chat_id   text NOT NULL,
  username  text,
  added_at  timestamp with time zone NOT NULL DEFAULT now(),
  CONSTRAINT admins_pkey PRIMARY KEY (chat_id)
);

-- 2. App config / feature flags
--    Key-value store for provider flags and other runtime settings.
--    Example rows:
--      provider.perplexity.enabled = 'true'
--      provider.omdb.enabled       = 'true'
--      provider.watchmode.enabled  = 'true'
CREATE TABLE IF NOT EXISTS public.app_config (
  key        text NOT NULL,
  value      text NOT NULL,
  updated_at timestamp with time zone NOT NULL DEFAULT now(),
  CONSTRAINT app_config_pkey PRIMARY KEY (key)
);

-- 3. Seed your admin(s) -- replace with your real Telegram chat_id(s)
-- INSERT INTO public.admins (chat_id, username) VALUES ('YOUR_CHAT_ID_HERE', 'your_username');

-- 4. Seed default provider flags (all enabled)
INSERT INTO public.app_config (key, value) VALUES
  ('provider.perplexity.enabled', 'true'),
  ('provider.omdb.enabled',       'true'),
  ('provider.watchmode.enabled',  'true')
ON CONFLICT (key) DO NOTHING;
