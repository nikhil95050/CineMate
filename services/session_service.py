"""SessionService — session CRUD.

Extracted from movie_service.py so imports in container.py stay clean
and circular dependency risks are eliminated.
"""
from __future__ import annotations

from typing import Any

from models.domain import SessionModel


class SessionService:
    def __init__(self, session_repo: Any | None = None) -> None:
        self.session_repo = session_repo

    def get_session(self, chat_id: str) -> SessionModel:
        if not self.session_repo:
            return SessionModel(chat_id=str(chat_id))
        row = self.session_repo.get_session(chat_id)
        return SessionModel.from_row(row)

    def upsert_session(self, session: SessionModel) -> None:
        if not self.session_repo:
            return
        self.session_repo.upsert_session(session.chat_id, session.to_row())

    def reset_session(self, chat_id: str) -> SessionModel:
        session = SessionModel(chat_id=str(chat_id))
        self.upsert_session(session)
        return session
