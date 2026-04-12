"""Service-layer singleton container.

All service and repository instances are created once here so the rest of
the codebase can do ``from services.container import rec_service`` without
worrying about construction order or circular imports.
"""
from __future__ import annotations

from repositories import (
    FeedbackRepository,
    HistoryRepository,
    MovieMetadataRepository,
    SessionRepository,
    UserRepository,
    WatchlistRepository,
    AdminRepository,
)
from services.discovery_service import DiscoveryService
from services.movie_service import HistoryService, MovieService, WatchlistService
from services.recommendation_service import RecommendationService
from services.session_service import SessionService
from services.user_service import UserService
from services.admin_service import AdminService
from services.health_service import HealthService
from services.semantic_service import SemanticService

# ---------------------------------------------------------------------------
# Repositories
# ---------------------------------------------------------------------------

session_repo        = SessionRepository()
user_repo           = UserRepository()
history_repo        = HistoryRepository()
watchlist_repo      = WatchlistRepository()
movie_metadata_repo = MovieMetadataRepository()
feedback_repo       = FeedbackRepository()
admin_repo          = AdminRepository()

# ---------------------------------------------------------------------------
# Services
# ---------------------------------------------------------------------------

session_service = SessionService(session_repo=session_repo)
user_service    = UserService(
    user_repo=user_repo,
    feedback_repo=feedback_repo,
    history_repo=history_repo,
)
history_service   = HistoryService(history_repo=history_repo)
watchlist_service = WatchlistService(watchlist_repo=watchlist_repo)
movie_service     = MovieService(
    history_repo=history_repo,
    watchlist_repo=watchlist_repo,
)
discovery_service = DiscoveryService()
rec_service       = RecommendationService(discovery=discovery_service)
admin_service     = AdminService(admin_repo=admin_repo)

# Feature 10 ----------------------------------------------------------------
# health_service must be created BEFORE semantic_service because
# SemanticService.__init__ accepts an optional health_service reference.
health_service   = HealthService(admin_repo=admin_repo)
semantic_service = SemanticService(health_service=health_service)
