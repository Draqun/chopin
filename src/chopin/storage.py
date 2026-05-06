"""Filesystem layout and JSON I/O for chopin's local cache."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from platformdirs import user_data_dir

_APP_NAME = "chopin"


def data_dir() -> Path:
    path = Path(user_data_dir(_APP_NAME))
    path.mkdir(parents=True, exist_ok=True)
    return path


def lastfm_path() -> Path:
    return data_dir() / "lastfm.json"


def spotify_path(playlist_id: str) -> Path:
    return data_dir() / f"spotify_{playlist_id}.json"


def search_cache_path() -> Path:
    return data_dir() / "search_cache.json"


def pushed_log_path(playlist_id: str) -> Path:
    return data_dir() / f"pushed_{playlist_id}.json"


def spotify_oauth_cache_path() -> Path:
    return data_dir() / ".spotify_cache"


def load_json(path: Path) -> Any | None:
    if not path.exists():
        return None
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
