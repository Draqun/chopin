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
  - Add `http://localhost:8888/callback` to the app's Redirect URIs.

## Install

```sh
git clone <this repo> chopin
cd chopin
uv sync           # or: python -m venv .venv && pip install -e .
```

This installs the `chopin` command into the project's virtualenv.

## Configure

Create a `.env` file in the project root (it is gitignored):

```dotenv
# Last.fm
LASTFM_API_KEY=your_lastfm_api_key
LASTFM_USER=your_lastfm_username

# Spotify
SPOTIPY_CLIENT_ID=your_spotify_client_id
SPOTIPY_CLIENT_SECRET=your_spotify_client_secret
SPOTIPY_REDIRECT_URI=http://localhost:8888/callback
```

The first time you run a Spotify command, a browser window opens for OAuth
consent. The refresh token is cached at `~/.local/share/chopin/.spotify_cache`
so subsequent runs don't prompt again.

## Usage

### 1. Pull your Last.fm history

```sh
chopin fetch lastfm
```

Walks `user.getRecentTracks` page by page until exhausted. Writes everything
to `~/.local/share/chopin/lastfm.json`. Re-running overwrites the cache.

### 2. Pull a Spotify playlist

```sh
chopin fetch spotify <playlist>
```

`<playlist>` accepts any of:

- a full URL: `https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M`
- a Spotify URI: `spotify:playlist:37i9dQZF1DXcBWIGoYBM5M`
- a bare playlist ID: `37i9dQZF1DXcBWIGoYBM5M`

Writes to `~/.local/share/chopin/spotify_<playlist_id>.json`.

### 3. Show what's missing

```sh
chopin diff <playlist>
```

Reads both caches, prints a one-line summary and the full list of missing
tracks in `artist - title` format, one per line. Pipe-friendly:

```sh
chopin diff <playlist> | grep -i "radiohead"
chopin diff <playlist> | wc -l
```

Read-only — does not modify anything.

### 4. Interactively add missing tracks

```sh
chopin add <playlist>
```

Opens an interactive checklist of the missing tracks. Toggle with space,
confirm with enter. For each selected track, `chopin` issues a Spotify search
(`track:<title> artist:<artist>`), takes the first hit, and adds it to the
playlist. Search results are cached at
`~/.local/share/chopin/search_cache.json` so re-runs don't re-query the API.

Tracks that don't return any Spotify hit are reported at the end and skipped.

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
