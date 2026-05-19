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
_BARE_ID = re.compile(r"^[A-Za-z0-9]{22}$")

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
    # Disable HTTP-status retries so a 429 with a multi-hour Retry-After
    # raises immediately instead of putting the process to sleep. We
    # handle 429 explicitly at the call site.
    return spotipy.Spotify(
        auth_manager=auth,
        retries=2,
        status_retries=0,
        backoff_factor=0.3,
    )


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


def _extract_track(
    item_obj: dict | None, skip: dict[str, int]
) -> dict | None:
    """Extract track from a playlist/saved-track item wrapper.

    Handles both the legacy `item.track` shape (saved tracks, older playlists)
    and the newer `item.item` shape (some playlists return the entry under
    'item' instead of 'track').
    """
    if not item_obj:
        skip["null_track"] += 1
        return None
    track = item_obj.get("track") or item_obj.get("item")
    if not track:
        skip["null_track"] += 1
        return None
    if track.get("is_local") or item_obj.get("is_local"):
        skip["is_local"] += 1
        return None
    if not track.get("id"):
        skip["no_id"] += 1
        return None
    artists = track.get("artists") or []
    artist = ", ".join(a.get("name", "") for a in artists if a.get("name"))
    title = track.get("name") or ""
    if not artist or not title:
        skip["no_artist_title"] += 1
        return None
    return {"id": track["id"], "artist": artist, "title": title}


def _new_skip_counter() -> dict[str, int]:
    return {"null_track": 0, "is_local": 0, "no_id": 0, "no_artist_title": 0}


def _log_skips(log: LogCb | None, skip: dict[str, int]) -> None:
    total = sum(skip.values())
    if log and total:
        parts = [f"{k}={v}" for k, v in skip.items() if v]
        log(f"skipped {total} items ({', '.join(parts)})")


def _user_market(client: spotipy.Spotify) -> str | None:
    try:
        me = client.current_user()
    except Exception:
        return None
    return me.get("country") or None


def _fetch_liked(
    client: spotipy.Spotify,
    progress: ProgressCb | None = None,
    log: LogCb | None = None,
) -> dict:
    market = _user_market(client)
    if log:
        log(f"using market={market or 'unspecified'}")
        log("requesting first batch of saved tracks (50)")
    results = client.current_user_saved_tracks(limit=50, market=market)
    total = int(results.get("total") or 0)
    if log:
        log(f"total saved tracks: {total}")
    seen: set[str] = set()
    tracks: list[dict] = []
    skip = _new_skip_counter()
    fetched = 0
    while results:
        items = results.get("items", []) or []
        for item in items:
            t = _extract_track(item, skip)
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
    _log_skips(log, skip)
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
    market = _user_market(client)
    if log:
        log(f"using market={market or 'unspecified'}")
        log(f"requesting playlist metadata for {playlist_id}")
    meta = client.playlist(playlist_id, fields="id,name", market=market)
    name = meta.get("name") or ""
    seen: set[str] = set()
    tracks: list[dict] = []
    skip = _new_skip_counter()
    fetched = 0
    # Spotify returns playlist entries under either `track` (legacy) or `item`
    # (newer); request both so the fields filter doesn't strip the right one.
    item_fields = (
        "track(id,name,artists(name),is_local),"
        "item(id,name,artists(name),is_local),"
        "is_local"
    )
    fields = f"items({item_fields}),next,total"
    results = client.playlist_items(
        playlist_id,
        fields=fields,
        additional_types=("track",),
        market=market,
    )
    total = int(results.get("total") or 0)
    if log:
        log(f"playlist {name!r}: {total} tracks")
    while results:
        items = results.get("items", []) or []
        for item in items:
            t = _extract_track(item, skip)
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
    _log_skips(log, skip)
    return {
        "playlist_id": meta["id"],
        "playlist_name": name,
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "count": len(tracks),
        "tracks": tracks,
    }


def search_cache_key(artist: str, title: str) -> str:
    return f"{artist.lower().strip()}||{title.lower().strip()}"


def search_track_candidates(
    client: spotipy.Spotify, artist: str, title: str, limit: int = 5
) -> list[dict]:
    """Return up to `limit` Spotify search hits for (artist, title).

    Raises spotipy.SpotifyException on API errors (rate limit, network, 5xx)
    so the caller can distinguish transient failures from a clean "no match".
    Returns an empty list for "no match" (no items in response).
    """
    q = f'track:"{title}" artist:"{artist}"'
    result = client.search(q=q, type="track", limit=limit)
    items = (result.get("tracks") or {}).get("items") or []
    candidates: list[dict] = []
    for it in items:
        artists = [a.get("name", "") for a in (it.get("artists") or []) if a.get("name")]
        candidates.append(
            {
                "id": it["id"],
                "name": it.get("name", ""),
                "artists": artists,
                "album": (it.get("album") or {}).get("name", ""),
                "duration_ms": it.get("duration_ms"),
            }
        )
    return candidates


def add_tracks(
    client: spotipy.Spotify, playlist_id: str, track_ids: list[str]
) -> None:
    """Add tracks to a regular playlist or to Liked Songs.

    Liked Songs use a different endpoint (`current_user_saved_tracks_add`)
    with a smaller per-call cap (50 vs 100 for playlists).
    """
    if playlist_id == LIKED_ID:
        for i in range(0, len(track_ids), 50):
            client.current_user_saved_tracks_add(track_ids[i : i + 50])
        return
    for i in range(0, len(track_ids), 100):
        client.playlist_add_items(playlist_id, track_ids[i : i + 100])


def verify_added(
    client: spotipy.Spotify, playlist_id: str, track_ids: list[str]
) -> set[str]:
    """Return the subset of `track_ids` that are actually present on target.

    Liked Songs: uses `current_user_saved_tracks_contains` (cheap, 50/call).
    Playlist: fetches the tail of `playlist_items` (added tracks land at the
    end) and intersects with the expected set. This costs 1 metadata call +
    ceil(N/100) item calls per verification, so verification should be invoked
    at flush boundaries, not per-track.
    """
    if not track_ids:
        return set()
    if playlist_id == LIKED_ID:
        present: set[str] = set()
        for i in range(0, len(track_ids), 50):
            chunk = track_ids[i : i + 50]
            flags = client.current_user_saved_tracks_contains(chunk)
            present.update(tid for tid, ok in zip(chunk, flags) if ok)
        return present

    head = client.playlist_items(
        playlist_id,
        limit=1,
        fields="total",
        additional_types=("track",),
    )
    total = head.get("total", 0)
    want = set(track_ids)
    # Look at the tail; pad by 50 for races with concurrent edits.
    window = len(track_ids) + 50
    offset = max(total - window, 0)
    seen: set[str] = set()
    while offset < total:
        page = client.playlist_items(
            playlist_id,
            offset=offset,
            limit=100,
            fields="items(track(id),item(id))",
            additional_types=("track",),
        )
        items = page.get("items") or []
        if not items:
            break
        for item in items:
            track = item.get("track") or item.get("item")
            if not track:
                continue
            tid = track.get("id")
            if tid in want:
                seen.add(tid)
        offset += len(items)
    return seen


READ_SCOPE = "playlist-read-private user-library-read user-read-private"
MODIFY_SCOPE = (
    "playlist-modify-public playlist-modify-private playlist-read-private "
    "user-library-read user-library-modify user-read-private"
)
