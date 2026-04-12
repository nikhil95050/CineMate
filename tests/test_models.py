import pytest

from models import MovieModel, UserModel, SessionModel


def test_movie_model_from_and_to_history_row():
    row = {
        "movie_id": "tt1375666",
        "title": "Inception",
        "year": "2010",
        "genres": "Action, Sci-Fi",
        "language": "English",
        "rating": "8.8",
    }

    movie = MovieModel.from_history_row(row)
    assert movie.movie_id == "tt1375666"
    assert movie.title == "Inception"
    assert movie.year == "2010"
    assert movie.rating == pytest.approx(8.8)
    assert "Action" in movie.genre_list

    out = movie.to_history_row(chat_id="123")
    assert out["chat_id"] == "123"
    assert out["title"] == "Inception"


def test_user_model_roundtrip():
    row = {
        "chat_id": "42",
        "username": "nikhil",
        "preferred_genres": ["Sci-Fi", "Drama"],
        "disliked_genres": ["Horror"],
        "preferred_language": "English",
        "preferred_era": "Modern",
        "watch_context": "Alone",
        "avg_rating_preference": 7.5,
        "subscriptions": ["Netflix", "Prime Video"],
        "user_taste_vector": {"top_actors": ["DiCaprio"]},
    }

    user = UserModel.from_row(row)
    assert user.chat_id == "42"
    assert "Sci-Fi" in user.preferred_genres

    out = user.to_row()
    assert out["chat_id"] == "42"
    assert "Sci-Fi" in out["preferred_genres"]


def test_session_model_roundtrip():
    row = {
        "chat_id": "99",
        "session_state": "questioning",
        "question_index": 3,
        "answers_mood": "Happy",
        "answers_genre": "Action, Sci-Fi",
        "last_recs_json": "[]",
        "overflow_buffer_json": "[]",
        "sim_depth": 1,
    }

    session = SessionModel.from_row(row)
    assert session.chat_id == "99"
    assert session.session_state == "questioning"
    assert session.question_index == 3

    out = session.to_row()
    assert out["chat_id"] == "99"
    assert out["question_index"] == 3
    assert "last_recs_json" in out
