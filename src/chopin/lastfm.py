"""Fetch full scrobble history from Last.fm via pylast."""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

import pylast


_RETRY_DELAYS = (2, 4, 8)


def _fetch_with_retry(api_key: str, username: str) -> list[Any]:
    network = pylast.LastFMNetwork(api_key=api_key)
    user = network.get_user(username)
    last_err: Exception | None = None
    for delay in (0, *_RETRY_DELAYS):
        if delay:
            time.sleep(delay)
        try:
            return list(user.get_recent_tracks(limit=None))
        except (pylast.NetworkError, pylast.WSError, pylast.MalformedResponseError) as e:
            last_err = e
    raise RuntimeError(f"last.fm fetch failed after retries: {last_err}")


def fetch_all_scrobbles(api_key: str, username: str) -> dict:
    played = _fetch_with_retry(api_key, username)
    seen: set[tuple[str, str]] = set()
    tracks: list[dict] = []
    for entry in played:
        track = entry.track
        artist = str(track.artist).strip()
        title = str(track.title).strip()
        if not artist or not title:
            continue
        key = (artist.lower(), title.lower())
        if key in seen:
            continue
        seen.add(key)
        album = entry.album.strip() if getattr(entry, "album", None) else None
        tracks.append({"artist": artist, "title": title, "album": album or None})
    tracks.sort(key=lambda t: (t["artist"].lower(), t["title"].lower()))
    return {
        "user": username,
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "count": len(tracks),
        "tracks": tracks,
    }
