"""Spotify client helpers: playlist fetch, search, add."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

import spotipy
from spotipy.oauth2 import SpotifyOAuth

from chopin.config import Config
from chopin.storage import spotify_oauth_cache_path


_PLAYLIST_URL = re.compile(r"open\.spotify\.com/(?:[^/]+/)*playlist/([A-Za-z0-9]+)")
_PLAYLIST_URI = re.compile(r"spotify:playlist:([A-Za-z0-9]+)")
_BARE_ID = re.compile(r"^[A-Za-z0-9]{16,}$")


def parse_playlist_id(value: str) -> str:
    value = value.strip()
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


def fetch_playlist(client: spotipy.Spotify, playlist_id: str) -> dict:
    meta = client.playlist(playlist_id, fields="id,name")
    tracks: list[dict] = []
    seen_ids: set[str] = set()
    results = client.playlist_items(
        playlist_id,
        fields="items(track(id,name,artists(name))),next",
        additional_types=("track",),
    )
    while results:
        for item in results.get("items", []):
            track = item.get("track")
            if not track or not track.get("id"):
                continue
            tid = track["id"]
            if tid in seen_ids:
                continue
            seen_ids.add(tid)
            artists = track.get("artists") or []
            artist = ", ".join(a.get("name", "") for a in artists if a.get("name"))
            title = track.get("name") or ""
            if not artist or not title:
                continue
            tracks.append({"id": tid, "artist": artist, "title": title})
        if results.get("next"):
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


READ_SCOPE = "playlist-read-private"
MODIFY_SCOPE = "playlist-modify-public playlist-modify-private playlist-read-private"
