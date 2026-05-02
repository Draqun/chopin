# chopin — design

Data: 2026-05-02
Status: zaakceptowany, gotowy do planu implementacji.

## Cel

CLI w Pythonie do porównywania historii scrobble z Last.fm z zawartością
wskazanej playlisty Spotify, oraz uzupełniania tej playlisty o brakujące
utwory. Bez automatyzacji — wszystko user-driven, jeden użytkownik.

Operacja konceptualna: `set(last.fm) - set(playlist)`.

## Sekcja 1 — Komendy i flow

Pakiet i entry point: `chopin`.

Cztery komendy:

```
chopin fetch lastfm                 # pobiera całą historię scrobble
chopin fetch spotify <playlist>     # pobiera tracki z playlisty Spotify
chopin diff <playlist>              # pokazuje brakujące utwory (read-only)
chopin add <playlist>               # interaktywny picker → dorzuca do playlisty
```

`<playlist>` przyjmuje URL, URI lub bare ID — wszystko sprowadzane do ID.

Typowa sesja:

```
$ chopin fetch lastfm
fetched 47823 scrobbles in 38s → ~/.local/share/chopin/lastfm.json

$ chopin fetch spotify 37i9dQZF1DXcBWIGoYBM5M
fetched 312 tracks → ~/.local/share/chopin/spotify_37i9dQZF1DXcBWIGoYBM5M.json

$ chopin diff 37i9dQZF1DXcBWIGoYBM5M
12834 unique tracks on last.fm, 312 on playlist, 12679 missing
artist — title
artist — title
...

$ chopin add 37i9dQZF1DXcBWIGoYBM5M
[picker questionary]
added 47 tracks
```

Założenie kluczowe: `diff` i `add` czytają wyłącznie z lokalnego cache'u.
Nie hitują API. Refresh przez ręczne `fetch …`.

`add` musi zmapować wybrane utwory na Spotify track IDs — robi to przez
`search?q=track:X artist:Y` i pierwszy hit. Wyniki cache'owane w
`search_cache.json`.

## Sekcja 2 — Stack i struktura kodu

Python 3.11+.

Zależności runtime:

| Pakiet | Po co |
|---|---|
| `typer` | CLI z grupami komend |
| `pylast` | klient Last.fm — paginacja `user.getRecentTracks` |
| `spotipy` | klient Spotify, OAuth, refresh tokens |
| `python-dotenv` | ładowanie `.env` |
| `questionary` | interaktywna checklista dla `add` |
| `platformdirs` | XDG-zgodne ścieżki |

Dependency manager: `uv` (lockfile, szybki venv).

Struktura projektu:

```
chopin/
├── pyproject.toml
├── README.md
├── .env.example
├── .gitignore
├── docs/plans/
│   └── 2026-05-02-chopin-design.md
└── src/chopin/
    ├── __init__.py
    ├── __main__.py
    ├── cli.py              # Typer app + dispatch komend
    ├── lastfm.py           # fetch_all_scrobbles()
    ├── spotify.py          # fetch_playlist(), add_tracks(), search_track()
    ├── matching.py         # normalize_key(), diff_tracks()
    ├── storage.py          # ścieżki, load/save_json()
    └── config.py           # ładowanie .env, walidacja
```

Layout `src/`. `cli.py` cienki — parsing argumentów + formatowanie outputu;
logika domenowa w innych modułach, testowalna bez Typera.

Entry point w `pyproject.toml`:

```toml
[project.scripts]
chopin = "chopin.cli:app"
```

## Sekcja 3 — Format danych i normalizacja

`~/.local/share/chopin/lastfm.json`:

```json
{
  "user": "damiang",
  "fetched_at": "2026-05-02T11:30:00Z",
  "count": 47823,
  "tracks": [
    {"artist": "Radiohead", "title": "Idioteque", "album": "Kid A"}
  ]
}
```

Duplikaty zwijamy do unikalnych `(artist, title)` na etapie `fetch lastfm`.
Album opcjonalny, tylko do display.

`~/.local/share/chopin/spotify_<playlist_id>.json`:

```json
{
  "playlist_id": "37i9dQZF1DXcBWIGoYBM5M",
  "playlist_name": "Today's Top Hits",
  "fetched_at": "2026-05-02T11:32:00Z",
  "count": 312,
  "tracks": [
    {"id": "1mea3bSkSGXuIRvnydlB5b", "artist": "Sabrina Carpenter", "title": "Espresso"}
  ]
}
```

`id` Spotify trzymany dla wykluczania w `add`, nie do matchingu.

`~/.local/share/chopin/search_cache.json`:

```json
{
  "radiohead||idioteque": "1bMwOfOhFRqNCpvuGwZsoG",
  "aphex twin||xtal": null
}
```

Klucz = znormalizowany `artist||title`. `null` = search bez hitu (negatywny
cache).

Normalizacja:

```python
def normalize_key(artist: str, title: str) -> str:
    a = _clean(artist)
    t = _clean(title)
    return f"{a}||{t}"

def _clean(s: str) -> str:
    s = s.lower().strip()
    s = re.sub(r"\s*[\(\[].*?[\)\]]\s*", " ", s)
    s = re.sub(r"\s+-\s+(remaster|remastered|live|mono|stereo).*$", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s
```

Diff:

```python
lastfm_keys = {normalize_key(t.artist, t.title): t for t in lastfm}
spotify_keys = {normalize_key(t.artist, t.title) for t in spotify}
missing = [t for k, t in lastfm_keys.items() if k not in spotify_keys]
```

`O(n+m)`. Sortowanie `missing` po `(artist.lower(), title.lower())` dla
deterministycznego outputu.

## Sekcja 4 — Error handling i edge cases

**Brak/niekompletny `.env`** — walidacja przy starcie każdej komendy:

```
error: missing LASTFM_API_KEY in .env
copy .env.example to .env and fill in credentials
exit 1
```

**Brak cache'u przy `diff`/`add`**:

```
error: no last.fm cache found. run `chopin fetch lastfm` first.
exit 1
```

**Last.fm failure w paginacji**: retry z backoff (3 próby, 2s/4s/8s) per
strona. Po wyczerpaniu — exit z informacją ile stron udało się pobrać. **Nie
zapisujemy częściowego cache'u** (lepiej zostawić stary).

**Spotify rate limit (429)**: spotipy respektuje `Retry-After` domyślnie.
Zostawiamy default.

**Spotify token expiry**: spotipy refresh'uje przez cache file. User nie widzi.

**Brak hitów przy `search` w `add`**: `null` w cache'u, na końcu raport
pominiętych:

```
added 47 tracks
skipped 3 (no Spotify match):
  - Foo Bar — Obscure Track
```

**Pusty diff**: `nothing missing — playlist covers all your last.fm tracks`,
exit 0. `add` nie odpala pickera.

**Duże listy w `add`** (>500 brakujących): ostrzeżenie + sugestia użycia
`chopin diff > missing.txt`, ale picker wstaje normalnie. Questionary
scrolluje i ma fuzzy search.

**Non-ASCII**: `encoding="utf-8"` wszędzie, `json.dump(..., ensure_ascii=False)`.
Bez `unidecode` w normalizacji — `Beyoncé` ≠ `Beyonce`, świadomy false-
negative.

**Atomic writes do JSON**: tmp file + `os.replace`, żeby Ctrl+C nie zostawił
uszkodzonego pliku.

## Out of scope (świadome wycięcia)

- Inkrementalny sync Last.fm (zawsze pełny pull).
- Fuzzy matching, MusicBrainz, transliteracja.
- Disambiguation przy wielu hitach Spotify search (zawsze pierwszy).
- Multi-user / profile.
- Dry-run dla `add`.
- Eksport do plików (`diff` printuje na stdout, user może sobie
  przekierować).
