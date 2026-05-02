# chopin — plan implementacji

Data: 2026-05-02
Bazuje na: `2026-05-02-chopin-design.md`.

## Założenia

- Solo użytkownik, nie dystrybuujemy.
- Testy tylko dla czystej logiki (`matching`, `storage`). Integracje z Last.fm
  i Spotify nie są mockowane — szkoda czasu na fakeowanie API dla narzędzia
  prywatnego.
- Brak CI, brak typecheckera, brak linterów na tym etapie. Można dodać
  później jeśli się okaże potrzebne.

## Etapy

### 1. Szkielet projektu

Pliki:

- `pyproject.toml` — metadata, dependencies, entry point `chopin`.
- `.gitignore` — `.env`, `.venv`, `__pycache__`, `*.egg-info`, `dist/`.
- `.env.example` — szablon zmiennych środowiskowych.
- `src/chopin/__init__.py` — pusty.
- `src/chopin/__main__.py` — `from chopin.cli import app; app()`.

Kryterium zakończenia: `uv sync` przechodzi, `uv run chopin --help` pokazuje
listę komend (po implementacji `cli.py`, ale szkielet `app = typer.Typer()`
wystarczy do przejścia importu).

### 2. `storage.py`

Funkcje:

- `data_dir() -> Path` — `platformdirs.user_data_dir("chopin")`, tworzy
  katalog jeśli go nie ma.
- `lastfm_path() -> Path`.
- `spotify_path(playlist_id: str) -> Path`.
- `search_cache_path() -> Path`.
- `spotify_oauth_cache_path() -> Path`.
- `load_json(path: Path) -> dict | None` — `None` jeśli brak pliku.
- `save_json(path: Path, data: dict) -> None` — atomic write przez tmp +
  `os.replace`, `ensure_ascii=False`, `indent=2`.

### 3. `config.py`

- `load_config() -> Config` — `python-dotenv.load_dotenv()`, czyta zmienne
  z env, waliduje, zwraca dataclass `Config(lastfm_api_key, lastfm_user,
  spotify_client_id, spotify_client_secret, spotify_redirect_uri)`.
- Brak klucza → wyjątek `ConfigError` z czytelnym komunikatem. CLI łapie i
  printuje + exit 1.

### 4. `matching.py`

- `normalize_key(artist, title) -> str` — implementacja z designu.
- `_clean(s) -> str` — helper, prywatny.
- `Track` dataclass: `artist`, `title`, `album: str | None`.
- `diff_tracks(lastfm: list[Track], spotify: list[Track]) -> list[Track]` —
  zwraca listę z `lastfm` której kluczy nie ma w `spotify`, posortowaną po
  `(artist.lower(), title.lower())`.

### 5. `lastfm.py`

- `fetch_all_scrobbles(api_key, username) -> dict` — używa pylast
  `User.get_recent_tracks(limit=None)` (pylast pagination obsłuży sam, ale
  trzeba opakować w retry).
- Retry: max 3 próby, exponential backoff 2/4/8s, na `pylast.NetworkError`
  i `pylast.WSError` (5xx).
- Po pobraniu — dedup przez set `(artist, title)`, sortowanie po `artist,
  title`.
- Zwraca dict gotowy do `save_json` (z `user`, `fetched_at`, `count`,
  `tracks`).

### 6. `spotify.py`

- `parse_playlist_id(value: str) -> str` — accepting URL/URI/bare ID.
- `make_client(config, scope) -> spotipy.Spotify` — `SpotifyOAuth` z cache
  pathem z `storage`. Scope dla `add` = `playlist-modify-public
  playlist-modify-private`. Scope dla `fetch_playlist` = pusty/None
  (publiczne i prywatne playlisty user'a wymagają `playlist-read-private`).
- `fetch_playlist(client, playlist_id) -> dict` — `playlist()` + paginacja
  `tracks()`, do `next` aż exhausted. Pomijamy `None` tracks i lokalne pliki.
- `search_track(client, artist, title, cache) -> str | None` — sprawdza
  cache, jeśli brak woła `client.search(q="track:X artist:Y", type="track",
  limit=1)`, zapisuje wynik do cache (włącznie z `None`).
- `add_tracks(client, playlist_id, track_ids) -> None` — batch po 100,
  `playlist_add_items`.

### 7. `cli.py`

```python
app = typer.Typer(no_args_is_help=True)
fetch_app = typer.Typer(no_args_is_help=True)
app.add_typer(fetch_app, name="fetch")

@fetch_app.command("lastfm")
def fetch_lastfm(): ...

@fetch_app.command("spotify")
def fetch_spotify(playlist: str): ...

@app.command()
def diff(playlist: str): ...

@app.command()
def add(playlist: str): ...
```

Każda komenda:
1. Łapie `ConfigError` i `FileNotFoundError` (brak cache'u) → drukuje błąd,
   exit 1.
2. Drukuje progress przez `typer.echo` / `rich.print` (jeśli wpadnie z
   typerem).
3. `add` używa `questionary.checkbox` z paged scrolling.

### 8. Testy

`tests/test_matching.py`:

- normalizacja: parens, square brackets, " - Remastered 2011" suffix,
  whitespace collapse, non-ASCII pass-through.
- diff: przypadki gdy missing pusty, gdy całe lastfm missing, gdy klucze
  matchują pomimo różnych formatów.

`tests/test_storage.py`:

- save_json + load_json roundtrip z non-ASCII.
- atomic write — sprawdzenie że tmp file jest sprzątany.

### 9. Weryfikacja

- `uv sync` — instalacja zależności.
- `uv run pytest` — testy przechodzą.
- `uv run chopin --help` — Typer pokazuje strukturę komend.
- `uv run chopin fetch --help`, `uv run chopin fetch lastfm --help`, etc.

Smoke test live (wymaga `.env` i będzie pominięty w autonomicznej
implementacji — to dla późniejszego ręcznego testu):

- `uv run chopin fetch lastfm`
- `uv run chopin fetch spotify <jakaś playlista>`
- `uv run chopin diff <id>` — sanity check
- `uv run chopin add <id>` — picker, dorzucenie 1-2 utworów

## Kolejność wykonania

```
1. szkielet (pyproject + struktura)
2. storage.py        ──┐
3. config.py           ├─ niezależne, można równolegle
4. matching.py + testy ┘
5. lastfm.py     (potrzebuje storage, config)
6. spotify.py    (potrzebuje storage, config, matching, search cache)
7. cli.py        (potrzebuje wszystkiego)
8. testy storage
9. uv sync, pytest, --help
```

## Co świadomie pomijam

- Mockowanie API w testach.
- Property-based tests.
- Type hints — będą, ale bez `mypy` w CI.
- Logging na poziomie modułów — `typer.echo` wystarczy.
- Konfiguracja przez `~/.config/chopin/config.toml` (tylko `.env`).
