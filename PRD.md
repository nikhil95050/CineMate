# CineMate v2 — Product Requirements Document

## 1. Project Overview

CineMate is a Telegram bot that recommends movies based on user preferences, mood, genre, era, and context. Users interact through a conversational questionnaire, receive curated movie cards with trailers and streaming availability, and can save, rate, and share recommendations. An admin web dashboard provides usage analytics, provider health monitoring, and user management.

**Scope of Change**: Rebuild the entire application codebase from scratch. Infrastructure (Telegram Bot API, Supabase PostgreSQL, Redis, TMDB, OMDb, Watchmode APIs) remains identical. Only the programming language, framework stack, and internal architecture change.

---

## 2. Current vs. Target Tech Stack

| Layer | Current (Python) | Target (TypeScript) | Rationale |
|-------|------------------|---------------------|-----------|
| Language | Python 3.12 | TypeScript 5.x (strict) | Type-safe across frontend + backend; ecosystem for Telegram bots |
| Web framework | FastAPI | Hono | Lighter, faster, edge-native, excellent TypeScript DX |
| Telegram bot | Custom httpx wrapper | grammY | Battle-tested bot framework with middleware, sessions, menus |
| ORM / DB | Raw Supabase REST + SQL | Prisma (PostgreSQL) | Type-safe queries, migrations, schema as source of truth |
| Validation | Pydantic | Zod | Runtime + static types from single source; smaller bundle |
| Background jobs | RQ (Redis Queue) | BullMQ | Modern, feature-rich, built-in retry/priority/isolation |
| Admin frontend | Jinja2 templates | React 19 + Tailwind CSS 4 | Component-driven UX, shared types with backend |
| HTTP client | httpx | Built-in fetch / ofetch | Standard, zero-dependency (Hono client + native fetch) |
| Logging | python-json-logger | pino / tslog | Structured JSON logging, better Node.js integration |
| Testing | pytest + pytest-asyncio | Vitest | 10x faster, native ESM, built-in mocking, watch mode |
| DI container | Manual module singletons | tsyringe / awilix | Proper DI with decorators, testability |
| Circuit breaker | Custom implementation | opossum | Mature, configurable, well-tested |

---

## 3. Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                      Telegram Users                         │
└────────────────────────┬────────────────────────────────────┘
                         │ webhook
                         ▼
┌─────────────────────────────────────────────────────────────┐
│  Hono Server (HTTP)                                         │
│  ┌─────────────┐  ┌──────────────┐  ┌───────────────────┐  │
│  │ Webhook      │  │ Admin API    │  │ Admin SPA         │  │
│  │ Router       │  │ Router       │  │ (React, served    │  │
│  │ /webhook/:tk │  │ /admin/api/* │  │  statically)      │  │
│  └──────┬───────┘  └──────┬───────┘  └───────────────────┘  │
│         │                 │                                  │
│  ┌──────┴─────────────────┴──────────────────────────────┐  │
│  │              grammY Bot Instance                       │  │
│  │  ┌──────────┐  ┌───────────┐  ┌───────────────────┐   │  │
│  │  │Commands  │  │Conversations│  │Callback Handlers  │   │  │
│  │  │/start    │  │questionnaire│  │like_, dislike_,   │   │  │
│  │  │/trending │  │flow         │  │save_, more_like_  │   │  │
│  │  └──────────┘  └───────────┘  └───────────────────┘   │  │
│  └───────────────────────────────────────────────────────┘  │
│                         │                                    │
│  ┌──────────────────────┴──────────────────────────────┐    │
│  │                  Service Layer                       │    │
│  │  ┌────────────┐ ┌───────────┐ ┌──────────────────┐  │    │
│  │  │Discovery   │ │Enrichment │ │Recommendation    │  │    │
│  │  │Service     │ │Service    │ │Service           │  │    │
│  │  └────────────┘ └───────────┘ └──────────────────┘  │    │
│  │  ┌────────────┐ ┌───────────┐ ┌──────────────────┐  │    │
│  │  │Health      │ │Admin      │ │Semantic          │  │    │
│  │  │Service     │ │Service    │ │Service            │  │    │
│  │  └────────────┘ └───────────┘ └──────────────────┘  │    │
│  └──────────────────────────────────────────────────────┘    │
│                         │                                    │
│  ┌──────────────────────┴──────────────────────────────┐    │
│  │                  Provider Layer                      │    │
│  │  ┌──────────┐ ┌────────┐ ┌──────────┐ ┌─────────┐  │    │
│  │  │TMDB      │ │OMDb    │ │Perplexity│ │Watchmode│  │    │
│  │  │Provider  │ │Provider│ │Provider  │ │Provider │  │    │
│  │  └──────────┘ └────────┘ └──────────┘ └─────────┘  │    │
│  │              (all extend BaseProvider)               │    │
│  └──────────────────────────────────────────────────────┘    │
│                         │                                    │
│  ┌──────────────────────┴──────────────────────────────┐    │
│  │                  Data Layer                          │    │
│  │         Prisma Client (PostgreSQL / Supabase)        │    │
│  │         ioredis (Redis)    BullMQ (Job Queue)        │    │
│  └──────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────┘
```

---

## 4. Feature Requirements

### 4.1 Telegram Bot (grammY)

| Feature | Priority | Description |
|---------|----------|-------------|
| `/start` onboarding | P0 | Questionnaire flow with grammY `Conversation` plugin: mood, genre, language, era, context, duration, avoid, favorites, rating |
| `/recommend` | P0 | Trigger recommendation from saved answers |
| `/trending` | P0 | TMDB trending movies, Perplexity LLM fallback |
| `/surprise` | P0 | Curated hidden gems via Perplexity LLM |
| `/movie <title>` | P0 | Similar movie recommendations |
| `/search <query>` | P0 | Search across TMDB + OMDb |
| `/star <name>` | P1 | Actor/director filmography via TMDB |
| `/share` | P1 | Shareable recommendation card |
| `/history` | P1 | Paginated history with inline pagination |
| `/watchlist` | P1 | Paginated watchlist with inline pagination |
| `/rating` | P2 | Set minimum rating preference |
| Inline callbacks | P0 | Like/dislike/save/watched/more_like buttons per card |
| Movie cards | P0 | HTML-formatted with poster photo, star rating, genres, description, trailer link, streaming info, reason |

### 4.2 Admin Dashboard (React + Tailwind)

| Feature | Priority | Description |
|---------|----------|-------------|
| Password auth | P0 | Session-based with `ADMIN_PASSWORD` env var |
| Dashboard | P0 | Total users, interactions, recs today, errors, queue stats |
| Provider Health | P0 | Per-provider circuit state, daily calls, budget, manual toggle |
| Usage Report | P1 | API calls by provider, token counts, estimated cost |
| Error Logs | P1 | Filterable, paginated error viewer |
| Feature Flags | P1 | Enable/disable providers, toggle bot_active |
| User Browser | P2 | Search users, view profile + history + feedback |
| Broadcast | P2 | Send message to all users with confirmation |
| Data Export | P2 | Export per-user data as JSON |

### 4.3 Data Pipeline

| Component | Description |
|-----------|-------------|
| Discovery pipeline | TMDB trending/similar/search → Perplexity LLM → movie_metadata cache |
| Enrichment pipeline | TMDB trailers/credits → Watchmode streaming → YouTube trailer fallback |
| Write-through cache | Every successful TMDB/OMDb response upserted to `movie_metadata` |
| Background aggregation | BullMQ repeatable job: `api_usage` → `provider_metrics`, `user_interactions` → `user_activity_daily` |

---

## 5. Data Model (Prisma Schema)

All 15 existing tables preserved. Schema becomes the single source of truth with Prisma migrations. Key improvements:

- **Enums for constraints**: `ReactionType` (like/dislike), `CircuitState` (open/closed/half-open)
- **Relations**: Explicit foreign keys between `users ↔ sessions`, `users ↔ history`, `users ↔ watchlist`
- **JSON fields**: Typed with Prisma `Json` for `preferred_genres`, `disliked_genres`, `user_taste_vector`, `data_json`
- **Indexes**: Replicated from `db/schema.sql` via Prisma `@@index` declarations

---

## 6. Provider Abstraction

All external API clients extend a `BaseProvider` abstract class:

```typescript
abstract class BaseProvider {
  abstract providerName: string;
  abstract dailyBudget: number;

  protected abstract getApiKey(): string;

  async checkHealth(): Promise<boolean>       // Circuit breaker gate
  reportSuccess(): void                        // Reset failure count
  reportFailure(): void                        // Increment failure count
  logUsage(action: string, tokens?: TokenCount): void // api_usage row
}
```

| Provider | Extends | Endpoints |
|----------|---------|-----------|
| `TmdbProvider` | `MovieMetadataProvider` | search/movie, movie/:id, trending, similar, credits, videos, person |
| `OmdbProvider` | `MovieMetadataProvider` | OMDb title lookup (fallback only) |
| `PerplexityProvider` | `LlmProvider` | chat/completions (sonar-pro → sonar fallback) |
| `WatchmodeProvider` | `StreamingProvider` | search (IMDb → title ID), title/:id/sources |

`MovieDataProvider` composes TMDB + OMDb with the same TMDB-first, OMDb-fallback chain.

---

## 7. Background Jobs (BullMQ)

| Job | Type | Frequency | Handler |
|-----|------|-----------|---------|
| `processIntent` | On-demand | Per webhook | `IntentProcessor.run()` |
| `enrichMovie` | On-demand | Per movie batch | `EnrichmentService.enrich()` |
| `aggregateMetrics` | Repeatable | Every 5 min | `MetricsAggregator.run()` |
| `cleanupStaleKeys` | Repeatable | Daily | `CleanupService.run()` |
| `keepalive` | Repeatable | Every 9 min | Pings `/health` on self URL |

BullMQ advantages over RQ: built-in retry with backoff, job progress, rate limiting per provider, Redis streams for reliability, dashboard UI for monitoring.

---

## 8. Admin Frontend Architecture

| Aspect | Specification |
|--------|---------------|
| Framework | React 19 with React Router v7 |
| Styling | Tailwind CSS 4 |
| Data fetching | TanStack Query (React Query) |
| State | URL search params for filters, React context for auth |
| Build | Vite (shared `vite.config.ts` with Hono dev server proxy) |
| Types | Shared TypeScript types from `shared/types.ts` used by both frontend and backend |

The React SPA is served as a static build from the Hono server. During development, Vite dev server proxies API calls to Hono on port 3001.

---

## 9. Project Structure

```
cinemate/
├── package.json
├── tsconfig.json
├── vitest.config.ts
├── prisma/
│   ├── schema.prisma          # Single source of truth
│   └── migrations/
├── src/
│   ├── index.ts               # Entry point: Hono server + grammY webhook
│   ├── app.ts                 # Hono app factory (testable)
│   ├── bot/
│   │   ├── index.ts           # grammY bot instance
│   │   ├── commands/          # /start, /recommend, /trending, /surprise, etc.
│   │   ├── conversations/     # grammY Conversation plugin flows
│   │   ├── callbacks/         # Inline button handlers
│   │   └── middleware/        # Auth, rate limit, dedup, logging
│   ├── providers/
│   │   ├── base.ts            # BaseProvider abstract class
│   │   ├── tmdb.ts            # TmdbProvider
│   │   ├── omdb.ts            # OmdbProvider
│   │   ├── perplexity.ts      # PerplexityProvider
│   │   ├── watchmode.ts       # WatchmodeProvider
│   │   └── movie-data.ts      # MovieDataProvider (TMDB + OMDb chain)
│   ├── services/
│   │   ├── discovery.ts       # Intent → movies pipeline
│   │   ├── enrichment.ts      # Trailers, streaming, credits
│   │   ├── recommendation.ts  # Dedup, filter, persist, buffer
│   │   ├── health.ts          # Circuit breaker + daily budget
│   │   ├── metrics.ts         # Background aggregation
│   │   ├── semantic.ts        # LLM intent classification
│   │   └── admin.ts           # Admin business logic
│   ├── repos/
│   │   ├── user.ts            # Prisma user queries
│   │   ├── session.ts         # Prisma session queries
│   │   ├── history.ts         # Prisma history queries
│   │   ├── watchlist.ts       # Prisma watchlist queries
│   │   ├── feedback.ts        # Prisma feedback queries
│   │   └── admin.ts           # Complex admin queries
│   ├── admin/
│   │   ├── router.ts          # Hono router for /admin/api/*
│   │   ├── auth.ts            # Session middleware
│   │   └── views.ts           # Static SPA serving
│   ├── webhook/
│   │   ├── router.ts          # /webhook/:token endpoint
│   │   └── middleware.ts      # Parse + validate + dedup + normalize
│   ├── jobs/                  # BullMQ job processors
│   │   ├── intent.ts
│   │   ├── metrics.ts
│   │   └── cleanup.ts
│   ├── shared/
│   │   └── types.ts           # Shared TypeScript types + Zod schemas
│   └── lib/
│       ├── prisma.ts          # Prisma client singleton
│       ├── redis.ts           # ioredis singleton
│       ├── queue.ts           # BullMQ connection + queue registry
│       ├── circuit.ts         # Opossum circuit breaker factory
│       └── logger.ts          # pino logger setup
├── admin-ui/                  # React SPA
│   ├── package.json
│   ├── vite.config.ts
│   ├── index.html
│   └── src/
│       ├── main.tsx
│       ├── App.tsx
│       ├── pages/
│       │   ├── Login.tsx
│       │   ├── Dashboard.tsx
│       │   ├── Providers.tsx
│       │   ├── Users.tsx
│       │   ├── Errors.tsx
│       │   ├── Usage.tsx
│       │   ├── Flags.tsx
│       │   ├── Queue.tsx
│       │   └── Broadcast.tsx
│       ├── components/
│       └── hooks/
└── tests/
    ├── providers/
    ├── services/
    ├── bot/
    └── admin/
```

---

## 10. Key Design Decisions

1. **grammY instead of raw HTTP**: grammY provides middleware chains, conversation state machines, session backends (Redis), and menu builders — eliminating ~500 lines of custom Telegram handling code.

2. **Hono instead of FastAPI**: Hono is 10x smaller, edge-native, and shares TypeScript types with the frontend. Its middleware API is composable (like Express) with full type inference.

3. **Prisma instead of raw SQL**: Single schema file generates fully typed client. Migrations are declarative. Eliminates the entire `supabase_client.py` abstraction layer.

4. **Zod instead of Pydantic**: Single schema definition serves both runtime validation and TypeScript type inference. No code generation step needed.

5. **BullMQ instead of RQ**: Native retry with exponential backoff, job progress tracking, rate limiting per-provider, and a built-in monitoring dashboard.

6. **opossum instead of custom circuit breaker**: 1.5M weekly downloads, configurable thresholds, half-open probing, event hooks for logging, fallback function support.

7. **No DI framework initially**: Use factory functions and constructor injection. Introduce tsyringe only if complexity warrants it.

---

## 11. Migration Strategy

| Phase | Duration | Deliverable |
|-------|----------|-------------|
| 1. Schema + Prisma | Week 1 | `schema.prisma` matching all 15 tables, initial migration, seed script |
| 2. Providers | Week 1-2 | All 4 providers with base class, circuit breaker, unit tests |
| 3. Core services | Week 2-3 | Discovery, enrichment, recommendation, health services |
| 4. Bot commands | Week 3-4 | All Telegram commands, conversations, callbacks |
| 5. Admin API | Week 4 | All `/admin/api/*` endpoints with auth |
| 6. Admin UI | Week 5 | React SPA with all pages |
| 7. Jobs + metrics | Week 5 | BullMQ processors, aggregation jobs |
| 8. Integration tests | Week 6 | End-to-end test suite |
| 9. Deployment | Week 6 | Docker compose, Render config, documentation |

**Zero-downtime strategy**: Deploy v2 alongside v1 on a different webhook path (`/webhook/v2/{token}`), run both in parallel, switch Telegram webhook URL after smoke testing.

---

## 12. Non-Functional Requirements

| Requirement | Target |
|-------------|--------|
| Webhook response time | < 500ms p95 (job enqueued, not processed) |
| Test coverage | ≥ 85% line coverage |
| Cold start time | < 3 seconds |
| Admin dashboard load | < 2 seconds for stats page |
| API rate limit | 30 req/min per user, 5 req/min for `/admin/api/login` |
| Logging | Structured JSON to stdout (Render-compatible) |
| Error handling | All errors caught, logged, and surfaced gracefully — never crash the process |
