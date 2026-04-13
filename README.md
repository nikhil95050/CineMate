CineMate Telegram Bot – Live Testing Guide and README Draft
1. Repository and Architecture Overview
CineMate is a Telegram movie‑recommendation bot built on FastAPI for webhook handling, RQ + Redis for background jobs, Supabase for persistence, and multiple external providers: Perplexity (LLM recommendations and semantic routing), OMDb (movie metadata), and Watchmode (streaming availability).
Core request flow is: Telegram update → /webhook/{BOT_TOKEN} in main.py → normalization and intent detection → queuing via queue_service.enqueue_job → async execution of worker_service.run_intent_job which dispatches to specific handlers and services.
The repository is organized into well‑defined layers: handlers (Telegram commands and callbacks), services (business logic and provider orchestration), repositories (Supabase‑backed storage with in‑memory fallbacks), clients (Perplexity, OMDb, Watchmode, Telegram), and models (Pydantic domain objects).[/cite:2]

2. Local Test Environment Setup
2.1 Clone and install
Clone the repo and create a virtual environment:

bash
git clone https://github.com/nikhil95050/CineMate.git
cd CineMate
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
Install dependencies:

bash
pip install -r requirements.txt
The project uses FastAPI, httpx, Redis/RQ, and pytest; it also installs the package itself as cinemate in editable mode.

2.2 Required environment variables
Use .env.example as the template and create a .env file in the repo root.

Minimum for local end‑to‑end testing (long‑polling, no external persistence):

TELEGRAM_BOT_TOKEN – from BotFather for your bot.

ENABLE_PERPLEXITY, ENABLE_TRAILERS, ENABLE_EXPLANATIONS – optional feature flags (default on if unset).

ADMIN_CHAT_IDS – include at least your own chat id for admin command tests (defaults to 1878846631).

CINEMATE_INLINE_JOBS=1 – run jobs inline without Redis/RQ for simplest local testing.

For full integration tests (recommended for infra scenarios in section 8):

Supabase: SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY.

Redis: REDIS_URL (or UPSTASH_REDIS_URL); optional REDIS_API_KEY/REDIS_REST_URL if you use Upstash helpers.

Perplexity: PERPLEXITY_API_KEY (with sonar/sonar‑pro access).

OMDb: OMDB_API_KEY.

Watchmode: WATCHMODE_API_KEY.

CINEMATE_QUEUE_NAME=cinemate_intent_jobs (matches defaults in queue and worker).

Use /debug/start once the app is running to verify readiness: it checks Telegram token, Supabase configuration, session service, and sends a test /start to the first admin id.

2.3 Running the bot locally (recommended: long‑polling)
For local manual testing you do not need a public webhook; use long‑polling via run_local.py:

Ensure .env is present and activated (dotenv is loaded at startup).

Start the local runner:

bash
python run_local.py
run_local.py uses Telegram getUpdates, normalizes each update (messages and callback queries), acknowledges callbacks, and calls services.worker_service.run_intent_job directly; there is no FastAPI or ngrok required.

Open the Telegram app, start a chat with your bot, and send /start – logs in the terminal should show detected intents and completion.

If you prefer webhook mode (e.g., with Render/ngrok): run uvicorn main:app and register the webhook as https://<public-url>/webhook/<TELEGRAM_BOT_TOKEN>; main verifies the path token and processes updates as in production.

2.4 Background worker (for non‑inline mode)
If CINEMATE_INLINE_JOBS is unset/false, queue_service.enqueue_job pushes jobs to a Redis queue and they are processed by an RQ worker.

Start Redis and ensure REDIS_URL is configured.

Start the worker in a second terminal:

bash
python rq_worker.py
This connects to Redis and listens on the queue name from CINEMATE_QUEUE_NAME.

In webhook mode, FastAPI will return quickly while the worker executes intents; in long‑polling mode you normally rely on inline jobs and do not need the worker.

3. High‑Level Feature Map (Intent → Handler → Services)
The key user‑visible features and their implementation chain are:

Feature	User entry	Intent	Handler	Core services	Notes
Onboarding question engine	/start	start then questioning	user_handlers.handle_start, rec_handlers.handle_questioning	SessionService, UserService, RecommendationService	Collects mood/genre/language/etc. then sends tailored recs.
Reset session	/reset	reset	user_handlers.handle_reset	SessionService	Clears session, keeps user profile.
Help guide	/help	help	user_handlers.handle_help	–	Static HTML guide to commands.
Similar‑movie recommendations	/movie <title>	movie	movie_handlers.handle_movie	RecommendationService, DiscoveryService	LLM + OMDb pipeline seeded by given movie.
Trending movies	/trending	trending	movie_handlers.handle_trending	RecommendationService, DiscoveryService	Trending/critically acclaimed recent movies.
Surprise me	/surprise	surprise	movie_handlers.handle_surprise	RecommendationService, DiscoveryService	Hidden gems, optionally avoid disliked genres.
More like this	callback more_like_<id>	more_like	movie_handlers.handle_more_like	RecommendationService, SessionService, UserService	Uses last recs to derive seed title and exclude already seen movies.
More suggestions	callback more_suggestions_action	more_suggestions	movie_handlers.handle_more_suggestions	RecommendationService	Drains overflow buffer or re‑discovers trending.
History	/history and history_pN callbacks	history	history_handlers.handle_history	MovieService, HistoryRepository	Paginated text list with Prev/Next inline buttons.
Watchlist	/watchlist and watchlist_pN callbacks	watchlist	history_handlers.handle_watchlist	MovieService, WatchlistRepository	Paginated watchlist with Prev/Next.
Mark watched	callback watched_<id>	watched	history_handlers.handle_watched	MovieService	Marks history row as watched and confirms.
Save to watchlist	callback save_<id>	save	history_handlers.handle_save	MovieService, WatchlistRepository	Uses session.last_recs or history as source; avoids duplicates.
Like / dislike	like_<id>, dislike_<id>	like, dislike	feedback_handlers.handle_like, handle_dislike	FeedbackRepository, UserService	Logs reactions, updates disliked genres, recomputes taste profile.
Min rating	/rating X or /min_rating X	min_rating	feedback_handlers.handle_min_rating	UserService	Validates 0–10 and stores rating preference.
Star filmography	/star <name>	star	discovery_handlers.handle_star	DiscoveryService, HistoryService, SessionService	Uses LLM + OMDb, persists to history and last recs.
Share card	/share	share	discovery_handlers.handle_share	SessionService	Builds forwardable text card from last recs, up to 5 movies.
Admin health/stats/cache/errors/usage	/admin_*	admin_*	admin_handlers & broadcast_handlers	AdminService, HealthService	Restricted by admin_only decorator and AdminRepository.
Semantic fallback	long free‑text	fallback → semantic	worker semantic routing	SemanticService, Perplexity	One‑shot classification into other intents for longer messages.
This mapping underpins all the test cases defined in the live test matrix and in the guide below.

4. Global Test Preconditions
Before running feature‑level tests in sections 5–8, ensure:

run_local.py is running and connected to your bot (or FastAPI + webhook is working).

.env is correctly populated and TELEGRAM_BOT_TOKEN is valid.

For admin tests, your Telegram chat id is included in ADMIN_CHAT_IDS and you are using that chat for /admin_* commands.

For provider‑level tests (Perplexity, OMDb, Watchmode), the corresponding API keys are set; otherwise expect graceful fallbacks to local metadata or friendly error behaviour.

Where this guide references "history stored" or "watchlist stored", in inline/no‑Supabase mode this is in memory only; in full mode it persists via Supabase repositories.

5. User Onboarding and Core Commands
5.1 New vs returning user (/start)
ID	Preconditions	User input	Steps	Expected output	Validation
A1	Fresh chat id with no prior session/user row (use a new Telegram account or clear Supabase for that id).	/start	1) Send /start in chat.	Bot resets session, creates/updates user, sets session_state="questioning", question_index=0, then sends a rich HTML welcome message followed by the first question from the question engine.	Check you receive a welcome paragraph referencing "CineMate" and a follow‑up message starting with Step 1/ and buttons from QUESTIONS[0] (mood options). Confirm session row in storage has session_state="questioning" and question_index=0 if you inspect Supabase or logs.
A2	Existing chat id with prior history and answers.	/start	1) After completing at least one onboarding run, send /start again.	Session is reset, a fresh question flow starts, but past history and preferences on the user profile are preserved.	Use /history and /watchlist before and after /start to confirm previous items remain; question index is reset to 0, and new answers overwrite answers_* fields in the session. Watch logs for reset_session then new questioning state.
A3	Returning user whose taste profile has some likes/dislikes and ratings.	Complete question flow → /movie	1) Run through the question engine once (see 5.2). 2) After receiving recommendations, call /movie <title> for a movie you like.	Recommendations reflect your taste (mood/genre etc.) and prior feedback, since RecommendationService filters by disliked genres and min rating and uses session answers.	Inspect at least one recommended movie: genres should avoid ones you explicitly disliked; if you set a high min rating via /rating (section 6), recommendations should all meet or exceed that rating. Use logs or DB to confirm disliked_genres and avg_rating_preference are applied in _movie_passes_filters and _resolve_min_rating.
5.2 Question engine flow
Precondition: Run /start once; you should see Step 1/… with mood options.

For each question:

Tap options as inline buttons (e.g., multiple genres; Done/Skip for multi‑select).

Observe that genre question allows toggling options (checkmarks), and Done advances to next question.

At the last question (rating), pick an option or type a free‑form answer when options are empty.

After the final question, the bot sends a message "Reviewing my notes and scanning the archives…" and then one or more movie cards with Like/Dislike/Save/Watched/More like this buttons plus a trailing "More suggestions" button.

Validation:

While answering, re‑tap one of the genre options: check that its checkmark toggles correctly and the next _send_current_question call reflects the updated selection. Session answers_genre should be a comma‑separated list of selected genres.

After completion, verify session.last_recs_json contains the recommendations and overflow_buffer_json holds extra candidates (in Supabase or logs), as set by RecommendationService.

Trigger more_suggestions_action and confirm additional cards appear and last_recs is extended without duplicates.

5.3 Help and reset
ID	Preconditions	User input	Expected output	Validation
B2	Any chat state.	/help	Bot returns a static HTML message listing /start, /search, /movie, /trending, /surprise, /history, /watchlist, /reset.	Verify the text matches the commands actually implemented in worker_service and detect_intent (no dangling or missing commands). Use this as the baseline for documenting bot capabilities.
B18	Any active session.	/reset	Session is cleared via SessionService.reset_session, and bot sends a friendly confirmation encouraging /start again.	After /reset, send q_* callback (e.g. old question button) from an earlier message; handle_questioning should ignore it when session_state is no longer questioning (no further question messages). This ensures stale callbacks are safe.
6. Recommendation Features and Feedback
6.1 /movie and semantic /search
ID	Preconditions	User input	Steps	Expected output	Validation
B3	Perplexity and OMDb keys configured; optional Watchmode key for streaming lines.	/movie Inception	1) Send /movie Inception. 2) Observe "Finding movies similar to Inception…" then movie cards.	3–5 enriched cards showing title, year, star rating, genres, truncated plot, curator reason, and possibly streaming and trailer info; each card has Like/Dislike/Save/Watched/More like this buttons plus a trailing "More suggestions" message.	Confirm the cards correspond to similar movies in theme or style; check that callbacks use like_<movie_id>, save_<movie_id>, etc., and that pressing them triggers appropriate handlers (see 6.3). Verify OMDb and Watchmode are called by checking logs and that metadata like rating and streaming platforms appear when keys are configured; when keys are missing, cards still show basic title/year/reason via LLM stubs.
B4	Same as B3.	/search Inception	1) Send /search Inception.	Since detect_intent maps /search to search which is dispatched to handle_movie, behaviour matches /movie (seed search).	Verify that both /movie Inception and /search Inception produce a comparable set of cards; semantic routing may also classify free‑text like "recommend movies like Inception" to movie_search or search, which also hits handle_movie. This confirms fallback semantic behaviour.
6.2 Trending and surprise
ID	Preconditions	User input	Expected output	Validation
B5	Perplexity key configured.	/trending	Bot sends "Fetching what's trending…" then cards for recent popular movies.	Check that movies are recent (description mentions last 12 months); when Perplexity fails, DiscoveryService falls back to movie_metadata table and still returns curated movies with a standard "Curated from our local library" reason.
B6	As above; optionally set some disliked genres via dislikes.	/surprise	Bot sends "Picking a surprise for you…" then eclectic recommendations.	Verify that if you have disliked certain genres (e.g., Horror) via dislike_*, surprise picks avoid those genres when possible. If Perplexity fails, fallback behaves like trending but may ignore dislikes; note this limitation when validating.
6.3 Like, dislike, watched, save, more like this, more suggestions
Trigger any recommendation flow (/movie, /trending, /surprise, or the question engine) and receive a batch of cards.

For each button type, test both valid and edge cases:

ID	Action	Preconditions	Input	Expected output	Validation
B12	Like	A card with callbacks present.	Tap "👍 Like".	Bot sends a short confirmation message and records a "like" reaction in FeedbackRepository; it schedules recompute_taste_profile in the background.	Confirm callback spinner disappears, you see a message like "Liked <title>!", and no crash occurs even if repositories are unavailable – errors are logged only. Optionally inspect Supabase feedback table for the new row.
B13	Dislike	Same as B12.	Tap "👎 Dislike".	Confirmation message; feedback row logged with "dislike"; user profile’s disliked_genres is updated with the movie’s genres.	Confirm repeated dislikes of movies in the same genre add unique genre strings to user.disliked_genres without duplicates. Later /surprise and question‑engine recommendations should avoid these genres. Ensure behaviour is graceful when history/session data is missing (title/genres fall back to movie id or empty strings).
B14	Watched	Same as B12.	Tap "✅ Watched".	Movie is marked watched in history; bot replies "<title> marked as watched!".	Call /history and confirm the movie appears with watched status according to your formatter; repeating watched_<id> should remain idempotent or give a friendly message, depending on repository logic, but must not crash.
B15	Save	Same as B12.	Tap "💾 Save".	Movie is added to watchlist (unless already present) with a confirmation message.	Call /watchlist and confirm the movie appears. Tap Save again on the same card; history_handlers.handle_save detects duplicates and responds "already in your watchlist" without adding a second row.
B16	More like this	Same as B12.	Tap "🎯 More like this".	Bot sends a new batch of similar movies, excluding previously recommended titles from last_recs and history where possible.	Confirm new cards have different titles from the previous batch and align with the seed movie’s style; test using a seed from last_recs_json and verify that the seed title is correctly inferred in the status message even if only movie_id is in the callback.
B17	Question engine more suggestions	Completed question engine recs.	Tap "More suggestions" under the trailing prompt.	Bot either drains overflow buffer using get_more_suggestions or re‑runs trending discovery; additional cards are appended and last_recs is updated.	Confirm the new cards are logged and that overflow is eventually emptied; subsequent taps should not produce infinite duplicates. When overflow is empty, verify that trending fallback still returns valid cards.
6.4 Rating preference
ID	Preconditions	User input	Expected output	Validation
B11	Valid rating	/rating 7 (or /min_rating 7.5)	Bot parses the numeric value, validates range 0–10, updates user.avg_rating_preference, and confirms the new min rating.	After setting /rating 7.5, trigger /movie or /trending and inspect the star ratings; no movie should have rating below 7.5 unless OMDb data is missing (rating None). Verify validation errors on non‑numeric inputs and out‑of‑range values: /rating excellent and /rating 11 should both yield friendly error messages without updating the profile.
7. History, Watchlist, Star, and Share
7.1 History and watchlist browsing
Using /history and /watchlist with pagination tests both repository behaviour and inline keyboard editing.

ID	Preconditions	User input	Steps	Expected output	Validation
B7	History listing	Have at least 12 watched/recommended movies (via previous tests).	/history	1) Call /history. 2) Use Prev/Next buttons to navigate pages.	Bot sends a formatted text list (10 per page by default) with page counters and Prev/Next inline buttons; tapping buttons edits the existing message in place rather than sending new messages.
B8	Watchlist listing	Save at least 12 movies via Save buttons.	/watchlist	Same as B7.	Same behaviour but using format_watchlist_list and watchlist_pN callbacks.
7.2 Star filmography
ID	Preconditions	User input	Expected output	Validation
B9	Valid star	/star Christopher Nolan	Bot responds with a status message then a set of filmography cards, saved into history and last_recs.	Confirm that the titles correspond to Nolan’s filmography; history entries are created for each movie and last_recs_json contains the same serialised list. If OMDb is unavailable, cards still show titles and reasons from the LLM but may lack ratings/streaming details. [/cite:22]
N1	Missing star name	/star	Bot sends a usage hint with examples; no crash.	Validate that calling /star without arguments does not raise exceptions and that no history or last_recs update occurs.
N2	Unknown star	/star Xyzabc1234	Bot replies that it could not find filmography info and suggests trying another name.	Confirm get_star_movies returns an empty list and handler emits the fallback message; there should be no partially populated cards.
7.3 Share card
ID	Preconditions	User input	Expected output	Validation
B10	With last recs	After any rec flow (e.g., /trending), call /share.	Bot builds a single HTML text card showing up to 5 movies, each with title/year, rating, genres, reason, and streaming label (when available), followed by a footer "Powered by CineMate" and instructions to forward. A follow‑up message explains how to forward the card.	Verify that the card excludes movies with streaming or streaming_platforms set to "N/A" or empty; _streaming_label should skip these gracefully. Confirm the number of movies never exceeds _MAX_SHARE_ITEMS (5) and that special characters are properly HTML‑escaped.
N3	No recs yet	/share in a fresh session	Bot replies with "Nothing to share yet" and suggests commands to get recommendations.	Confirm session.last_recs_json is empty and that the handler does not crash or send malformed HTML when there is nothing to share.
8. Admin and Infrastructure Scenarios
Admin commands are restricted by admin_only, which checks AdminRepository.is_admin(chat_id) and silently no‑ops for non‑admins – there is no error message, which is intentional.

8.1 Admin commands
All admin tests must be run from a chat id that appears in ADMIN_CHAT_IDS and is present in the admin repo’s admin list (or seeded data).

ID	Command	Preconditions	Expected output	Validation
C1	/admin_health	Admin chat.	A "System Health" card listing providers such as Perplexity, OMDb, Watchmode, Redis, Supabase, with statuses like ok, not_configured, or similar.	Verify that toggling env variables (e.g., unsetting OMDB_API_KEY) changes the status to not configured. Confirm no crash when Supabase or Redis are absent; HealthService uses AdminRepository and tolerates missing configs.
C2	/admin_stats	Some user activity recorded.	Bot prints metrics like interaction counts and user counts.	Ensure get_stats result is formatted with _safe_format_value, so numeric and string stats both display without formatting errors. Empty stats should produce "No stats recorded yet".
C3	/admin_clear_cache	Redis configured (optional).	Bot returns a report of cleared keys (e.g., semantic cache, rate‑limit keys, app_config cache).	Confirm that rate‑limit and semantic cache behaviour is consistent before/after this command (e.g., semantic classification is re‑queried after clearing).[/cite:29]
C4	/admin_errors or /admin_errors 5	Some error logs in DB.	Bot prints up to N recent errors with timestamp, type, message, workflow step, and chat id; truncates long messages to stay under Telegram limits.	Introduce a deliberate error (e.g., temporary bad API key) and verify it appears. Confirm that limit parsing works and that an empty error set yields "No recent errors".
C5	/admin_usage 24	Provider usage data in app_config.	Bot prints per‑provider call counts, tokens, and estimated cost plus top users.	Confirm that numbers format correctly and that total cost is calculated and displayed; when no usage data exists, output should still be valid Markdown without crashes.
C6–C8	/admin_broadcast <msg> followed by confirm/cancel	Admin chat; some users in DB.	Bot shows a preview with inline buttons (Confirm & Send / Cancel). On Confirm, it sends the message to all user chat ids with rate‑limited batches; on Cancel, it reports broadcast cancelled or no pending broadcast.	Confirm that pending message is stored in Redis or the in‑memory _PENDING_STORE, and that admin_repo.get_all_user_chat_ids drives the broadcast list. Check that a second /admin_broadcast_confirm after completion reports "no pending broadcast". Validate that failures per user are counted but do not abort the loop.
C9–C10	/admin_disable_provider omdb and /admin_enable_provider omdb	HealthService active; OMDb key configured.	Disable sets provider to a disabled state, causing is_healthy to block calls; enable reopens the circuit.	After disabling OMDb, run /movie or /star and verify behaviour: OMDb is skipped (no new metadata upserts), but recs still appear via LLM stubs and/or movie_metadata fallback. Re‑enable and confirm OMDb enrichments resume. Cross‑check HealthService.get_provider_status via admin_service if exposed (or via logs).
8.2 Negative and robustness cases
The docs matrix defines several negative cases; each should be exercised via run_local.py:

ID	Scenario	Input	Expected behaviour	Validation
N4	Malformed callback	Manually craft a callback with data "like_" or "watched_" (no id).	Handlers detect missing movie id and respond by answering the callback with a short warning (if callback id exists) and doing nothing else.	Verify that no exceptions bubble up and no invalid DB writes are attempted; logs may show a warning but user experience is a small toast or nothing.
N5	Empty / non‑text message	Send sticker, photo, or blank text.	normalize_input sets input_text to empty; detect_intent falls back to fallback; handle_fallback sends a polite help message for unsupported content.	Confirm no crashes; dedup/rate‑limit logic still runs but the handler returns quickly.
N6	Rate limiting	Send /trending 15 times rapidly from same chat (script via curl or spam in app).	Up to a threshold, requests enqueue normally; beyond threshold is_rate_limited returns true and send_message_safely sends a friendly rate‑limit notice while returning HTTP 200 from webhook.	Confirm that you see the rate‑limit message after enough rapid calls and that additional messages within the window are ignored or rate‑limited, as per redis_cache.is_rate_limited configuration; see the example bash loop in TEST_MATRIX.md.
N7	Duplicate update ids	Resend the same Telegram update using a replay tool.	_redis_cache.mark_processed_update prevents re‑processing; webhook returns {"ok": true} without side effects.	Confirm that message side effects (e.g., history insert) occur only once even if the same update JSON is POSTed multiple times.
N8	Very large input	Send a text message approaching Telegram length limits (~2000 characters).	RequestSizeLimitMiddleware ensures request size stays under CINEMATE_MAX_REQUEST_BYTES; long but valid messages go through normalization and either semantic routing or question engine as usual.	Confirm no FastAPI exceptions for large but valid payloads and that the middleware logs oversize rejections if you artificially exceed the configured max by direct HTTP POST.
N9	/rating with non‑number	/rating excellent	Handler sends a validation error explaining that rating must be numeric between 0 and 10.	Confirm user.avg_rating_preference remains unchanged and subsequent recs are unaffected.
N10	Admin command from non‑admin	/admin_health from a non‑admin chat.	admin_only decorator detects non‑admin via AdminRepository.is_admin and returns without any user‑visible output.	Confirm that there is no crash and no message; logs may show debug entries about blocked admin attempts.
8.3 Infrastructure failure simulations
For full fidelity on infra tests, run with Supabase and Redis configured and RQ worker enabled; then simulate failures:

ID	Scenario	Simulation	Expected behaviour	Validation
I1	Redis unavailable	Stop Redis or point REDIS_URL to an invalid host, then trigger /trending.	queue_service.enqueue_job detects missing Redis and falls back to inline async execution; redis_cache also falls back to in‑memory storage for rate limiting and dedup where implemented.	Confirm that requests still succeed (cards delivered) but logs show warnings about Redis/RQ unavailability and inline scheduling; no unhandled exceptions should occur.
I2	Supabase unavailable	Remove/blank SUPABASE_URL or simulate network failure in Supabase client, then call /history.	Movie/history/watchlist repositories fall back to in‑memory implementations (depending on repo design); /history still returns something but may be empty or ephemeral.	Confirm that handlers do not crash and that admin health reports Supabase as not configured or failing while other features keep working.
I3	OMDb provider down	Use /admin_disable_provider omdb then /search Inception.	Discovery still returns titles from the LLM, but OMDb enrichment is skipped; cards may lack accurate ratings/plots but show reasons and titles.	Confirm that HealthService.is_healthy("omdb") blocks OMDb calls and that movie_metadata upserts stop during this period; re‑enable and verify enrichments resume.
I4	Perplexity down	Disable Perplexity via admin or by breaking the API key, then send a long free‑text query.	Semantic routing and discovery fall back to movie_metadata repository; for some flows this may mean only trending or cached movies are returned or that fallback is fallback handler.	Confirm that SemanticService returns unknown when health indicates Perplexity is unhealthy and that discovery logs errors but still returns DB‑backed stubs when available.
I5	Watchmode down	Disable Watchmode via admin or unset WATCHMODE_API_KEY, then run /trending.	Cards appear without streaming labels; recommendations themselves are unaffected.	Confirm format_streaming_summary returns an empty string and share cards do not show misleading streaming info.
I6	Worker crash in RQ mode	With RQ worker running, start a heavy recommendation (e.g., /movie with complex seed), then kill the worker process mid‑job.	The current job is lost (known limitation) but the user does not see a stack trace; future requests are handled by new worker sessions.	Confirm no partial data is written for the killed job and that subsequent /trending etc. continue to work after restarting the worker. See TEST_MATRIX.md known limitation about retries.
I7	Circuit half‑open	Force repeated failures (e.g., bad OMDb key) until HealthService opens the circuit, then wait at least RECOVERY_WINDOW seconds and trigger another call.	After the recovery window, is_healthy returns true once to allow a probe; on success, circuit transitions to CLOSED and feature flag is re‑enabled, otherwise the circuit remains open.	Confirm provider status transitions OPEN → HALF‑OPEN → CLOSED via logs or admin inspection; daily budget guards continue to apply regardless of circuit state.
9. Running Automated Tests
The tests/ directory contains extensive unit and integration tests that mirror many of the manual scenarios above, including question engine flows, error logging, webhooks, and regression tests for specific features (7–9).

To run the full suite locally:

bash
pytest
Key files:

tests/test_webhook.py – webhook and intent routing tests.

tests/test_e2e_regression.py – end‑to‑end flows across many intents.

tests/test_recommendation_service.py, tests/test_recommendation_modes.py – rec engine behaviour and filtering tests.

tests/test_enrichment_*, tests/test_health_service.py, tests/test_queue_service.py – provider and infra logic tests.

Use automated tests as a safety net while executing this manual guide: when a manual scenario fails, look for a corresponding test to understand expected behaviour and assertions.

10. Proposed README.md Content
The current README.md is essentially empty; the following content can replace it to reflect the actual implementation and testing approach.

text
# CineMate – Telegram Movie Companion

CineMate is a Telegram bot that helps you discover movies you will actually enjoy.
It combines a conversational question engine, LLM‑powered discovery, and
provider metadata (OMDb, Watchmode) to deliver rich recommendation cards
right inside Telegram.

## Features

- **Onboarding question engine** – short, friendly questions to capture your
  mood, genres, language, era, and more before recommending films.
- **Similarity search** – `/movie <title>` and `/search <title>` return
  curated movies similar to a seed film.
- **Trending & surprise** – `/trending` surfaces recent popular titles;
  `/surprise` picks hidden gems, taking your dislikes into account.
- **History & watchlist** – `/history` and `/watchlist` provide paginated
  views of what you have seen and saved.
- **Feedback‑aware recommendations** – Like/Dislike and min‑rating
  preferences feed back into the recommendation engine.
- **Star filmography & share cards** – `/star <name>` returns a curated
  filmography; `/share` builds a forwardable recommendation card.
- **Admin dashboard** – `/admin_health`, `/admin_stats`, `/admin_errors`,
  `/admin_usage`, `/admin_broadcast`, and provider toggles for operations.

## Architecture

- **FastAPI** app in `main.py` exposes a Telegram webhook and a `/health`
  endpoint.
- **Queue & workers** – jobs are enqueued via `services.queue_service` to a
  Redis/RQ queue (or executed inline for local development).
- **Worker** – `services.worker_service.run_intent_job` routes intents to
  handlers under `handlers/`.
- **Services** – `services/` contains discovery, recommendation, enrichment,
  session, user, admin, health, semantic, and logging services.
- **Repositories** – `repositories/` provide Supabase‑backed storage with
  in‑memory fallbacks for local/dev usage.
- **Clients** – `clients/` wrap Telegram, Perplexity, OMDb, and Watchmode.

## Getting Started (Local)

1. Clone the repo and install dependencies:
   ```bash
   git clone https://github.com/nikhil95050/CineMate.git
   cd CineMate
   python -m venv .venv
   source .venv/bin/activate  # Windows: .venv\\Scripts\\activate
   pip install -r requirements.txt
   ```
2. Copy `.env.example` to `.env` and fill in at least:
   - `TELEGRAM_BOT_TOKEN`
   - `PERPLEXITY_API_KEY`
   - `OMDB_API_KEY`
   - `WATCHMODE_API_KEY`
   - `SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY` (for persistence)
   - `REDIS_URL` (for queue and rate‑limiting)
3. For local testing without Redis/RQ, set `CINEMATE_INLINE_JOBS=1`.

### Run via long‑polling (dev)

```bash
python run_local.py
```

This script calls Telegram's `getUpdates` API directly and dispatches
messages to the same worker logic the webhook uses.

### Run with webhook + worker (prod‑like)

```bash
uvicorn main:app --host 0.0.0.0 --port 8000
python rq_worker.py
```

Register the webhook URL with Telegram as:

```text
https://<your-domain>/webhook/<TELEGRAM_BOT_TOKEN>
```

## Testing

- Automated tests: `pytest`.
- Live manual tests: see `docs/TEST_MATRIX.md` and the separate
  "CineMate Live Testing Guide" document for step‑by‑step Telegram
  scenarios.

## Environment & Health

The `/health` endpoint reports configuration readiness (Telegram token,
Perplexity key, Supabase, Redis). Admin commands expose provider health,
usage, recent errors, and cache controls.

## Roadmap / Known Limitations

See `docs/TEST_MATRIX.md` for known limitations and suggested refactors,
including RQ job retries, webhook signature verification, and telemetry.
This README skeleton can be adjusted to your preferred style but captures the actual capabilities, architecture, and entrypoints implemented in the codebase.
