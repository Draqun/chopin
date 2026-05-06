"""Spotify client helpers: playlist fetch, search, add."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Callable

import spotipy
from spotipy.oauth2 import SpotifyOAuth

from chopin.config import Config
from chopin.storage import spotify_oauth_cache_path


_PLAYLIST_URL = re.compile(r"open\.spotify\.com/(?:[^/]+/)*playlist/([A-Za-z0-9]+)")
_PLAYLIST_URI = re.compile(r"spotify:playlist:([A-Za-z0-9]+)")
_BARE_ID = re.compile(r"^[A-Za-z0-9]{16,}$")

LIKED_ID = "liked"
LIKED_ALIASES = {"liked", "saved", "polubione", "ulubione", "liked songs"}

ProgressCb = Callable[[int, int], None]
LogCb = Callable[[str], None]


def parse_playlist_id(value: str) -> str:
    value = value.strip()
    if value.lower() in LIKED_ALIASES:
        return LIKED_ID
    m = _PLAYLIST_URL.search(value)
    if m:
        return m.group(1)
    m = _PLAYLIST_URI.match(value)
    if m:
        return m.group(1)
    if _BARE_ID.match(value):
        return value
    raise ValueError(f"cannot parse playlist id from: {value!r}")


def make_client(config: Config, scope: str) -> spotipy.Spotify:
    auth = SpotifyOAuth(
        client_id=config.spotify_client_id,
        client_secret=config.spotify_client_secret,
        redirect_uri=config.spotify_redirect_uri,
        scope=scope,
        cache_path=str(spotify_oauth_cache_path()),
        open_browser=True,
    )
    return spotipy.Spotify(auth_manager=auth)


def ensure_auth(client: spotipy.Spotify) -> None:
    """Force-acquire access token before any spinner UI starts.

    The first API call against a fresh client may trigger an OAuth flow which
    spotipy logs to stdout (e.g. "Opening in existing browser session."). Doing
    this eagerly before any Spinner avoids interleaved output.
    """
    am = client.auth_manager
    if hasattr(am, "get_access_token"):
        am.get_access_token(as_dict=False)


def find_playlist_by_name(
    client: spotipy.Spotify, name: str, log: LogCb | None = None
) -> str:
    target = name.strip().lower()
    if log:
        log(f"searching playlists for {name!r}")
    results = client.current_user_playlists(limit=50)
    page = 1
    while results:
        for pl in results.get("items", []):
            if not pl:
                continue
            if (pl.get("name") or "").strip().lower() == target:
                if log:
                    log(f"matched on page {page}: id={pl['id']}")
                return pl["id"]
        if results.get("next"):
            page += 1
            if log:
                log(f"scanning playlists page {page}")
            results = client.next(results)
        else:
            break
    raise ValueError(f"playlist not found by name: {name!r}")


def resolve_playlist(client: spotipy.Spotify, value: str) -> str:
    """Resolve user input → playlist id (or LIKED_ID).

    Accepts URL, URI, bare id, liked-songs aliases, or a playlist name owned
    by the current user. Falls back to name lookup when value isn't a URL/URI/id.
    """
    try:
        return parse_playlist_id(value)
    except ValueError:
        return find_playlist_by_name(client, value)


def _extract_track(track: dict | None) -> dict | None:
    if not track or not track.get("id"):
        return None
    artists = track.get("artists") or []
    artist = ", ".join(a.get("name", "") for a in artists if a.get("name"))
    title = track.get("name") or ""
    if not artist or not title:
        return None
    return {"id": track["id"], "artist": artist, "title": title}


def _fetch_liked(
    client: spotipy.Spotify,
    progress: ProgressCb | None = None,
    log: LogCb | None = None,
) -> dict:
    if log:
        log("requesting first batch of saved tracks (50)")
    results = client.current_user_saved_tracks(limit=50)
    total = int(results.get("total") or 0)
    if log:
        log(f"total saved tracks: {total}")
    seen: set[str] = set()
    tracks: list[dict] = []
    fetched = 0
    while results:
        items = results.get("items", []) or []
        for item in items:
            t = _extract_track(item.get("track") if item else None)
            if not t or t["id"] in seen:
                continue
            seen.add(t["id"])
            tracks.append(t)
        fetched += len(items)
        if progress:
            progress(min(fetched, total) if total else fetched, total)
        if results.get("next"):
            if log:
                log(f"requesting next batch (offset {fetched})")
            results = client.next(results)
        else:
            break
    return {
        "playlist_id": LIKED_ID,
        "playlist_name": "Liked Songs",
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "count": len(tracks),
        "tracks": tracks,
    }


def fetch_playlist(
    client: spotipy.Spotify,
    playlist_id: str,
    progress: ProgressCb | None = None,
    log: LogCb | None = None,
) -> dict:
    if playlist_id == LIKED_ID:
        return _fetch_liked(client, progress, log)
    if log:
        log(f"requesting playlist metadata for {playlist_id}")
    meta = client.playlist(playlist_id, fields="id,name,tracks(total)")
    total = int(((meta.get("tracks") or {}).get("total")) or 0)
    if log:
        log(f"playlist {meta.get('name', '?')!r}: {total} tracks")
    seen: set[str] = set()
    tracks: list[dict] = []
    fetched = 0
    results = client.playlist_items(
        playlist_id,
        fields="items(track(id,name,artists(name))),next",
        additional_types=("track",),
    )
    while results:
        items = results.get("items", []) or []
        for item in items:
            t = _extract_track(item.get("track") if item else None)
            if not t or t["id"] in seen:
                continue
            seen.add(t["id"])
            tracks.append(t)
        fetched += len(items)
        if progress:
            progress(min(fetched, total) if total else fetched, total)
        if results.get("next"):
            if log:
                log(f"requesting next batch (offset {fetched})")
            results = client.next(results)
        else:
            break
    return {
        "playlist_id": meta["id"],
        "playlist_name": meta.get("name", ""),
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "count": len(tracks),
        "tracks": tracks,
    }


def search_track(
    client: spotipy.Spotify,
    artist: str,
    title: str,
    cache: dict[str, str | None],
) -> str | None:
    key = f"{artist.lower().strip()}||{title.lower().strip()}"
    if key in cache:
        return cache[key]
    q = f'track:"{title}" artist:"{artist}"'
    try:
        result = client.search(q=q, type="track", limit=1)
    except spotipy.SpotifyException:
        cache[key] = None
        return None
    items = (result.get("tracks") or {}).get("items") or []
    track_id = items[0]["id"] if items else None
    cache[key] = track_id
    return track_id


def add_tracks(
    client: spotipy.Spotify, playlist_id: str, track_ids: list[str]
) -> None:
    for i in range(0, len(track_ids), 100):
        batch = track_ids[i : i + 100]
        client.playlist_add_items(playlist_id, batch)


READ_SCOPE = "playlist-read-private user-library-read"
MODIFY_SCOPE = (
    "playlist-modify-public playlist-modify-private playlist-read-private "
    "user-library-read user-library-modify"
)
