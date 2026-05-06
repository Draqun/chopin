"""Typer-based CLI entry point."""

from __future__ import annotations

import sys
import threading
import time
from typing import Callable

import questionary
import spotipy
import typer

from chopin import lastfm as lastfm_mod
from chopin import spotify as spotify_mod
from chopin import storage
from chopin.config import Config, ConfigError, load_config
from chopin.matching import Track, diff_tracks


app = typer.Typer(no_args_is_help=True, add_completion=False)
fetch_app = typer.Typer(no_args_is_help=True, help="Fetch data from Last.fm or Spotify")
app.add_typer(fetch_app, name="fetch")


def _die(msg: str) -> "typer.Exit":
    typer.echo(f"error: {msg}", err=True)
    return typer.Exit(code=1)


def _config_or_die() -> Config:
    try:
        return load_config()
    except ConfigError as e:
        raise _die(str(e)) from e


class Spinner:
    """Stderr spinner with optional progress detail and discrete log lines.

    On non-TTY stderr, degrades to plain log lines (no animation, no \\r tricks).
    """

    FRAMES = ("|", "/", "-", "\\")
    INTERVAL = 0.1

    def __init__(self, label: str) -> None:
        self.label = label
        self.detail = ""
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._tty = bool(getattr(sys.stderr, "isatty", lambda: False)())

    def _draw(self, frame: str) -> None:
        text = f"\r\x1b[K{frame} {self.label}"
        if self.detail:
            text += f" — {self.detail}"
        sys.stderr.write(text)
        sys.stderr.flush()

    def _run(self) -> None:
        i = 0
        while not self._stop.is_set():
            with self._lock:
                self._draw(self.FRAMES[i % len(self.FRAMES)])
            i += 1
            self._stop.wait(self.INTERVAL)

    def update(self, detail: str) -> None:
        with self._lock:
            self.detail = detail
            if not self._tty:
                # in non-tty mode emit a discrete progress line
                sys.stderr.write(f"{self.label}: {detail}\n")
                sys.stderr.flush()

    def log(self, msg: str) -> None:
        with self._lock:
            if self._tty:
                # clear current spinner line, write log, spinner redraws on next tick
                sys.stderr.write(f"\r\x1b[K{msg}\n")
            else:
                sys.stderr.write(f"{msg}\n")
            sys.stderr.flush()

    def __enter__(self) -> "Spinner":
        if self._tty:
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()
        else:
            sys.stderr.write(f"{self.label} ...\n")
            sys.stderr.flush()
        return self

    def __exit__(self, *_: object) -> None:
        if self._tty:
            self._stop.set()
            if self._thread:
                self._thread.join()
            sys.stderr.write("\r\x1b[K")
            sys.stderr.flush()


def _load_lastfm_or_die() -> tuple[list[Track], bool]:
    """Return (tracks, has_playcount). has_playcount=False for legacy caches."""
    data = storage.load_json(storage.lastfm_path())
    if data is None:
        raise _die("no last.fm cache found. run `chopin fetch lastfm` first.")
    has_playcount = bool(data["tracks"]) and "playcount" in data["tracks"][0]
    tracks = [
        Track(
            artist=t["artist"],
            title=t["title"],
            album=t.get("album"),
            playcount=t.get("playcount"),
        )
        for t in data["tracks"]
    ]
    return tracks, has_playcount


def _load_spotify_or_die(playlist_id: str) -> tuple[dict, list[Track]]:
    data = storage.load_json(storage.spotify_path(playlist_id))
    if data is None:
        hint = (
            "chopin fetch spotify"
            if playlist_id == spotify_mod.LIKED_ID
            else f"chopin fetch spotify {playlist_id}"
        )
        raise _die(
            f"no spotify cache for playlist {playlist_id}. run `{hint}` first."
        )
    tracks = [Track(artist=t["artist"], title=t["title"]) for t in data["tracks"]]
    return data, tracks


def _resolve_playlist(
    config: Config, value: str, scope: str
) -> tuple[spotipy.Spotify, str]:
    """Return (client, playlist_id). Tries cheap parse first, falls back to name lookup."""
    client = spotify_mod.make_client(config, scope)
    typer.echo("authorizing Spotify ...", err=True)
    spotify_mod.ensure_auth(client)
    try:
        playlist_id = spotify_mod.parse_playlist_id(value)
    except ValueError:
        with Spinner(f"resolving playlist name {value!r}") as spin:
            try:
                playlist_id = spotify_mod.find_playlist_by_name(
                    client, value, log=spin.log
                )
            except ValueError as e:
                raise _die(str(e)) from e
    return client, playlist_id


@fetch_app.command("lastfm")
def fetch_lastfm() -> None:
    """Fetch full scrobble history from Last.fm."""
    config = _config_or_die()
    started = time.monotonic()
    with Spinner(f"fetching scrobbles for {config.lastfm_user}") as spin:
        def progress(done: int, total: int) -> None:
            pct = done * 100 // total if total else 0
            spin.update(f"page {done}/{total} ({pct}%)")

        payload = lastfm_mod.fetch_all_scrobbles(
            config.lastfm_api_key,
            config.lastfm_user,
            progress=progress,
            log=spin.log,
        )
    storage.save_json(storage.lastfm_path(), payload)
    elapsed = time.monotonic() - started
    typer.echo(
        f"fetched {payload['count']} unique tracks in {elapsed:.1f}s "
        f"→ {storage.lastfm_path()}"
    )


@fetch_app.command("spotify")
def fetch_spotify(
    playlist: str = typer.Argument(
        "liked",
        help="Playlist URL, URI, ID, name, or 'liked' (default = Liked Songs)",
    ),
) -> None:
    """Fetch tracks from a Spotify playlist (defaults to Liked Songs)."""
    config = _config_or_die()
    client, playlist_id = _resolve_playlist(config, playlist, spotify_mod.READ_SCOPE)
    label = "Liked Songs" if playlist_id == spotify_mod.LIKED_ID else f"playlist {playlist_id}"
    started = time.monotonic()
    with Spinner(f"fetching {label}") as spin:
        def progress(done: int, total: int) -> None:
            pct = done * 100 // total if total else 0
            if total:
                spin.update(f"{done}/{total} tracks ({pct}%)")
            else:
                spin.update(f"{done} tracks")

        payload = spotify_mod.fetch_playlist(
            client, playlist_id, progress=progress, log=spin.log
        )
    storage.save_json(storage.spotify_path(playlist_id), payload)
    elapsed = time.monotonic() - started
    typer.echo(
        f"fetched {payload['count']} tracks from \"{payload['playlist_name']}\" "
        f"in {elapsed:.1f}s → {storage.spotify_path(playlist_id)}"
    )


@app.command()
def diff(
    playlist: str = typer.Argument(
        "liked",
        help="Playlist URL, URI, ID, name, or 'liked' (default = Liked Songs)",
    ),
) -> None:
    """Show tracks scrobbled on Last.fm but missing from the playlist."""
    try:
        playlist_id = spotify_mod.parse_playlist_id(playlist)
    except ValueError:
        config = _config_or_die()
        client = spotify_mod.make_client(config, spotify_mod.READ_SCOPE)
        typer.echo("authorizing Spotify ...", err=True)
        spotify_mod.ensure_auth(client)
        with Spinner(f"resolving playlist name {playlist!r}") as spin:
            try:
                playlist_id = spotify_mod.find_playlist_by_name(
                    client, playlist, log=spin.log
                )
            except ValueError as e:
                raise _die(str(e)) from e
    lastfm_tracks, _ = _load_lastfm_or_die()
    _, spotify_tracks = _load_spotify_or_die(playlist_id)
    missing = diff_tracks(lastfm_tracks, spotify_tracks)
    typer.echo(
        f"{len(lastfm_tracks)} unique tracks on last.fm, "
        f"{len(spotify_tracks)} on playlist, "
        f"{len(missing)} missing",
        err=True,
    )
    if not missing:
        typer.echo("nothing missing — playlist covers all your last.fm tracks", err=True)
        return
    for t in missing:
        typer.echo(f"{t.artist} — {t.title}")


@app.command()
def add(
    playlist: str = typer.Argument(..., help="Playlist URL, URI, ID, or name"),
) -> None:
    """Interactively add missing tracks to the Spotify playlist."""
    config = _config_or_die()
    client, playlist_id = _resolve_playlist(config, playlist, spotify_mod.MODIFY_SCOPE)
    if playlist_id == spotify_mod.LIKED_ID:
        raise _die(
            "adding to Liked Songs not supported yet. pass a regular playlist."
        )
    lastfm_tracks, _ = _load_lastfm_or_die()
    _, spotify_tracks = _load_spotify_or_die(playlist_id)
    missing = diff_tracks(lastfm_tracks, spotify_tracks)
    if not missing:
        typer.echo("nothing missing — playlist covers all your last.fm tracks")
        return
    if len(missing) > 500:
        typer.echo(
            f"warning: {len(missing)} missing tracks — picker may be unwieldy. "
            "consider `chopin diff <playlist> > missing.txt` for offline review.",
            err=True,
        )
    choices = [
        questionary.Choice(title=f"{t.artist} — {t.title}", value=i)
        for i, t in enumerate(missing)
    ]
    answer = questionary.checkbox(
        f"select tracks to add to playlist ({len(missing)} missing)",
        choices=choices,
    ).ask()
    if not answer:
        typer.echo("nothing selected, aborting")
        return
    selected = [missing[i] for i in answer]
    cache_path = storage.search_cache_path()
    cache: dict[str, str | None] = storage.load_json(cache_path) or {}
    resolved: list[str] = []
    skipped: list[Track] = []
    with Spinner("searching tracks on Spotify") as spin:
        for i, t in enumerate(selected, 1):
            spin.update(f"{i}/{len(selected)}: {t.artist} — {t.title}")
            track_id = spotify_mod.search_track(client, t.artist, t.title, cache)
            if track_id:
                resolved.append(track_id)
            else:
                skipped.append(t)
                spin.log(f"no match: {t.artist} — {t.title}")
    storage.save_json(cache_path, cache)
    if resolved:
        with Spinner(f"adding {len(resolved)} tracks to playlist"):
            spotify_mod.add_tracks(client, playlist_id, resolved)
    typer.echo(f"added {len(resolved)} tracks")
    if skipped:
        typer.echo(f"skipped {len(skipped)} (no Spotify match):")
        for t in skipped:
            typer.echo(f"  - {t.artist} — {t.title}")


def main() -> None:  # pragma: no cover
    try:
        app()
    except KeyboardInterrupt:
        typer.echo("\naborted", err=True)
        sys.exit(130)


if __name__ == "__main__":  # pragma: no cover
    main()
