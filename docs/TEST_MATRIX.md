# CineMate — E2E Live Test Matrix
**Feature 11: Manual & Live Testing Plan**
Chat ID used for tests: `1878846631` (both user and admin)

---

## Environment Prerequisites

| Service | Requirement |
|---|---|
| Telegram Bot | Token set, webhook registered |
| Redis (Upstash) | URL in `REDIS_URL` env var |
| Supabase | URL + API key in env vars |
| Perplexity API | Key set in env; sonar-pro access |
| OMDb API | Key set in env |
| Watchmode API | Key set in env |
| Worker process | `rq worker cinemate_intent_jobs` running |

---

## User Scenarios

### A. New User vs Returning User

| # | Scenario | Steps | Expected |
|---|---|---|---|
| A1 | New user first `/start` | Send `/start` from fresh chat ID | Welcome message, preferences prompt |
| A2 | Returning user `/start` | Send `/start` from existing chat ID | Personalized greeting, history preserved |
| A3 | Returning user gets recommendations | Complete questionnaire → `/movie` | Recommendations respect prior taste profile |

---

## Command Coverage

### B. Positive Cases — All Commands

| # | Command | Input | Expected |
|---|---|---|---|
| B1 | `/start` | `/start` | Onboarding or welcome-back message |
| B2 | `/help` | `/help` | Full command list, no errors |
| B3 | `/movie` | `/movie action thriller 2023` | 3–5 movie cards with inline buttons |
| B4 | `/search` | `/search Inception` | Movie detail card |
| B5 | `/trending` | `/trending` | Current trending list |
| B6 | `/surprise` | `/surprise` | Single random recommendation |
| B7 | `/history` | `/history` | Paginated watch history |
| B8 | `/watchlist` | `/watchlist` | Saved movies list |
| B9 | `/star` | `/star Christopher Nolan` | Nolan filmography cards |
| B10 | `/share` | Get recs → `/share` | Forwardable text card with ≤5 movies |
| B11 | `/rating` | `/rating 7` | Confirm min rating set |
| B12 | `like_<id>` callback | Tap Like button | Taste profile updated, confirmation |
| B13 | `dislike_<id>` callback | Tap Dislike button | Feedback recorded, confirmation |
| B14 | `watched_<id>` callback | Tap Watched button | Added to history |
| B15 | `save_<id>` callback | Tap Save button | Added to watchlist |
| B16 | `more_like_<id>` callback | Tap More Like This | Similar movie cards |
| B17 | Question engine | `/movie` → answer all Q prompts | Final recommendation based on answers |
| B18 | `/reset` | `/reset` | Session cleared, fresh start |

### C. Admin Commands (Chat ID: 1878846631)

| # | Command | Expected |
|---|---|---|
| C1 | `/admin_health` | Provider circuit states (closed/open) |
| C2 | `/admin_stats` | User count, interaction count |
| C3 | `/admin_clear_cache` | Redis cache cleared, confirmation |
| C4 | `/admin_errors` | Recent error log entries |
| C5 | `/admin_usage` | Per-provider call counts |
| C6 | `/admin_broadcast <msg>` | Confirmation prompt shown |
| C7 | Confirm broadcast | All users receive message |
| C8 | Cancel broadcast | No message sent |
| C9 | `/admin_disable_provider omdb` | OMDb circuit opens, fallback kicks in |
| C10 | `/admin_enable_provider omdb` | OMDb re-enabled, next call succeeds |

---

## Negative Cases

| # | Scenario | Input | Expected |
|---|---|---|---|
| N1 | `/star` without a name | `/star` | Usage hint message, no crash |
| N2 | `/star` with unknown name | `/star Xyzabc1234` | "Couldn't find" fallback |
| N3 | `/share` before any recs | `/share` (fresh session) | "Nothing to share yet" message |
| N4 | Malformed callback | Send raw `like_` (no ID) | Graceful error, no crash |
| N5 | Empty message | Send blank / sticker | No crash, no reply or polite fallback |
| N6 | Rapid messages (rate limit) | Send 15 messages in 60s | Rate-limit message shown after 12th |
| N7 | Duplicate update_id | Resend same Telegram update | Only processed once (dedup) |
| N8 | Very long input | Send 2000-char message | Gracefully handled, no truncation errors |
| N9 | `/rating` with non-number | `/rating excellent` | Friendly validation error |
| N10 | Admin command from non-admin | `/admin_health` from other chat ID | Access denied or silent ignore |

---

## Infrastructure Failure Scenarios

| # | Scenario | Simulation | Expected |
|---|---|---|---|
| I1 | Redis unavailable | Stop Redis, send `/trending` | Falls back to in-memory, no crash |
| I2 | Supabase unavailable | Blank `SUPABASE_URL`, send `/history` | In-memory path, no crash |
| I3 | OMDb provider down | `/admin_disable_provider omdb` → `/search Inception` | Fallback to Perplexity or friendly error |
| I4 | Perplexity provider down | `/admin_disable_provider perplexity` → freeform text | Semantic routing skipped, fallback message |
| I5 | Watchmode provider down | `/admin_disable_provider watchmode` → `/trending` | Streaming info missing but movie cards shown |
| I6 | Worker process killed mid-job | Kill worker during `/movie` | Job lost (known limitation), no crash to user |
| I7 | Provider circuit half-open | Wait 120s after 3 failures → send command | Probe allowed, circuit closes on success |

---

## Rate Limit Test Procedure

```bash
# Send 15 messages in rapid succession
for i in $(seq 1 15); do
  curl -s -X POST https://api.telegram.org/bot<TOKEN>/sendMessage \
    -d chat_id=1878846631 -d text="/trending" &
done
```
Expected: Messages 1–12 processed; messages 13–15 receive rate-limit notice.

---

## Graceful Shutdown Verification

```bash
# 1. Enqueue several heavy jobs (send /movie requests)
# 2. Send SIGTERM to worker
kill -SIGTERM $(pgrep -f "rq worker")
# 3. Inspect logs — no "job lost" errors for in-flight jobs
# Expected: Worker drains current job, exits cleanly
```

---

## Confirmed Working Flows

- Webhook → dedup → rate-limit → enqueue → worker dispatch
- All 25+ intents correctly routed in worker_service
- Question engine full lifecycle (start → answer → recommendations)
- History & watchlist CRUD (in-memory + Supabase)
- Like / dislike / min_rating feedback recorded
- Star filmography with graceful empty fallback
- Share card built, capped at 5 items, N/A streaming suppressed
- All admin commands dispatched (health, stats, cache, errors, usage, broadcast, provider toggle)
- Circuit-breaker: opens at 3 failures, half-open after 120s, closes on success
- Daily budget guard per provider
- Semantic routing with recursion guard and Redis cache
- LoggingService error context captured, truncated, no stack trace leaks
- Redis fallback to in-memory for all operations
- Supabase fallback to in-memory repos
- Queue inline mode (CINEMATE_INLINE_JOBS=1) executes jobs when RQ unavailable

---

## Known Limitations

1. **In-flight job loss on worker crash**: RQ jobs not configured with `retry` — a worker killed mid-job loses that job. Fix: add `Retry(max=3, interval=30)` in `enqueue_job`.
2. **Semantic routing only on fallback**: Deliberate — semantic is not called for known intents.
3. **Daily budget in-memory only when Supabase down**: `increment_daily_calls` writes to in-memory `AdminRepository` only — resets on restart.
4. **Broadcast silent when Supabase down**: No error surfaced to admin (safe, but opaque).
5. **No webhook signature verification**: Telegram `X-Telegram-Bot-Api-Secret-Token` header not validated. Recommended for production hardening.

---

## Optional Follow-up Refactors

| Priority | Item |
|---|---|
| High | Add `Retry(max=3, interval=30)` to `enqueue_job` for RQ job resilience |
| High | Add Telegram webhook secret token header validation in `main.py` |
| Medium | Replace `asyncio.get_event_loop().run_until_complete` in tests with `pytest-asyncio` |
| Medium | Add structured `request_id` propagation through all handler calls |
| Medium | Add `/admin_backup_session` for exporting user data |
| Low | Add integration test using `fakeredis` for real Redis behaviour |
| Low | Move admin allowlist to `app_config` table for dynamic admin management |
| Low | Add OpenTelemetry spans around provider calls for distributed tracing |
