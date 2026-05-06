"""Fetch full scrobble history from Last.fm via the public REST API."""

from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Callable

_API_URL = "https://ws.audioscrobbler.com/2.0/"
_PAGE_LIMIT = 200
_RETRY_DELAYS = (2, 4, 8)
_TIMEOUT = 30
_PER_REQUEST_DELAY = 0.2  # ~5 req/s, Last.fm's documented limit

ProgressCb = Callable[[int, int], None]
LogCb = Callable[[str], None]


def _request_page(api_key: str, username: str, page: int) -> dict:
    params = urllib.parse.urlencode(
        {
            "method": "user.getrecenttracks",
            "user": username,
            "api_key": api_key,
            "format": "json",
            "limit": _PAGE_LIMIT,
            "page": page,
        }
    )
    url = f"{_API_URL}?{params}"
    last_err: Exception | None = None
    for delay in (0, *_RETRY_DELAYS):
        if delay:
            time.sleep(delay)
        try:
            with urllib.request.urlopen(url, timeout=_TIMEOUT) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            last_err = e
            continue
        if "error" in payload:
            raise RuntimeError(
                f"last.fm api error {payload.get('error')}: {payload.get('message')}"
            )
        return payload
    raise RuntimeError(f"last.fm fetch failed after retries: {last_err}")


def _normalize_items(raw) -> list[dict]:
    if raw is None:
        return []
    if isinstance(raw, dict):
        return [raw]
    return list(raw)


def fetch_all_scrobbles(
    api_key: str,
    username: str,
    progress: ProgressCb | None = None,
    log: LogCb | None = None,
) -> dict:
    if log:
        log("requesting page 1 (discovering total)")
    first = _request_page(api_key, username, 1)
    rt = first.get("recenttracks", {})
    attr = rt.get("@attr", {})
    total_pages = max(int(attr.get("totalPages", 1)), 1)
    total_scrobbles = int(attr.get("total", 0) or 0)
    if log:
        log(f"total: {total_scrobbles} scrobbles across {total_pages} pages")

    by_key: dict[tuple[str, str], dict] = {}
    order: list[tuple[str, str]] = []

    def consume(page_data: dict) -> None:
        items = _normalize_items(page_data.get("recenttracks", {}).get("track"))
        for item in items:
            if (item.get("@attr") or {}).get("nowplaying") == "true":
                continue
            artist = ((item.get("artist") or {}).get("#text") or "").strip()
            title = (item.get("name") or "").strip()
            if not artist or not title:
                continue
            key = (artist.lower(), title.lower())
            existing = by_key.get(key)
            if existing is None:
                album = ((item.get("album") or {}).get("#text") or "").strip()
                by_key[key] = {
                    "artist": artist,
                    "title": title,
                    "album": album or None,
                    "playcount": 1,
                }
                order.append(key)
            else:
                existing["playcount"] += 1

    consume(first)
    if progress:
        progress(1, total_pages)
    for page in range(2, total_pages + 1):
        if log:
            log(f"requesting page {page}/{total_pages}")
        data = _request_page(api_key, username, page)
        consume(data)
        if progress:
            progress(page, total_pages)

    tracks = [by_key[k] for k in order]
    tracks.sort(key=lambda t: (t["artist"].lower(), t["title"].lower()))
    return _build_payload(username, tracks)


def _build_payload(username: str, tracks: list[dict]) -> dict:
    return {
        "user": username,
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "count": len(tracks),
        "tracks": tracks,
    }


# Sentinel returned when a track is not known to last.fm at all (no info).
TRACK_UNKNOWN: int = -1


def get_track_listeners(api_key: str, artist: str, title: str) -> int:
    """Return how many distinct last.fm users have scrobbled this track.

    Uses track.getInfo. Returns TRACK_UNKNOWN (-1) when last.fm has no entry
    for the (artist, title) pair (404-style errors). Sleeps briefly per call
    to stay under the documented ~5 req/s rate.
    """
    params = urllib.parse.urlencode(
        {
            "method": "track.getInfo",
            "artist": artist,
            "track": title,
            "api_key": api_key,
            "format": "json",
            "autocorrect": "0",
        }
    )
    url = f"{_API_URL}?{params}"
    last_err: Exception | None = None
    for delay in (0, *_RETRY_DELAYS):
        if delay:
            time.sleep(delay)
        try:
            with urllib.request.urlopen(url, timeout=_TIMEOUT) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            last_err = e
            continue
        time.sleep(_PER_REQUEST_DELAY)
        if "error" in payload:
            # 6 = "Track not found" — treat as unknown, not retryable
            if int(payload.get("error", 0)) == 6:
                return TRACK_UNKNOWN
            raise RuntimeError(
                f"last.fm api error {payload.get('error')}: {payload.get('message')}"
            )
        track = payload.get("track") or {}
        listeners = track.get("listeners")
        if listeners is None:
            return TRACK_UNKNOWN
        try:
            return int(listeners)
        except (TypeError, ValueError):
            return TRACK_UNKNOWN
    raise RuntimeError(f"last.fm track.getInfo failed after retries: {last_err}")
