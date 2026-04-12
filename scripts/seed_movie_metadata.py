"""
One-shot script to seed the movie_metadata Supabase table with dummy data.

Run from the project root:
    python -m scripts.seed_movie_metadata
"""
import asyncio
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from repositories.movie_metadata_repository import MovieMetadataRepository

DUMMY_MOVIES = [
    ("tt0111161", {
        "Title": "The Shawshank Redemption", "Year": "1994",
        "Genre": "Drama", "Language": "English",
        "Director": "Frank Darabont", "imdbRating": "9.3",
        "Plot": "Two imprisoned men bond over years, finding solace and eventual redemption.",
        "Poster": "N/A", "Runtime": "142 min", "Rated": "R"
    }),
    ("tt0068646", {
        "Title": "The Godfather", "Year": "1972",
        "Genre": "Crime, Drama", "Language": "English, Italian, Latin",
        "Director": "Francis Ford Coppola", "imdbRating": "9.2",
        "Plot": "The aging patriarch of an organized crime dynasty transfers control to his son.",
        "Poster": "N/A", "Runtime": "175 min", "Rated": "R"
    }),
    ("tt0468569", {
        "Title": "The Dark Knight", "Year": "2008",
        "Genre": "Action, Crime, Drama", "Language": "English, Mandarin",
        "Director": "Christopher Nolan", "imdbRating": "9.0",
        "Plot": "Batman raises the stakes in his war on crime with the help of allies.",
        "Poster": "N/A", "Runtime": "152 min", "Rated": "PG-13"
    }),
    ("tt0816692", {
        "Title": "Interstellar", "Year": "2014",
        "Genre": "Adventure, Drama, Sci-Fi", "Language": "English",
        "Director": "Christopher Nolan", "imdbRating": "8.7",
        "Plot": "A team of explorers travel through a wormhole in space.",
        "Poster": "N/A", "Runtime": "169 min", "Rated": "PG-13"
    }),
    ("tt0211915", {
        "Title": "Amelie", "Year": "2001",
        "Genre": "Comedy, Romance", "Language": "French",
        "Director": "Jean-Pierre Jeunet", "imdbRating": "8.3",
        "Plot": "A shy waitress decides to change the lives of those around her for the better.",
        "Poster": "N/A", "Runtime": "122 min", "Rated": "R"
    }),
    ("tt0095016", {
        "Title": "Die Hard", "Year": "1988",
        "Genre": "Action, Thriller", "Language": "English, German, Italian",
        "Director": "John McTiernan", "imdbRating": "8.2",
        "Plot": "An NYPD officer tries to save his wife and others taken hostage.",
        "Poster": "N/A", "Runtime": "132 min", "Rated": "R"
    }),
    ("tt1375666", {
        "Title": "Inception", "Year": "2010",
        "Genre": "Action, Adventure, Sci-Fi", "Language": "English, Japanese, French",
        "Director": "Christopher Nolan", "imdbRating": "8.8",
        "Plot": "A thief who steals corporate secrets through dream-sharing technology.",
        "Poster": "N/A", "Runtime": "148 min", "Rated": "PG-13"
    }),
    ("tt0120737", {
        "Title": "The Lord of the Rings: The Fellowship of the Ring", "Year": "2001",
        "Genre": "Adventure, Drama, Fantasy", "Language": "English, Sindarin",
        "Director": "Peter Jackson", "imdbRating": "8.8",
        "Plot": "A meek Hobbit and eight companions set out on a journey to destroy a powerful ring.",
        "Poster": "N/A", "Runtime": "178 min", "Rated": "PG-13"
    }),
    ("tt0245429", {
        "Title": "Spirited Away", "Year": "2001",
        "Genre": "Animation, Adventure, Family", "Language": "Japanese",
        "Director": "Hayao Miyazaki", "imdbRating": "8.6",
        "Plot": "A girl enters the spirit world after her parents are transformed into pigs.",
        "Poster": "N/A", "Runtime": "125 min", "Rated": "PG"
    }),
    ("tt0133093", {
        "Title": "The Matrix", "Year": "1999",
        "Genre": "Action, Sci-Fi", "Language": "English",
        "Director": "Lana Wachowski, Lilly Wachowski", "imdbRating": "8.7",
        "Plot": "A hacker discovers the truth about his reality and his role in the war against its controllers.",
        "Poster": "N/A", "Runtime": "136 min", "Rated": "R"
    }),
]


async def seed():
    repo = MovieMetadataRepository()
    print(f"Seeding {len(DUMMY_MOVIES)} movies into movie_metadata table...\n")
    ok, fail = 0, 0
    for movie_id, data in DUMMY_MOVIES:
        success = await repo.upsert(movie_id, data)
        status = "OK" if success else "FAIL"
        print(f"  [{status}]  {movie_id}  {data['Title']}")
        if success:
            ok += 1
        else:
            fail += 1
    print(f"\nDone. {ok} inserted/updated, {fail} failed.")


if __name__ == "__main__":
    asyncio.run(seed())
