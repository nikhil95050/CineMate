# Graph Report - C:\Users\Saira\OneDrive\Desktop\CineMate  (2026-04-28)

## Corpus Check
- 56 files · ~33,553 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 659 nodes · 1435 edges · 46 communities detected
- Extraction: 56% EXTRACTED · 44% INFERRED · 0% AMBIGUOUS · INFERRED: 636 edges (avg confidence: 0.7)
- Token cost: 0 input · 0 output

## Community Hubs (Navigation)
- [[_COMMUNITY_Community 0|Community 0]]
- [[_COMMUNITY_Community 1|Community 1]]
- [[_COMMUNITY_Community 2|Community 2]]
- [[_COMMUNITY_Community 3|Community 3]]
- [[_COMMUNITY_Community 4|Community 4]]
- [[_COMMUNITY_Community 5|Community 5]]
- [[_COMMUNITY_Community 6|Community 6]]
- [[_COMMUNITY_Community 7|Community 7]]
- [[_COMMUNITY_Community 8|Community 8]]
- [[_COMMUNITY_Community 9|Community 9]]
- [[_COMMUNITY_Community 10|Community 10]]
- [[_COMMUNITY_Community 11|Community 11]]
- [[_COMMUNITY_Community 12|Community 12]]
- [[_COMMUNITY_Community 13|Community 13]]
- [[_COMMUNITY_Community 14|Community 14]]
- [[_COMMUNITY_Community 15|Community 15]]
- [[_COMMUNITY_Community 16|Community 16]]
- [[_COMMUNITY_Community 17|Community 17]]
- [[_COMMUNITY_Community 18|Community 18]]
- [[_COMMUNITY_Community 19|Community 19]]
- [[_COMMUNITY_Community 20|Community 20]]
- [[_COMMUNITY_Community 21|Community 21]]
- [[_COMMUNITY_Community 22|Community 22]]
- [[_COMMUNITY_Community 23|Community 23]]
- [[_COMMUNITY_Community 24|Community 24]]
- [[_COMMUNITY_Community 25|Community 25]]
- [[_COMMUNITY_Community 26|Community 26]]
- [[_COMMUNITY_Community 27|Community 27]]
- [[_COMMUNITY_Community 28|Community 28]]
- [[_COMMUNITY_Community 29|Community 29]]
- [[_COMMUNITY_Community 30|Community 30]]
- [[_COMMUNITY_Community 31|Community 31]]
- [[_COMMUNITY_Community 32|Community 32]]
- [[_COMMUNITY_Community 33|Community 33]]
- [[_COMMUNITY_Community 34|Community 34]]
- [[_COMMUNITY_Community 35|Community 35]]
- [[_COMMUNITY_Community 36|Community 36]]
- [[_COMMUNITY_Community 37|Community 37]]
- [[_COMMUNITY_Community 38|Community 38]]
- [[_COMMUNITY_Community 39|Community 39]]
- [[_COMMUNITY_Community 40|Community 40]]
- [[_COMMUNITY_Community 41|Community 41]]
- [[_COMMUNITY_Community 42|Community 42]]
- [[_COMMUNITY_Community 43|Community 43]]
- [[_COMMUNITY_Community 44|Community 44]]
- [[_COMMUNITY_Community 45|Community 45]]

## God Nodes (most connected - your core abstractions)
1. `MovieModel` - 67 edges
2. `SessionModel` - 49 edges
3. `UserModel` - 44 edges
4. `is_configured()` - 40 edges
5. `send_message()` - 37 edges
6. `MovieMetadataRepository` - 29 edges
7. `select_rows()` - 27 edges
8. `MovieService` - 19 edges
9. `DiscoveryService` - 18 edges
10. `LoggingService` - 18 edges

## Surprising Connections (you probably didn't know these)
- `TelegramClient` --uses--> `Telegram send helpers.  All helpers route through TelegramClient so webhook mo`  [INFERRED]
  clients\telegram_client.py → clients\telegram_helpers.py
- `send_message()` --calls--> `handle_help()`  [INFERRED]
  C:\Users\Saira\OneDrive\Desktop\CineMate\clients\telegram_helpers.py → handlers\user_handlers.py
- `Convert raw Watchmode sources into a brief human-readable string.` --uses--> `LoggingService`  [INFERRED]
  clients\watchmode_client.py → services\logging_service.py
- `History, watchlist, save, and watched handlers.` --uses--> `MovieModel`  [INFERRED]
  handlers\history_handlers.py → C:\Users\Saira\OneDrive\Desktop\CineMate\models\domain.py
- `Question-engine handlers for onboarding and guided recommendations.` --uses--> `SessionModel`  [INFERRED]
  handlers\rec_handlers.py → C:\Users\Saira\OneDrive\Desktop\CineMate\models\domain.py

## Communities

### Community 0 - "Community 0"
Cohesion: 0.03
Nodes (76): AdminRepository, _env_admin_ids(), Upsert a metric_name / metric_value pair in bot_stats., BUG-ADM-1 FIX: select_rows() accepts `order` (PostgREST string),         not th, Aggregate api_usage by provider for the last `hours` hours., Return top users by interaction count.          H-3 FIX: capped fetch to 2000, Return chat IDs from ADMIN_CHAT_IDS environment variable., BUG #5 FIX: Seed admins table from ADMIN_CHAT_IDS env var once. (+68 more)

### Community 1 - "Community 1"
Cohesion: 0.05
Nodes (82): BaseModel, Service-layer singleton container.  All service and repository instances are c, _build_share_card(), handle_share(), _movie_to_dict(), Handlers for /star and /share commands., Build a nicely formatted text card from a list of movie dicts., Handle /share — build a forwardable recommendation card from last_recs. (+74 more)

### Community 2 - "Community 2"
Cohesion: 0.07
Nodes (50): handle_admin_clear_cache(), handle_admin_disable_provider(), handle_admin_enable_provider(), handle_admin_errors(), handle_admin_health(), handle_admin_stats(), handle_admin_usage(), Admin command handlers: health, stats, cache, errors, usage, provider flags. (+42 more)

### Community 3 - "Community 3"
Cohesion: 0.05
Nodes (43): Serialise the model to a dict suitable for Supabase REST upsert.          JSON, BatchLogger, CustomJsonFormatter, log_api_usage(), log_event(), log_interaction(), LoggingService, profile_call() (+35 more)

### Community 4 - "Community 4"
Cohesion: 0.06
Nodes (37): _cancel_pending(), handle_admin_broadcast(), handle_admin_broadcast_cancel(), handle_admin_broadcast_confirm(), _pop_pending(), Admin broadcast: pending-confirm-cancel pattern with rate limiting.  BUG #8 FI, _store_pending(), Return PAGE_SIZE rows for the given 1-indexed page, newest first. (+29 more)

### Community 5 - "Community 5"
Cohesion: 0.08
Nodes (19): clear_test_stores(), _now(), AdminRepository: admins table, app_config flags, bot_stats, error_logs, api_usag, Clear in-memory fallback stores (for test isolation)., AdminService, AdminService: health checks, cache clearing, cost estimation, provider flags., _as_bool(), is_feature_enabled() (+11 more)

### Community 6 - "Community 6"
Cohesion: 0.09
Nodes (29): from_row(), debug_start(), Full end-to-end smoke test that runs handle_start and returns a trace., build_question_keyboard(), handle_questioning(), handle_recommend(), _legacy_option_answer(), _move_next() (+21 more)

### Community 7 - "Community 7"
Cohesion: 0.1
Nodes (13): Shape compatible with WatchlistRepository._map_to_supabase., format_history_list(), format_watchlist_list(), Presentation-layer formatting helpers for history and watchlist.  These live i, Return an HTML-formatted string for a page of history rows., Return an HTML-formatted string for a page of watchlist rows., handle_history(), handle_watchlist() (+5 more)

### Community 8 - "Community 8"
Cohesion: 0.1
Nodes (15): Shape compatible with HistoryRepository._map_to_supabase.          Fields not, enrich_movies(), handle_clear_history(), _cache_key(), _coerce_row(), HistoryRepository, Supabase-backed history repository with Redis slice caching.  Table: history, ISSUE 5 FIX: delete all history rows for a user.          Removes the in-memor (+7 more)

### Community 9 - "Community 9"
Cohesion: 0.11
Nodes (17): _coerce_list(), _coerce_streaming_info(), _ensure_dict(), _ensure_list(), from_display_string(), from_history_row(), from_watchlist_row(), _parse_jsonb_dict() (+9 more)

### Community 10 - "Community 10"
Cohesion: 0.15
Nodes (12): build_movie_card_text(), build_movie_keyboard(), Shared movie card formatting for Telegram (HTML parse mode)., Send a list of movie cards, with an optional 'More suggestions' button at the en, Build the HTML caption for a single movie card., Inline keyboard attached to each movie card., Send a single movie card with its inline keyboard., _star_rating() (+4 more)

### Community 11 - "Community 11"
Cohesion: 0.21
Nodes (12): enqueue_job(), _get_queue(), Schedule the async worker function.      Behaviour depends on whether an event, Emit a single WARNING if inline mode is active in a production env., Resolve a dotted function path like 'services.worker_service.run_intent_job'., Execute the target function as an awaitable coroutine., Return an RQ Queue instance or None., Enqueue a background job.      INLINE mode (CINEMATE_INLINE_JOBS=1) or when Re (+4 more)

### Community 12 - "Community 12"
Cohesion: 0.18
Nodes (10): detect_intent(), normalize_input(), Input normalization and intent detection for CineMate., Map raw input text to a logical bot intent., Extract core fields from a Telegram update object.      Returns a dict contain, main(), normalize(), process_update() (+2 more)

### Community 13 - "Community 13"
Cohesion: 0.18
Nodes (11): _coerce_jsonb_fields(), _parse_dict(), _parse_list(), Supabase-backed user repository.  Falls back gracefully to in-memory storage w, Return a copy of *row* with all jsonb columns as native Python types.      BUG, Return a copy of *row* containing only the users schema columns., Repository for user rows keyed by chat_id.      Implements the same interface, Coerce *value* to a Python list.      Accepts: list (returned as-is), JSON str (+3 more)

### Community 14 - "Community 14"
Cohesion: 0.2
Nodes (9): _ensure_json_str(), _load_json_list(), _prepare_for_db(), Supabase-backed session repository.  Falls back gracefully to in-memory storag, Repository for session rows keyed by chat_id.      Implements the same interfa, Return *value* as a valid JSON string.      - If *value* is already a ``str``,, Deserialise a JSON text column value back to a Python list.      - If *value*, Return a copy of *row* with JSON text columns properly serialised.      Ensure (+1 more)

### Community 15 - "Community 15"
Cohesion: 0.5
Nodes (3): admin_only(), admin_only: decorator that silently ignores calls from non-admin users., Wrap an async handler so it silently no-ops for non-admins.

### Community 16 - "Community 16"
Cohesion: 1.0
Nodes (0): 

### Community 17 - "Community 17"
Cohesion: 1.0
Nodes (0): 

### Community 18 - "Community 18"
Cohesion: 1.0
Nodes (0): 

### Community 19 - "Community 19"
Cohesion: 1.0
Nodes (0): 

### Community 20 - "Community 20"
Cohesion: 1.0
Nodes (1): Build a StreamingInfo from a legacy plain-text streaming string.          Pars

### Community 21 - "Community 21"
Cohesion: 1.0
Nodes (1): True when at least one streaming/rent/buy platform is known.

### Community 22 - "Community 22"
Cohesion: 1.0
Nodes (1): Accept a raw dict, a StreamingInfo instance, or None.

### Community 23 - "Community 23"
Cohesion: 1.0
Nodes (1): User profile used throughout the bot.      This corresponds to the `users` tab

### Community 24 - "Community 24"
Cohesion: 1.0
Nodes (0): 

### Community 25 - "Community 25"
Cohesion: 1.0
Nodes (0): 

### Community 26 - "Community 26"
Cohesion: 1.0
Nodes (1): Write a row to api_usage after every external provider call.          BUG #9 F

### Community 27 - "Community 27"
Cohesion: 1.0
Nodes (1): Full end-to-end smoke test that runs handle_start and returns a trace.

### Community 28 - "Community 28"
Cohesion: 1.0
Nodes (1): Assert at import time that QUESTION_COLUMNS is consistent with SessionModel.

### Community 29 - "Community 29"
Cohesion: 1.0
Nodes (1): Return top users by interaction count.

### Community 30 - "Community 30"
Cohesion: 1.0
Nodes (1): Return a copy of *row* with all NOT NULL text columns coerced to ''.

### Community 31 - "Community 31"
Cohesion: 1.0
Nodes (1): CRUD + pagination for the history table.

### Community 32 - "Community 32"
Cohesion: 1.0
Nodes (1): Bulk-upsert recommendation rows. Conflict key: (chat_id, movie_id).

### Community 33 - "Community 33"
Cohesion: 1.0
Nodes (1): Set watched=True and watched_at=now. Returns True on success.

### Community 34 - "Community 34"
Cohesion: 1.0
Nodes (1): ISSUE 5 FIX: delete all history rows for a user.          Removes the in-memor

### Community 35 - "Community 35"
Cohesion: 1.0
Nodes (1): Return PAGE_SIZE rows for the given 1-indexed page, newest first.

### Community 36 - "Community 36"
Cohesion: 1.0
Nodes (1): Return total number of history rows for this user.

### Community 37 - "Community 37"
Cohesion: 1.0
Nodes (1): Coerce *value* to a Python list.      Accepts: list (returned as-is), JSON str

### Community 38 - "Community 38"
Cohesion: 1.0
Nodes (1): Coerce *value* to a Python dict or None.      Accepts: dict (returned as-is),

### Community 39 - "Community 39"
Cohesion: 1.0
Nodes (1): Return a copy of *row* with all jsonb columns as native Python types.      BUG

### Community 40 - "Community 40"
Cohesion: 1.0
Nodes (1): Normalise a raw Supabase users row so that numeric fields are not     accidenta

### Community 41 - "Community 41"
Cohesion: 1.0
Nodes (1): Repository for user rows keyed by chat_id.      Implements the same interface

### Community 42 - "Community 42"
Cohesion: 1.0
Nodes (1): Full end-to-end smoke test that runs handle_start and returns a trace.

### Community 43 - "Community 43"
Cohesion: 1.0
Nodes (1): Serialise the model to a dict suitable for Supabase REST upsert.          JSON

### Community 44 - "Community 44"
Cohesion: 1.0
Nodes (1): Conversation/session state.      Mirrors the `sessions` table schema and Sessi

### Community 45 - "Community 45"
Cohesion: 1.0
Nodes (1): Assert at import time that QUESTION_COLUMNS is consistent with SessionModel.

## Knowledge Gaps
- **162 isolated node(s):** `Reject requests whose Content-Length exceeds MAX_REQUEST_BODY_BYTES.      Two-`, `Ping /health every 9 minutes so Render never spins down.`, `Full end-to-end smoke test that runs handle_start and returns a trace.`, `RQ worker entrypoint for CineMate.  Run this process alongside the FastAPI web`, `Local development runner using Telegram long-polling.  Run with:  python run_l` (+157 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **Thin community `Community 16`** (1 nodes): `__init__.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 17`** (1 nodes): `conftest.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 18`** (1 nodes): `worker_runner.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 19`** (1 nodes): `__init__.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 20`** (1 nodes): `Build a StreamingInfo from a legacy plain-text streaming string.          Pars`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 21`** (1 nodes): `True when at least one streaming/rent/buy platform is known.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 22`** (1 nodes): `Accept a raw dict, a StreamingInfo instance, or None.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 23`** (1 nodes): `User profile used throughout the bot.      This corresponds to the `users` tab`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 24`** (1 nodes): `__init__.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 25`** (1 nodes): `__init__.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 26`** (1 nodes): `Write a row to api_usage after every external provider call.          BUG #9 F`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 27`** (1 nodes): `Full end-to-end smoke test that runs handle_start and returns a trace.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 28`** (1 nodes): `Assert at import time that QUESTION_COLUMNS is consistent with SessionModel.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 29`** (1 nodes): `Return top users by interaction count.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 30`** (1 nodes): `Return a copy of *row* with all NOT NULL text columns coerced to ''.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 31`** (1 nodes): `CRUD + pagination for the history table.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 32`** (1 nodes): `Bulk-upsert recommendation rows. Conflict key: (chat_id, movie_id).`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 33`** (1 nodes): `Set watched=True and watched_at=now. Returns True on success.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 34`** (1 nodes): `ISSUE 5 FIX: delete all history rows for a user.          Removes the in-memor`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 35`** (1 nodes): `Return PAGE_SIZE rows for the given 1-indexed page, newest first.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 36`** (1 nodes): `Return total number of history rows for this user.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 37`** (1 nodes): `Coerce *value* to a Python list.      Accepts: list (returned as-is), JSON str`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 38`** (1 nodes): `Coerce *value* to a Python dict or None.      Accepts: dict (returned as-is),`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 39`** (1 nodes): `Return a copy of *row* with all jsonb columns as native Python types.      BUG`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 40`** (1 nodes): `Normalise a raw Supabase users row so that numeric fields are not     accidenta`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 41`** (1 nodes): `Repository for user rows keyed by chat_id.      Implements the same interface`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 42`** (1 nodes): `Full end-to-end smoke test that runs handle_start and returns a trace.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 43`** (1 nodes): `Serialise the model to a dict suitable for Supabase REST upsert.          JSON`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 44`** (1 nodes): `Conversation/session state.      Mirrors the `sessions` table schema and Sessi`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 45`** (1 nodes): `Assert at import time that QUESTION_COLUMNS is consistent with SessionModel.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `MovieModel` connect `Community 1` to `Community 2`, `Community 7`, `Community 8`, `Community 9`, `Community 10`?**
  _High betweenness centrality (0.082) - this node is a cross-community bridge._
- **Why does `is_configured()` connect `Community 0` to `Community 8`, `Community 4`, `Community 5`, `Community 6`?**
  _High betweenness centrality (0.069) - this node is a cross-community bridge._
- **Why does `MovieMetadataRepository` connect `Community 1` to `Community 0`?**
  _High betweenness centrality (0.053) - this node is a cross-community bridge._
- **Are the 60 inferred relationships involving `MovieModel` (e.g. with `DiscoveryService` and `DiscoveryService: turns intents into LLM prompts, parses responses, fetches meta`) actually correct?**
  _`MovieModel` has 60 INFERRED edges - model-reasoned connections that need verification._
- **Are the 45 inferred relationships involving `SessionModel` (e.g. with `DiscoveryService` and `DiscoveryService: turns intents into LLM prompts, parses responses, fetches meta`) actually correct?**
  _`SessionModel` has 45 INFERRED edges - model-reasoned connections that need verification._
- **Are the 40 inferred relationships involving `UserModel` (e.g. with `DiscoveryService` and `DiscoveryService: turns intents into LLM prompts, parses responses, fetches meta`) actually correct?**
  _`UserModel` has 40 INFERRED edges - model-reasoned connections that need verification._
- **Are the 36 inferred relationships involving `is_configured()` (e.g. with `lifespan()` and `debug_start()`) actually correct?**
  _`is_configured()` has 36 INFERRED edges - model-reasoned connections that need verification._