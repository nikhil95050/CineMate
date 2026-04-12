# CineMate – Domain Baseline

This repository is the new, cleanly structured implementation of the CineMate (Antigravity) movie recommendation bot backend.

This initial commit implements **Feature 1 – Domain Baseline**:

- Pydantic models for core entities (Movie, User, Session).
- Core configuration and infrastructure modules:
  - `config/app_config.py` – feature flags and startup readiness.
  - `config/redis_cache.py` – Redis client, JSON cache, deduplication, rate limiting.
  - `config/supabase_client.py` – Supabase REST client (sync + async wrappers).
  - `utils/time_utils.py` – UTC ISO timestamp helper.
  - `services/logging_service.py` – structured logging, batched Supabase logging.
- Thin service shims that will later grow into full services but currently only demonstrate how to work with the new models.
- Basic tests for models and services.

> Important: No business logic is implemented or changed here. This is a **type-safety and infra baseline** only. JSON shapes on the wire (as used by the existing Antigravity-main project) are preserved via conversion helpers on the models.

## Project structure

```text
config/
  app_config.py
  redis_cache.py
  supabase_client.py
models/
  domain.py
services/
  logging_service.py
  movie_service.py
  user_service.py
  session_service.py
utils/
  time_utils.py
tests/
  test_models.py
  test_services.py
pyproject.toml
README.md
```

## Models

`models/domain.py` defines three core Pydantic models:

- **MovieModel** – normalized view of a movie across OMDb, Watchmode, and internal history/watchlist records.
- **UserModel** – consolidated user profile including preferences and taste vector.
- **SessionModel** – questionnaire/session state with the same fields used in the original Antigravity `sessions` table.

Each model includes helpers to convert to/from the dict shapes used by the original Supabase repositories (so it stays compatible with existing JSON payloads):

- `MovieModel.from_history_row(row: dict)` / `to_history_row()`
- `MovieModel.to_watchlist_row()`
- `UserModel.from_row(row: dict)` / `to_row()`
- `SessionModel.from_row(row: dict)` / `to_row()`

## Config & infra

The following modules are ported and lightly namespaced from the original Antigravity-main project, retaining behavior:

- `config/app_config.py` – in-memory feature flags and startup readiness checks.
- `config/redis_cache.py` – Redis URL validation, connection management, JSON get/set, local in-process cache, deduplication (`mark_processed_update`), rate limiting (`is_rate_limited`), and metrics increment (`increment`).
- `config/supabase_client.py` – async + sync HTTPX clients, CRUD helpers, and availability check.
- `utils/time_utils.py` – `utc_now_iso()` helper used across the codebase.
- `services/logging_service.py` – global JSON logging setup, batched interaction and error logging to Supabase, profiling helpers, and interaction logging.

These modules are unchanged in behavior, only copied into the new structure so future features can be built on them.

## Services

The following minimal services are introduced only to demonstrate how Pydantic models will be used at the service layer:

- `MovieService` – accepts `MovieModel` instances and exposes thin methods like `add_to_history()` and `add_to_watchlist()` that delegate to a repository-like dependency.
- `UserService` – wraps a repository-like dependency and exposes `get_user()` and `upsert_user()` using `UserModel`.
- `SessionService` – provides `get_session()`, `upsert_session()`, and `reset_session()` using `SessionModel`.

These services are intentionally simple and do not implement any business logic yet; they only coordinate conversions between models and dict shapes.

## Tests

Two basic test modules are provided:

- `tests/test_models.py`
  - Validates required fields.
  - Ensures sensible defaults for optional fields.
  - Tests conversion from sample dicts (matching Supabase rows) into models and back.

- `tests/test_services.py`
  - Uses simple in-memory fake repositories to ensure MovieService, UserService, and SessionService call their dependencies with correctly converted data.

Run tests with:

```bash
pip install -e .[dev]
pytest
```

## Manual test checklist (for later stages)

When the rest of the bot is implemented (webhook, handlers, discovery, recommendation, etc.), the manual checks from the migration plan will apply:

- Run the FastAPI app and worker with valid `.env`.
- Exercise `/start`, `/history`, `/watchlist`, `/movie`.
- Confirm there are no validation-related crashes and that JSON payloads match expectations.

For now, this commit only establishes the domain and infra baseline and is safe to build on for subsequent features (job queue, inbound webhook, recommendation logic, etc.).
