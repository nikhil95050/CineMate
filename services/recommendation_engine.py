"""Recommendation engine configuration for CineMate.

This module defines the QUESTION set used by the onboarding question engine
flow. The structure mirrors the original Antigravity project so that the
questionnaire logic and any future recommendation logic can share the same
contract.
"""
from __future__ import annotations

from typing import List, Optional, Sequence, Tuple

# (key, question_text, options)
QUESTIONS: List[Tuple[str, str, List[str]]] = [
    (
        "mood",
        "So, what's the vibe today? Are we looking for something to lift your spirits, or something deep and dark?",
        ["Happy", "Sad", "Excited", "Relaxed", "Vibrant", "Dark"],
    ),
    (
        "genre",
        "Every great film starts with its flavor. Which genres speak to you right now? (Pick a few, then tap Done)",
        [
            "Action",
            "Comedy",
            "Drama",
            "Sci-Fi",
            "Horror",
            "Romance",
            "Thriller",
            "Animation",
        ],
    ),
    (
        "language",
        "Do you have a specific linguistic preference, or are you open to exploring world cinema?",
        ["English", "Spanish", "Hindi", "French", "Japanese", "Korean"],
    ),
    (
        "era",
        "Are we feeling nostalgic for the classics, or do you want something fresh off the press?",
        [
            "Modern (2020s)",
            "Recent (2010s)",
            "2000s",
            "Classic (90s/80s)",
            "Vintage",
        ],
    ),
    (
        "context",
        "Who's joining you for this cinematic journey? (It helps me pick the right intensity!)",
        ["Alone", "With Family", "With Friends", "Date Night"],
    ),
    (
        "time",
        "How long of a commitment are we talking about today?",
        ["Under 90m", "Standard (2h)", "Epic (2.5h+)", "No limit"],
    ),
    (
        "avoid",
        "Is there anything that's a total 'no' for you right now? (Type it in or tap Skip)",
        [],
    ),
    (
        "favorites",
        "Tell me about some films, actors, or directors you absolutely adore. It helps me find your cinematic kindred spirits!",
        [],
    ),
    (
        "rating",
        "And finally, how high are our standards today? (IMDb rating)",
        ["Any", "6+", "7+", "8+", "9+"],
    ),
]

QUESTION_KEYS: List[str] = [q[0] for q in QUESTIONS]


def get_next_question(question_index: int) -> Optional[Tuple[str, str, List[str]]]:
    """Return the (key, text, options) tuple for the given index, or None.

    This helper keeps question index arithmetic in one place so handlers can
    safely advance through the questionnaire without off‑by‑one errors.
    """
    if 0 <= question_index < len(QUESTIONS):
        return QUESTIONS[question_index]
    return None
