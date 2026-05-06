"""Track normalization and set-difference logic."""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Track:
    artist: str
    title: str
    album: str | None = None
    playcount: int | None = None
    listeners: int | None = None


_PARENS = re.compile(r"\s*[\(\[].*?[\)\]]\s*")
_SUFFIX = re.compile(
    r"\s+-\s+(remaster|remastered|live|mono|stereo|edit|version)\b.*$",
    re.IGNORECASE,
)
_WS = re.compile(r"\s+")


def _clean(s: str) -> str:
    s = s.lower().strip()
    s = _PARENS.sub(" ", s)
    s = _SUFFIX.sub("", s)
    s = _WS.sub(" ", s).strip()
    return s


def normalize_key(artist: str, title: str) -> str:
    return f"{_clean(artist)}||{_clean(title)}"


def diff_tracks(lastfm: list[Track], spotify: list[Track]) -> list[Track]:
    spotify_keys = {normalize_key(t.artist, t.title) for t in spotify}
    seen: set[str] = set()
    missing: list[Track] = []
    for t in lastfm:
        key = normalize_key(t.artist, t.title)
        if key in spotify_keys or key in seen:
            continue
        seen.add(key)
        missing.append(t)
    missing.sort(key=lambda t: (t.artist.lower(), t.title.lower()))
    return missing
