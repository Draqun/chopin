import pytest

from chopin.spotify import parse_playlist_id


def test_parse_url():
    assert (
        parse_playlist_id("https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M")
        == "37i9dQZF1DXcBWIGoYBM5M"
    )


def test_parse_url_with_query():
    assert (
        parse_playlist_id(
            "https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M?si=abc123"
        )
        == "37i9dQZF1DXcBWIGoYBM5M"
    )


def test_parse_uri():
    assert parse_playlist_id("spotify:playlist:37i9dQZF1DXcBWIGoYBM5M") == "37i9dQZF1DXcBWIGoYBM5M"


def test_parse_bare_id():
    assert parse_playlist_id("37i9dQZF1DXcBWIGoYBM5M") == "37i9dQZF1DXcBWIGoYBM5M"


def test_parse_bare_id_strips_whitespace():
    assert parse_playlist_id("  37i9dQZF1DXcBWIGoYBM5M  ") == "37i9dQZF1DXcBWIGoYBM5M"


def test_parse_invalid_raises():
    with pytest.raises(ValueError):
        parse_playlist_id("not a playlist")


def test_parse_localized_url():
    assert (
        parse_playlist_id(
            "https://open.spotify.com/intl-pl/playlist/37i9dQZF1DXcBWIGoYBM5M"
        )
        == "37i9dQZF1DXcBWIGoYBM5M"
    )
