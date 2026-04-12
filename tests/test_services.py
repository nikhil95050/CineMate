from typing import Any, Dict, List

from models import MovieModel, UserModel, SessionModel
from services.movie_service import MovieService, UserService, SessionService


class FakeHistoryRepo:
    def __init__(self) -> None:
        self.logged: List[Dict[str, Any]] = []

    def log_recommendations(self, chat_id: str, rows: List[Dict[str, Any]]) -> None:
        self.logged.append({"chat_id": chat_id, "rows": rows})


class FakeWatchlistRepo:
    def __init__(self) -> None:
        self.saved: List[Dict[str, Any]] = []

    def add_to_watchlist(self, chat_id: str, row: Dict[str, Any]) -> None:
        self.saved.append({"chat_id": chat_id, "row": row})


class FakeUserRepo:
    def __init__(self) -> None:
        self.users: Dict[str, Dict[str, Any]] = {}

    def get_user(self, chat_id: str) -> Dict[str, Any]:
        return self.users.get(chat_id, {"chat_id": chat_id})

    def upsert_user(self, chat_id: str, username: str | None = None, patch: Dict[str, Any] | None = None) -> None:
        current = self.users.get(chat_id, {"chat_id": chat_id})
        if username:
            current["username"] = username
        if patch:
            current.update(patch)
        self.users[chat_id] = current


class FakeSessionRepo:
    def __init__(self) -> None:
        self.sessions: Dict[str, Dict[str, Any]] = {}

    def get_session(self, chat_id: str) -> Dict[str, Any]:
        return self.sessions.get(chat_id, {"chat_id": chat_id})

    def upsert_session(self, chat_id: str, patch: Dict[str, Any]) -> None:
        current = self.sessions.get(chat_id, {"chat_id": chat_id})
        current.update(patch)
        self.sessions[chat_id] = current


def test_movie_service_add_to_history_and_watchlist():
    history_repo = FakeHistoryRepo()
    watchlist_repo = FakeWatchlistRepo()
    svc = MovieService(history_repo=history_repo, watchlist_repo=watchlist_repo)

    movie = MovieModel(movie_id="tt1375666", title="Inception")

    svc.add_to_history("1", [movie])
    assert history_repo.logged
    assert history_repo.logged[0]["chat_id"] == "1"

    svc.add_to_watchlist("1", movie)
    assert watchlist_repo.saved
    assert watchlist_repo.saved[0]["chat_id"] == "1"


def test_user_service_roundtrip():
    repo = FakeUserRepo()
    svc = UserService(user_repo=repo)

    user = UserModel(chat_id="42", username="nikhil")
    svc.upsert_user(user)

    fetched = svc.get_user("42")
    assert fetched.username == "nikhil"


def test_session_service_roundtrip():
    repo = FakeSessionRepo()
    svc = SessionService(session_repo=repo)

    session = SessionModel(chat_id="99", session_state="questioning", question_index=2)
    svc.upsert_session(session)

    fetched = svc.get_session("99")
    assert fetched.session_state == "questioning"
    assert fetched.question_index == 2
