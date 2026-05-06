"""Typer-based CLI entry point."""

from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path
from typing import Callable

import questionary
import spotipy
import typer

from chopin import lastfm as lastfm_mod
from chopin import spotify as spotify_mod
from chopin import storage
from chopin.config import Config, ConfigError, load_config
from chopin.matching import Track, diff_tracks, normalize_key


app = typer.Typer(no_args_is_help=True, add_completion=False)
fetch_app = typer.Typer(no_args_is_help=True, help="Fetch data from Last.fm or Spotify")
lastfm_app = typer.Typer(no_args_is_help=True, help="Last.fm utilities")
app.add_typer(fetch_app, name="fetch")
app.add_typer(lastfm_app, name="lastfm")


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


def _load_lastfm_or_die() -> tuple[list[Track], bool, bool]:
    """Return (tracks, has_playcount, has_listeners).

    has_playcount/has_listeners are False for caches without those columns
    (older fetches, or before `chopin lastfm lonely` was run).
    """
    data = storage.load_json(storage.lastfm_path())
    if data is None:
        raise _die("no last.fm cache found. run `chopin fetch lastfm` first.")
    has_playcount = bool(data["tracks"]) and "playcount" in data["tracks"][0]
    has_listeners = any(
        t.get("listeners") is not None for t in data.get("tracks") or []
    )
    tracks = [
        Track(
            artist=t["artist"],
            title=t["title"],
            album=t.get("album"),
            playcount=t.get("playcount"),
            listeners=t.get("listeners"),
        )
        for t in data["tracks"]
    ]
    return tracks, has_playcount, has_listeners


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
    old = storage.load_json(storage.lastfm_path()) or {}
    old_listeners = {
        normalize_key(t["artist"], t["title"]): t.get("listeners")
        for t in (old.get("tracks") or [])
        if t.get("listeners") is not None
    }
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
    if old_listeners:
        carried = 0
        for t in payload["tracks"]:
            key = normalize_key(t["artist"], t["title"])
            if key in old_listeners:
                t["listeners"] = old_listeners[key]
                carried += 1
        if carried:
            typer.echo(
                f"carried over listener counts for {carried} known tracks",
                err=True,
            )
    storage.save_json(storage.lastfm_path(), payload)
    elapsed = time.monotonic() - started
    typer.echo(
        f"fetched {payload['count']} unique tracks in {elapsed:.1f}s "
        f"→ {storage.lastfm_path()}"
    )


_LISTENER_CHECKPOINT_EVERY = 50
# Push tracks to Spotify in chunks during the search loop so partial work
# survives rate limits, network blips, and Ctrl+C. Smaller than the API per-call
# cap (50 for Liked, 100 for playlists) so failures don't lose a full batch.
_ADD_FLUSH_EVERY = 25

# sentinel for cache lookup — None means "no Spotify match" (legitimate, cached);
# missing key means "never searched, please search".
_CACHE_MISSING = object()


@lastfm_app.command("lonely")
def lastfm_lonely(
    max_listeners: int = typer.Option(
        1,
        "--max-listeners",
        "-n",
        min=1,
        help="Print tracks where total last.fm listeners is at most N (default 1).",
    ),
) -> None:
    """List tracks where you are (essentially) the only listener.

    Fills missing `listeners` counts in the cache via track.getInfo. The
    cache is checkpointed periodically so an interrupted run resumes.
    """
    config = _config_or_die()
    data = storage.load_json(storage.lastfm_path())
    if data is None:
        raise _die("no last.fm cache found. run `chopin fetch lastfm` first.")
    tracks = data.get("tracks") or []
    if not tracks:
        typer.echo("last.fm cache is empty", err=True)
        return

    missing = [t for t in tracks if t.get("listeners") is None]
    typer.echo(
        f"{len(tracks)} tracks total; {len(missing)} need a listener lookup",
        err=True,
    )

    if missing:
        rate = lastfm_mod._PER_REQUEST_DELAY
        eta_min = (len(missing) * rate) / 60
        typer.echo(
            f"estimated wall time: ~{eta_min:.1f} min (rate-limited at "
            f"{1 / rate:.0f} req/s)",
            err=True,
        )
        with Spinner("fetching listener counts") as spin:
            for i, t in enumerate(missing, 1):
                spin.update(
                    f"{i}/{len(missing)}: {t['artist']} — {t['title']}"
                )
                try:
                    listeners = lastfm_mod.get_track_listeners(
                        config.lastfm_api_key, t["artist"], t["title"]
                    )
                except RuntimeError as e:
                    spin.log(f"error: {e} — saving and aborting")
                    storage.save_json(storage.lastfm_path(), data)
                    raise _die(str(e)) from e
                t["listeners"] = listeners
                if i % _LISTENER_CHECKPOINT_EVERY == 0:
                    storage.save_json(storage.lastfm_path(), data)
                    spin.log(
                        f"checkpoint: {i}/{len(missing)} written to cache"
                    )
        storage.save_json(storage.lastfm_path(), data)

    lonely = [
        t
        for t in tracks
        if t.get("listeners") is not None
        and t["listeners"] != lastfm_mod.TRACK_UNKNOWN
        and t["listeners"] <= max_listeners
    ]
    unknown = [
        t
        for t in tracks
        if t.get("listeners") == lastfm_mod.TRACK_UNKNOWN
    ]

    typer.echo(
        f"{len(lonely)} lonely tracks (<= {max_listeners} listener(s)), "
        f"{len(unknown)} unknown to last.fm",
        err=True,
    )
    if unknown:
        typer.echo(
            "(unknown = last.fm has no entry for the artist/title — "
            "almost certainly junk)",
            err=True,
        )

    rows = lonely + unknown
    rows.sort(
        key=lambda t: (
            t.get("listeners")
            if t.get("listeners") is not None
            and t["listeners"] != lastfm_mod.TRACK_UNKNOWN
            else -1,
            -(t.get("playcount") or 0),
            t["artist"].lower(),
            t["title"].lower(),
        )
    )
    for t in rows:
        listeners = t.get("listeners")
        marker = (
            "?"
            if listeners is None or listeners == lastfm_mod.TRACK_UNKNOWN
            else str(listeners)
        )
        plays = t.get("playcount") or 1
        typer.echo(
            f"{marker:>4} listener(s), {plays:>4} plays — "
            f"{t['artist']} — {t['title']}"
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


def _resolve_many_playlists(
    values: list[str], scope: str
) -> list[str]:
    """Resolve a list of playlist references to ids. Auths once if any need name lookup."""
    needs_lookup: list[str] = []
    cheap: list[str] = []
    for v in values:
        try:
            cheap.append(spotify_mod.parse_playlist_id(v))
        except ValueError:
            needs_lookup.append(v)
    if not needs_lookup:
        return cheap
    config = _config_or_die()
    client = spotify_mod.make_client(config, scope)
    typer.echo("authorizing Spotify ...", err=True)
    spotify_mod.ensure_auth(client)
    resolved: list[str] = list(cheap)
    for v in needs_lookup:
        with Spinner(f"resolving playlist name {v!r}") as spin:
            try:
                resolved.append(
                    spotify_mod.find_playlist_by_name(client, v, log=spin.log)
                )
            except ValueError as e:
                raise _die(str(e)) from e
    return resolved


def _load_exclude_keys(playlist_ids: list[str]) -> set[str]:
    """Load cached tracks for each id, return union of normalized keys.

    Dies with a helpful message when any cache is missing.
    """
    keys: set[str] = set()
    for pid in playlist_ids:
        data = storage.load_json(storage.spotify_path(pid))
        if data is None:
            hint = (
                "chopin fetch spotify"
                if pid == spotify_mod.LIKED_ID
                else f"chopin fetch spotify {pid}"
            )
            raise _die(
                f"no cache for exclude playlist {pid!r}. run `{hint}` first."
            )
        for t in data.get("tracks", []):
            keys.add(normalize_key(t["artist"], t["title"]))
    return keys


@app.command()
def diff(
    playlist: str = typer.Argument(
        "liked",
        help="Playlist URL, URI, ID, name, or 'liked' (default = Liked Songs)",
    ),
    min_plays: int = typer.Option(
        1,
        "--min-plays",
        "-m",
        min=1,
        help="Drop tracks with fewer than N plays on last.fm (default 1 = no filter).",
    ),
    exclude: list[str] = typer.Option(
        None,
        "--exclude",
        "-x",
        help="Exclude tracks present on another (already fetched) playlist. Repeatable.",
    ),
    exclude_lonely: int = typer.Option(
        0,
        "--exclude-lonely",
        "-L",
        min=0,
        help="Drop tracks with at most N total last.fm listeners (also drops "
        "tracks unknown to last.fm). 0 = off. Requires `chopin lastfm lonely`.",
    ),
    output: Path | None = typer.Option(
        None,
        "--output",
        "-o",
        help="Write the filtered missing-track list as JSON to FILE "
        "(consume it with `chopin add --input FILE`).",
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

    lastfm_tracks, has_playcount, has_listeners = _load_lastfm_or_die()
    if min_plays > 1 and not has_playcount:
        raise _die(
            "last.fm cache has no playcount data (legacy format). "
            "refetch with `chopin fetch lastfm` to use --min-plays."
        )
    if exclude_lonely and not has_listeners:
        raise _die(
            "last.fm cache has no listener data. "
            "run `chopin lastfm lonely` first to populate it."
        )

    _, spotify_tracks = _load_spotify_or_die(playlist_id)

    exclude_ids: list[str] = []
    if exclude:
        exclude_ids = _resolve_many_playlists(exclude, spotify_mod.READ_SCOPE)
    exclude_keys = _load_exclude_keys(exclude_ids) if exclude_ids else set()

    missing = diff_tracks(lastfm_tracks, spotify_tracks)
    filtered = missing
    dropped_min = 0
    if min_plays > 1:
        before = len(filtered)
        filtered = [t for t in filtered if (t.playcount or 0) >= min_plays]
        dropped_min = before - len(filtered)
    dropped_excl = 0
    if exclude_keys:
        before = len(filtered)
        filtered = [
            t for t in filtered if normalize_key(t.artist, t.title) not in exclude_keys
        ]
        dropped_excl = before - len(filtered)
    dropped_lonely = 0
    if exclude_lonely:
        before = len(filtered)
        filtered = [
            t
            for t in filtered
            if t.listeners is not None
            and t.listeners != lastfm_mod.TRACK_UNKNOWN
            and t.listeners > exclude_lonely
        ]
        dropped_lonely = before - len(filtered)

    summary = (
        f"{len(lastfm_tracks)} unique tracks on last.fm, "
        f"{len(spotify_tracks)} on playlist, "
        f"{len(missing)} missing"
    )
    if dropped_min:
        summary += f", {dropped_min} dropped by --min-plays {min_plays}"
    if dropped_excl:
        summary += f", {dropped_excl} dropped by --exclude"
    if dropped_lonely:
        summary += f", {dropped_lonely} dropped by --exclude-lonely {exclude_lonely}"
    summary += f", {len(filtered)} remaining"
    typer.echo(summary, err=True)

    if output:
        payload = {
            "playlist_id": playlist_id,
            "filters": {
                "min_plays": min_plays if min_plays > 1 else None,
                "exclude": list(exclude) if exclude else None,
                "exclude_lonely": exclude_lonely or None,
            },
            "count": len(filtered),
            "tracks": [
                {
                    "artist": t.artist,
                    "title": t.title,
                    "playcount": t.playcount,
                    "listeners": t.listeners,
                }
                for t in filtered
            ],
        }
        storage.save_json(output, payload)
        typer.echo(f"wrote {len(filtered)} tracks → {output}", err=True)

    if not filtered:
        typer.echo("nothing remaining — all missing tracks filtered out", err=True)
        return
    for t in filtered:
        typer.echo(f"{t.artist} — {t.title}")


def _load_diff_input(path: Path) -> list[Track]:
    """Load tracks produced by `chopin diff --output FILE`."""
    if not path.exists():
        raise _die(f"input file not found: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise _die(f"input file is not valid JSON: {e}") from e
    raw = data.get("tracks") if isinstance(data, dict) else data
    if not isinstance(raw, list):
        raise _die(
            f"input file shape unexpected — need a list of tracks, "
            f"or {{\"tracks\": [...]}}"
        )
    tracks: list[Track] = []
    for i, item in enumerate(raw):
        if not isinstance(item, dict) or "artist" not in item or "title" not in item:
            raise _die(f"input file entry {i} missing artist/title")
        tracks.append(
            Track(
                artist=item["artist"],
                title=item["title"],
                playcount=item.get("playcount"),
                listeners=item.get("listeners"),
            )
        )
    return tracks


def _format_candidate(c: dict) -> str:
    artists = ", ".join(c.get("artists") or [])
    name = c.get("name") or ""
    album = c.get("album") or ""
    duration_ms = c.get("duration_ms")
    if duration_ms:
        secs = duration_ms // 1000
        duration = f"{secs // 60}:{secs % 60:02d}"
    else:
        duration = "?:??"
    parts = [f"{artists} — {name}"]
    if album:
        parts.append(f"[{album}]")
    parts.append(f"({duration})")
    return " ".join(parts)


def _pick_candidate(query: Track, candidates: list[dict]) -> dict | None:
    """Show a picker for multiple Spotify search hits. Returns chosen dict or None."""
    typer.echo(
        f"\nmultiple matches for: {query.artist} — {query.title}", err=True
    )
    choices = [
        questionary.Choice(title=_format_candidate(c), value=i)
        for i, c in enumerate(candidates)
    ]
    choices.append(questionary.Choice(title="(skip — don't add)", value=-1))
    answer = questionary.select(
        "pick a version", choices=choices, default=choices[0]
    ).ask()
    if answer is None or answer == -1:
        return None
    return candidates[answer]


@app.command()
def add(
    playlist: str = typer.Argument(..., help="Playlist URL, URI, ID, or name"),
    input_file: Path = typer.Option(
        ...,
        "--input",
        "-i",
        help="Path to a JSON file produced by `chopin diff --output FILE`.",
    ),
    no_confirm: bool = typer.Option(
        False,
        "--no-confirm",
        help="Skip the input-list picker and feed every track from the input "
        "file into the search step.",
    ),
    auto_pick: bool = typer.Option(
        False,
        "--auto-pick",
        "-A",
        help="When the Spotify search returns multiple matches, auto-pick the "
        "first one (legacy behavior). Default: ask interactively.",
    ),
    search_limit: int = typer.Option(
        5,
        "--search-limit",
        min=1,
        max=20,
        help="How many search hits to consider per track (1 = always first).",
    ),
    skipped_output: Path | None = typer.Option(
        None,
        "--skipped",
        "-s",
        help="Write tracks that ended up not-added (no match, API error, or "
        "user-skipped) as JSON to FILE for later review or rerun.",
    ),
) -> None:
    """Add tracks listed in --input to the Spotify playlist."""
    config = _config_or_die()
    client, playlist_id = _resolve_playlist(config, playlist, spotify_mod.MODIFY_SCOPE)
    candidates = _load_diff_input(input_file)
    if not candidates:
        typer.echo("input file is empty, nothing to add", err=True)
        return

    if no_confirm:
        selected = candidates
        typer.echo(
            f"feeding all {len(selected)} tracks from {input_file} "
            "into search (--no-confirm)",
            err=True,
        )
    else:
        if len(candidates) > 500:
            typer.echo(
                f"warning: {len(candidates)} tracks in input — picker may be "
                "unwieldy. consider editing the JSON file or rerunning "
                "`chopin diff --output` with tighter filters.",
                err=True,
            )
        choices = [
            questionary.Choice(title=f"{t.artist} — {t.title}", value=i)
            for i, t in enumerate(candidates)
        ]
        answer = questionary.checkbox(
            f"select tracks to add to playlist ({len(candidates)} candidates)",
            choices=choices,
        ).ask()
        if not answer:
            typer.echo("nothing selected, aborting")
            return
        selected = [candidates[i] for i in answer]

    cache_path = storage.search_cache_path()
    cache: dict[str, str | None] = storage.load_json(cache_path) or {}
    pushed_path = storage.pushed_log_path(playlist_id)
    already_pushed: set[str] = set(storage.load_json(pushed_path) or [])
    if already_pushed:
        typer.echo(
            f"resuming: {len(already_pushed)} tracks already pushed to this "
            "playlist by a prior run, will be skipped",
            err=True,
        )
    pending: list[str] = []
    added_total = 0
    no_match: list[Track] = []
    errors: list[tuple[Track, str]] = []
    user_skipped: list[Track] = []

    def flush_pending(reason: str) -> None:
        nonlocal added_total
        if not pending:
            return
        new_ids = [tid for tid in pending if tid not in already_pushed]
        pending_count = len(pending)
        if not new_ids:
            typer.echo(
                f"already pushed: {pending_count} tracks "
                f"(skipping — {reason})",
                err=True,
            )
            pending.clear()
            return
        try:
            spotify_mod.add_tracks(client, playlist_id, new_ids)
        except spotipy.SpotifyException as e:
            typer.echo(
                f"flush failed ({len(new_ids)} new tracks held back, "
                f"will retry on next run): {e}",
                err=True,
            )
            return
        already_pushed.update(new_ids)
        storage.save_json(pushed_path, sorted(already_pushed))
        added_total += len(new_ids)
        dup_skipped = pending_count - len(new_ids)
        msg = (
            f"pushed {len(new_ids)} tracks to playlist "
            f"({added_total} total — {reason})"
        )
        if dup_skipped:
            msg += f"; {dup_skipped} already pushed earlier, skipped"
        typer.echo(msg, err=True)
        pending.clear()
        storage.save_json(cache_path, cache)

    typer.echo(
        f"searching {len(selected)} tracks on Spotify "
        f"(picker: {'first hit auto' if auto_pick else 'interactive'}, "
        f"flush every {_ADD_FLUSH_EVERY})",
        err=True,
    )
    try:
        for i, t in enumerate(selected, 1):
            prefix = f"[{i}/{len(selected)}]"
            key = spotify_mod.search_cache_key(t.artist, t.title)
            cached = cache.get(key, _CACHE_MISSING)
            if cached is not _CACHE_MISSING:
                if cached is None:
                    no_match.append(t)
                    typer.echo(
                        f"{prefix} cached no match: {t.artist} — {t.title}",
                        err=True,
                    )
                else:
                    pending.append(cached)
                    typer.echo(
                        f"{prefix} cached: {t.artist} — {t.title}", err=True
                    )
            else:
                typer.echo(
                    f"{prefix} searching: {t.artist} — {t.title}", err=True
                )
                try:
                    hits = spotify_mod.search_track_candidates(
                        client, t.artist, t.title, limit=search_limit
                    )
                except spotipy.SpotifyException as e:
                    if getattr(e, "http_status", None) == 429:
                        retry_after = (e.headers or {}).get("Retry-After")
                        typer.echo(
                            f"{prefix} HARD RATE LIMIT (429). retry-after="
                            f"{retry_after}s. aborting run — flushing what "
                            "we have. wait it out, switch to another Spotify "
                            "client_id, or rerun later (search cache and "
                            "pushed log preserve progress).",
                            err=True,
                        )
                        flush_pending("rate-limit abort")
                        return
                    errors.append((t, str(e)))
                    typer.echo(
                        f"{prefix} api error (not cached): {e}", err=True
                    )
                    continue

                if not hits:
                    cache[key] = None
                    no_match.append(t)
                    typer.echo(
                        f"{prefix} no match: {t.artist} — {t.title}", err=True
                    )
                    continue

                if len(hits) == 1 or auto_pick:
                    chosen = hits[0]
                else:
                    chosen = _pick_candidate(t, hits)
                    if chosen is None:
                        user_skipped.append(t)
                        typer.echo(f"{prefix} skipped by user", err=True)
                        continue

                cache[key] = chosen["id"]
                pending.append(chosen["id"])
                typer.echo(
                    f"{prefix} → {_format_candidate(chosen)}", err=True
                )

            if len(pending) >= _ADD_FLUSH_EVERY:
                flush_pending(f"batch at {i}/{len(selected)}")
    except KeyboardInterrupt:
        typer.echo(
            "\ninterrupted — flushing what we have before exiting ...",
            err=True,
        )
        flush_pending("interrupted")
        storage.save_json(cache_path, cache)
        raise
    finally:
        storage.save_json(cache_path, cache)

    flush_pending("end")

    typer.echo(f"added {added_total} tracks")
    if no_match:
        typer.echo(f"skipped {len(no_match)} (no Spotify match):")
        for t in no_match:
            typer.echo(f"  - {t.artist} — {t.title}")
    if errors:
        typer.echo(f"errored on {len(errors)} (transient API errors, not cached):")
        for t, err in errors:
            typer.echo(f"  - {t.artist} — {t.title}: {err}")
    if user_skipped:
        typer.echo(f"user-skipped {len(user_skipped)}:")
        for t in user_skipped:
            typer.echo(f"  - {t.artist} — {t.title}")

    if skipped_output is not None:
        not_added = no_match + [t for t, _ in errors] + user_skipped
        payload = {
            "playlist_id": playlist_id,
            "input_file": str(input_file),
            "count": len(not_added),
            "categories": {
                "no_match": len(no_match),
                "errors": len(errors),
                "user_skipped": len(user_skipped),
            },
            "tracks": [
                {
                    "artist": t.artist,
                    "title": t.title,
                    "playcount": t.playcount,
                    "listeners": t.listeners,
                }
                for t in not_added
            ],
        }
        storage.save_json(skipped_output, payload)
        typer.echo(
            f"wrote {len(not_added)} skipped tracks → {skipped_output}",
            err=True,
        )


def main() -> None:  # pragma: no cover
    try:
        app()
    except KeyboardInterrupt:
        typer.echo("\naborted", err=True)
        sys.exit(130)


if __name__ == "__main__":  # pragma: no cover
    main()
