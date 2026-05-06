# chopin

A small CLI tool that compares your Last.fm scrobble history against a Spotify
playlist and helps you fill in the gaps. Pulls everything you have ever
scrobbled, pulls a chosen playlist, computes the set difference, and lets you
interactively pick which missing tracks to add.

## What it does

1. Fetches your full scrobble history from Last.fm and caches it locally as
   JSON.
2. Fetches the tracks of a chosen Spotify playlist and caches them locally as
   JSON.
3. Computes `set(last.fm) - set(playlist)` using a normalized
   `artist - title` key (lowercased, parenthetical suffixes like
   `(Remastered 2011)` stripped, whitespace collapsed).
4. Shows the missing tracks, or opens an interactive picker so you can select
   which ones to add to the playlist.

The four steps are independent commands. `diff` and `add` only read the local
JSON cache — they never hit the network on their own. You decide when to
refresh by re-running `fetch`.

## Requirements

- Python 3.11+
- A Last.fm API account (free): https://www.last.fm/api/account/create
- A Spotify developer app: https://developer.spotify.com/dashboard
  - Add `http://127.0.0.1:8888/callback` to the app's Redirect URIs.
    (Spotify is deprecating `localhost` as a redirect host — use the
    loopback IP instead.)

## Install

There are two install modes. Pick the one that matches what you're doing.

### A) Just use the tool (recommended for end users)

Install `chopin` as a global CLI on your PATH (via `uv tool`):

```sh
git clone https://github.com/Draqun/chopin.git
cd chopin
make init           # create dev venv + scaffold .env from template
make install-cli    # build wheel and install ~/.local/bin/chopin globally
```

After this, `chopin --help` works from any directory. Make sure
`~/.local/bin` is on your `PATH`.

To update later (pulls latest from GitHub `main` and reinstalls):

```sh
make update-cli
# or a different branch:
make update-cli REPO_BRANCH=experiments
```

To remove:

```sh
make uninstall-cli
```

### B) Develop on chopin

If you're editing the source, you don't need a global install — just use
the dev venv:

```sh
git clone https://github.com/Draqun/chopin.git
cd chopin
make init
```

Then run the CLI through `uv run` (or activate `.venv/`):

```sh
uv run chopin --help
# or
source .venv/bin/activate
chopin --help
```

The Makefile targets (`make fetch-lastfm`, `make diff`, etc.) wrap
`uv run chopin ...` so they always use the local source — handy while
hacking on the code.

## Configure

Create a `.env` file in the project root (it is gitignored):

```dotenv
# Last.fm
LASTFM_API_KEY=your_lastfm_api_key
LASTFM_USER=your_lastfm_username

# Spotify
SPOTIPY_CLIENT_ID=your_spotify_client_id
SPOTIPY_CLIENT_SECRET=your_spotify_client_secret
SPOTIPY_REDIRECT_URI=http://127.0.0.1:8888/callback
```

The first time you run a Spotify command, a browser window opens for OAuth
consent. The refresh token is cached at `~/.local/share/chopin/.spotify_cache`
so subsequent runs don't prompt again.

## Usage

All long-running commands print live progress to stderr — a spinner with a
status line on a TTY, plain log lines when piped or redirected.

### 1. Pull your Last.fm history

```sh
chopin fetch lastfm
```

Walks `user.getRecentTracks` page by page until exhausted, counting how many
times you've scrobbled each track along the way. Writes everything to
`~/.local/share/chopin/lastfm.json`. Re-running overwrites the cache.

### 2. Pull a Spotify playlist

```sh
chopin fetch spotify [<playlist>]
```

With no argument, fetches your **Liked Songs** (the default). `<playlist>`
accepts any of:

- a full URL: `https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M`
- a Spotify URI: `spotify:playlist:37i9dQZF1DXcBWIGoYBM5M`
- a bare playlist ID: `37i9dQZF1DXcBWIGoYBM5M`
- a playlist **name** owned by you: `"Discover Weekly"` — looked up against
  your playlists; an exact case-insensitive match wins
- an alias for Liked Songs: `liked`, `saved`, `polubione`, `ulubione`,
  `liked songs`

Liked Songs are stored under the sentinel id `liked`
(`~/.local/share/chopin/spotify_liked.json`); regular playlists go to
`~/.local/share/chopin/spotify_<playlist_id>.json`.

### 3. Show what's missing

```sh
chopin diff [<playlist>] [--min-plays N] [--exclude PLAYLIST]... \
            [--exclude-lonely N] [--output FILE]
```

Reads both caches, prints a one-line summary and the full list of missing
tracks in `artist - title` format, one per line. Defaults to comparing
against Liked Songs. Pipe-friendly:

```sh
chopin diff | grep -i "radiohead"
chopin diff "Discover Weekly" | wc -l
```

Three filters refine the missing list:

- `--min-plays N` (`-m`) drops tracks scrobbled fewer than N times. Useful
  for skipping one-off plays of songs you didn't actually like. Requires a
  cache produced after the playcount feature landed; older caches are
  rejected with a refetch hint.
- `--exclude PLAYLIST` (`-x`) subtracts tracks present on another already
  fetched playlist. Repeatable. Each value accepts URL, URI, bare id, or
  playlist name. The referenced playlists must already be cached locally.
- `--exclude-lonely N` (`-L`) drops tracks with at most N total Last.fm
  listeners (and tracks Last.fm has never heard of). Requires the listener
  data to be populated first via `chopin lastfm lonely` (see below).

`--output FILE` (`-o`) additionally writes the filtered result as JSON to
FILE. That file is the input for `chopin add` (see the next section), so
the two commands form a small pipeline you can inspect and edit between
steps.

```sh
chopin fetch spotify Smutne
chopin diff -m 3 -x Smutne -x "Discover Weekly" -L 1 \
            --output picks.json
```

Read-only — does not modify anything on Spotify.

### 4. Find tracks where you're the only listener

The YouTube scrobbler historically dumped non-music videos into Last.fm.
You can identify those by looking at how many other people on Last.fm have
ever scrobbled the same track — usually nobody:

```sh
chopin lastfm lonely               # default: only-listener tracks
chopin lastfm lonely -n 3          # also include tracks with <=3 listeners
```

The first run is slow (one `track.getInfo` request per cached track,
rate-limited at ~5 req/s) but the listener count is persisted into
`lastfm.json`, so subsequent runs only look up newly seen tracks. The
cache is checkpointed every 50 lookups, so an interrupted run resumes
where it left off. `chopin fetch lastfm` carries listener data forward
across refetches.

Tracks not known to Last.fm at all are tagged `?` in the output —
those are the most likely junk.

### 5. Add tracks from a diff to a playlist

```sh
chopin add <playlist> --input picks.json \
          [--no-confirm] [--auto-pick] \
          [--search-limit N] [--skipped FILE]
```

`add` consumes the JSON file produced by `chopin diff --output`. This is
deliberate — it keeps `add` and `diff` in sync (no duplicated filter
flags) and lets you inspect or hand-edit the file between the two steps:

```sh
chopin diff Liked -m 3 -L 1 --output picks.json
$EDITOR picks.json                       # optional: trim noise by hand
chopin add Liked --input picks.json
```

The flow has two interactive stages:

1. **Input picker** — a checklist of all tracks from the input file. Toggle
   with space, confirm with enter. `--no-confirm` skips this stage and
   feeds the entire input file into the search step.
2. **Per-track disambiguation** — for each selected track, `chopin`
   queries Spotify (`track:<title> artist:<artist>`) and asks how many
   hits to consider via `--search-limit N` (default 5). When the search
   returns more than one match, you pick which version to add. The
   picker shows `artists — title [album] (m:ss)` per candidate, plus a
   "skip" choice. `--auto-pick` (`-A`) restores the legacy first-hit-wins
   behavior.

Search results are cached at `~/.local/share/chopin/search_cache.json`
so subsequent runs don't re-query for the same `(artist, title)` pair.
Only definitive outcomes are cached — transient API errors (rate-limit,
network glitches, 5xx) are reported and retried on the next run rather
than poisoning the cache as "no match".

Tracks are pushed to Spotify **incrementally** in batches of 25 during
the search loop, not at the end. That means partial progress survives
Ctrl+C, rate limits, or any other crash — rerun `chopin add` against
the same input file and the search cache will resolve already-pushed
tracks instantly while picking up where the previous run left off.

Per-playlist, `chopin` records the IDs it has already pushed in
`~/.local/share/chopin/pushed_<playlist_id>.json` (or
`pushed_liked.json`). On resume, anything already in that log is skipped
before the API call — no duplicates on regular playlists where
`playlist_add_items` is not idempotent. Liked Songs are idempotent on
Spotify's side, so the log there is just a courtesy.

After the search step, `add` reports three categories of non-additions:

- **no Spotify match** — Spotify returned zero hits.
- **API errors** — transient failures; rerun and they'll be retried.
- **user-skipped** — you chose "skip" in the disambiguation picker.

`--skipped FILE` writes all of those to a JSON file in the same shape
that `--input` consumes, so you can review them later, hand-fix titles,
and rerun `chopin add` against that file.

`<playlist>` accepts the same forms as `fetch spotify`, including the
Liked Songs aliases (`liked`, `polubione`, …) — saved tracks use a
separate API endpoint (`current_user_saved_tracks_add`, capped at 50 per
call), which `add_tracks` switches to automatically.

## Data storage

All state lives under `~/.local/share/chopin/`:

```
~/.local/share/chopin/
├── lastfm.json                       # full scrobble history
├── spotify_<playlist_id>.json        # one file per fetched playlist
├── search_cache.json                 # cached Spotify search results
└── .spotify_cache                    # spotipy OAuth refresh token
```

To start clean, delete the file in question and re-run the relevant `fetch`.

## Limitations

- **Naive matching.** Tracks are compared by normalized `artist - title`. This
  misses live versions vs studio, alternate spellings, transliterations, and
  inconsistent featuring formatting. False negatives (tracks reported as
  missing when they are actually on the playlist under a slightly different
  spelling) are expected.
- **First Spotify hit wins.** `add` does not let you disambiguate when the
  search returns multiple matches. Review the playlist after running `add` if
  you care about edition accuracy.
- **No incremental Last.fm sync.** `fetch lastfm` always pulls everything from
  scratch. Fine for tens of thousands of scrobbles, slow above that.
- **Single user.** Configuration is global per machine — no profile
  switching.

## License

MIT — see [LICENSE](LICENSE).
