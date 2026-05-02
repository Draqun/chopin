from chopin.matching import Track, diff_tracks, normalize_key


def test_normalize_lowercases_and_trims():
    assert normalize_key("  Radiohead  ", "Idioteque") == "radiohead||idioteque"


def test_normalize_strips_parenthetical_suffix():
    assert normalize_key("Radiohead", "Karma Police (Remastered 2011)") == normalize_key(
        "Radiohead", "Karma Police"
    )


def test_normalize_strips_square_brackets():
    assert normalize_key("Aphex Twin", "Xtal [Remix]") == normalize_key("Aphex Twin", "Xtal")


def test_normalize_strips_dash_remaster_suffix():
    assert normalize_key("The Beatles", "Let It Be - Remastered 2009") == normalize_key(
        "The Beatles", "Let It Be"
    )


def test_normalize_strips_dash_live_suffix():
    assert normalize_key("Radiohead", "Creep - Live at Glastonbury") == normalize_key(
        "Radiohead", "Creep"
    )


def test_normalize_collapses_whitespace():
    assert normalize_key("Foo   Bar", "Baz \t Qux") == "foo bar||baz qux"


def test_normalize_preserves_non_ascii():
    # świadomy false-negative: nie chcemy unidecode
    assert normalize_key("Beyoncé", "Halo") != normalize_key("Beyonce", "Halo")
    assert normalize_key("Beyoncé", "Halo") == "beyoncé||halo"


def test_diff_returns_only_missing():
    lastfm = [
        Track("Radiohead", "Idioteque"),
        Track("Aphex Twin", "Xtal"),
        Track("Boards of Canada", "Roygbiv"),
    ]
    spotify = [Track("Aphex Twin", "Xtal")]
    missing = diff_tracks(lastfm, spotify)
    assert [(t.artist, t.title) for t in missing] == [
        ("Boards of Canada", "Roygbiv"),
        ("Radiohead", "Idioteque"),
    ]


def test_diff_handles_normalized_match():
    lastfm = [Track("Radiohead", "Karma Police (Remastered 2011)")]
    spotify = [Track("Radiohead", "Karma Police")]
    assert diff_tracks(lastfm, spotify) == []


def test_diff_dedupes_lastfm_input():
    lastfm = [
        Track("Radiohead", "Creep"),
        Track("radiohead", "creep"),
        Track("Radiohead", "Creep (Acoustic)"),  # parens stripped → same key
    ]
    spotify: list[Track] = []
    missing = diff_tracks(lastfm, spotify)
    assert len(missing) == 1


def test_diff_empty_lastfm():
    assert diff_tracks([], [Track("X", "Y")]) == []


def test_diff_all_missing():
    lastfm = [Track("X", "Y"), Track("A", "B")]
    missing = diff_tracks(lastfm, [])
    assert [(t.artist, t.title) for t in missing] == [("A", "B"), ("X", "Y")]
