"""Load and validate configuration from .env."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import find_dotenv, load_dotenv
from platformdirs import user_config_dir


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class Config:
    lastfm_api_key: str
    lastfm_user: str
    spotify_client_id: str
    spotify_client_secret: str
    spotify_redirect_uri: str


_REQUIRED = (
    "LASTFM_API_KEY",
    "LASTFM_USER",
    "SPOTIPY_CLIENT_ID",
    "SPOTIPY_CLIENT_SECRET",
    "SPOTIPY_REDIRECT_URI",
)


def _candidate_dotenvs() -> list[Path]:
    """.env files to try, in priority order: explicit env, project-relative, user config."""
    paths: list[Path] = []
    explicit = os.environ.get("CHOPIN_DOTENV")
    if explicit:
        paths.append(Path(explicit))
    # walk up from CWD looking for a .env (works when run from the project)
    found = find_dotenv(usecwd=True)
    if found:
        paths.append(Path(found))
    # global config: e.g. ~/.config/chopin/.env on Linux
    paths.append(Path(user_config_dir("chopin")) / ".env")
    return paths


def load_config() -> Config:
    for candidate in _candidate_dotenvs():
        if candidate.is_file():
            load_dotenv(candidate, override=False)
            break
    missing = [k for k in _REQUIRED if not os.environ.get(k)]
    if missing:
        joined = ", ".join(missing)
        raise ConfigError(
            f"missing credentials: {joined}\n"
            "set them via environment variables, CHOPIN_DOTENV=/path/to/.env, "
            f"a project-local .env, or {Path(user_config_dir('chopin')) / '.env'}"
        )
    return Config(
        lastfm_api_key=os.environ["LASTFM_API_KEY"],
        lastfm_user=os.environ["LASTFM_USER"],
        spotify_client_id=os.environ["SPOTIPY_CLIENT_ID"],
        spotify_client_secret=os.environ["SPOTIPY_CLIENT_SECRET"],
        spotify_redirect_uri=os.environ["SPOTIPY_REDIRECT_URI"],
    )
