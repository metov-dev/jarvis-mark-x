from actions import music_player as M


def _item(name, *artists, uri="spotify:track:x"):
    return {"name": name, "artists": [{"name": a} for a in artists], "uri": uri}


def test_wrong_top_result_is_rejected():
    """Журнал 27.11 23:34: «поставь музыку Не виновен» включило «Ayriliq» — Zohid."""
    assert not M._track_matches("не виновен", _item("Ayriliq", "Zohid"))


def test_title_or_title_plus_artist_matches():
    assert M._track_matches("не виновен", _item("Не виновен", "Кто-то"))
    assert M._track_matches("Ummon Sen Kechir", _item("Sen Kechir", "Ummon"))
    assert M._track_matches("ёлки", _item("Елки", "x"))


def test_search_falls_back_to_none_without_match(monkeypatch):
    import io
    import json

    monkeypatch.setattr(M, "_SPOTIFY_BLOCKED_UNTIL", 0)
    monkeypatch.setattr(M, "_get_spotify_token", lambda: "t")
    asked = []

    def fake_urlopen(req, timeout=0):
        asked.append(req.full_url)
        body = {"tracks": {"items": [_item("Ayriliq", "Zohid")]}}
        return io.BytesIO(json.dumps(body).encode())

    monkeypatch.setattr(M.urllib.request, "urlopen", fake_urlopen)
    assert M._spotify_search_track_uri("не виновен") is None
    assert all("q=%D0%BD%D0%B5&" not in u for u in asked)        # не искал по одному «не»
