import json
from pathlib import Path

from chopin import storage


def test_save_and_load_json_roundtrip(tmp_path: Path):
    target = tmp_path / "data.json"
    payload = {"user": "damian", "tracks": [{"artist": "Behemoth", "title": "Ora Pro Nobis Lucifer"}]}
    storage.save_json(target, payload)
    loaded = storage.load_json(target)
    assert loaded == payload


def test_save_json_preserves_non_ascii(tmp_path: Path):
    target = tmp_path / "data.json"
    payload = {"artist": "Behemoth", "title": "Ora Pro Nóbis Łucifer"}
    storage.save_json(target, payload)
    raw = target.read_text(encoding="utf-8")
    assert "Łucifer" in raw  # ensure_ascii=False
    assert json.loads(raw) == payload


def test_load_json_returns_none_for_missing(tmp_path: Path):
    assert storage.load_json(tmp_path / "missing.json") is None


def test_save_json_atomic_no_leftover_tmp(tmp_path: Path):
    target = tmp_path / "data.json"
    storage.save_json(target, {"hello": "world"})
    siblings = list(tmp_path.iterdir())
    assert siblings == [target]


def test_save_json_overwrites_existing(tmp_path: Path):
    target = tmp_path / "data.json"
    storage.save_json(target, {"v": 1})
    storage.save_json(target, {"v": 2})
    assert storage.load_json(target) == {"v": 2}


def test_paths_are_under_data_dir():
    base = storage.data_dir()
    assert storage.lastfm_path().parent == base
    assert storage.spotify_path("abc").name == "spotify_abc.json"
    assert storage.search_cache_path().name == "search_cache.json"
