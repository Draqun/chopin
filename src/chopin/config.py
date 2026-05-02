"""Load and validate configuration from .env."""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv


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


def load_config() -> Config:
    load_dotenv()
    missing = [k for k in _REQUIRED if not os.environ.get(k)]
    if missing:
        joined = ", ".join(missing)
        raise ConfigError(
            f"missing in .env: {joined}\n"
            "copy .env.example to .env and fill in credentials"
        )
    return Config(
        lastfm_api_key=os.environ["LASTFM_API_KEY"],
        lastfm_user=os.environ["LASTFM_USER"],
        spotify_client_id=os.environ["SPOTIPY_CLIENT_ID"],
        spotify_client_secret=os.environ["SPOTIPY_CLIENT_SECRET"],
        spotify_redirect_uri=os.environ["SPOTIPY_REDIRECT_URI"],
    )
