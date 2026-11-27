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


def test_premium_find_rejects_unrelated_top_track(monkeypatch):
    """Журнал 27.11 23:50: «не виновен» → «Take Me There», потом Madonna."""
    from actions import spotify_premium as sp

    class R:
        def raise_for_status(self): ...

        def json(self):
            return {"tracks": {"items": [_item("Take Me There", "DA TI"), _item("La Isla Bonita", "Madonna")]},
                    "artists": {"items": []}}

    monkeypatch.setattr(sp, "_req", lambda *a, **k: R())
    assert sp.find("не виновен") is None
    monkeypatch.setattr(sp, "_req", lambda *a, **k: type("R2", (R,), {"json": lambda self: {
        "tracks": {"items": [_item("Не виновен", "Скриптонит")]}, "artists": {"items": []}}})())
    assert sp.find("не виновен")[0] == "spotify:track:x"


def test_started_ignores_old_paused_track_resuming(monkeypatch):
    """28.11 00:24: стоял на паузе «Город дорог», открыли «Люби меня» — Play
    возобновил старую, и она засчиталась как включённая."""
    from types import SimpleNamespace as NS

    from core import media_session
    before = NS(title="Город дорог", artist="MONA", playing=False)
    states = iter([NS(title="Город дорог", artist="MONA", playing=True)] * 3 +
                  [NS(title="Люби меня", artist="MiyaGi", playing=True)])
    monkeypatch.setattr(media_session, "now_playing", lambda app=None: next(states))
    monkeypatch.setattr(media_session, "command", lambda *a, **k: True)
    monkeypatch.setattr(M.time, "sleep", lambda s: None)
    assert M._started(before, timeout=5).title == "Люби меня"
