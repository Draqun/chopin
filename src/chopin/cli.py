"""Typer-based CLI entry point."""

from __future__ import annotations

import sys
import time

import questionary
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


def _load_lastfm_or_die() -> list[Track]:
    data = storage.load_json(storage.lastfm_path())
    if data is None:
        raise _die("no last.fm cache found. run `chopin fetch lastfm` first.")
    return [
        Track(artist=t["artist"], title=t["title"], album=t.get("album"))
        for t in data["tracks"]
    ]


def _load_spotify_or_die(playlist_id: str) -> tuple[dict, list[Track]]:
    data = storage.load_json(storage.spotify_path(playlist_id))
    if data is None:
        raise _die(
            f"no spotify cache for playlist {playlist_id}. "
            f"run `chopin fetch spotify {playlist_id}` first."
        )
    tracks = [Track(artist=t["artist"], title=t["title"]) for t in data["tracks"]]
    return data, tracks


@fetch_app.command("lastfm")
def fetch_lastfm() -> None:
    """Fetch full scrobble history from Last.fm."""
    config = _config_or_die()
    typer.echo(f"fetching scrobbles for {config.lastfm_user} ...")
    started = time.monotonic()
    payload = lastfm_mod.fetch_all_scrobbles(config.lastfm_api_key, config.lastfm_user)
    storage.save_json(storage.lastfm_path(), payload)
    elapsed = time.monotonic() - started
    typer.echo(
        f"fetched {payload['count']} unique tracks in {elapsed:.1f}s "
        f"→ {storage.lastfm_path()}"
    )


@fetch_app.command("spotify")
def fetch_spotify(playlist: str = typer.Argument(..., help="Playlist URL, URI or ID")) -> None:
    """Fetch tracks from a Spotify playlist."""
    config = _config_or_die()
    try:
        playlist_id = spotify_mod.parse_playlist_id(playlist)
    except ValueError as e:
        raise _die(str(e)) from e
    client = spotify_mod.make_client(config, spotify_mod.READ_SCOPE)
    typer.echo(f"fetching playlist {playlist_id} ...")
    payload = spotify_mod.fetch_playlist(client, playlist_id)
    storage.save_json(storage.spotify_path(playlist_id), payload)
    typer.echo(
        f"fetched {payload['count']} tracks from \"{payload['playlist_name']}\" "
        f"→ {storage.spotify_path(playlist_id)}"
    )


@app.command()
def diff(playlist: str = typer.Argument(..., help="Playlist URL, URI or ID")) -> None:
    """Show tracks scrobbled on Last.fm but missing from the playlist."""
    try:
        playlist_id = spotify_mod.parse_playlist_id(playlist)
    except ValueError as e:
        raise _die(str(e)) from e
    lastfm_tracks = _load_lastfm_or_die()
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
def add(playlist: str = typer.Argument(..., help="Playlist URL, URI or ID")) -> None:
    """Interactively add missing tracks to the Spotify playlist."""
    config = _config_or_die()
    try:
        playlist_id = spotify_mod.parse_playlist_id(playlist)
    except ValueError as e:
        raise _die(str(e)) from e
    lastfm_tracks = _load_lastfm_or_die()
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
    client = spotify_mod.make_client(config, spotify_mod.MODIFY_SCOPE)
    cache_path = storage.search_cache_path()
    cache: dict[str, str | None] = storage.load_json(cache_path) or {}
    resolved: list[str] = []
    skipped: list[Track] = []
    for t in selected:
        track_id = spotify_mod.search_track(client, t.artist, t.title, cache)
        if track_id:
            resolved.append(track_id)
        else:
            skipped.append(t)
    storage.save_json(cache_path, cache)
    if resolved:
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
