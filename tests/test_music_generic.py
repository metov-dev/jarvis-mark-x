"""«Включи музыку» без названия — свой плейлист сразу, без поиска по интернету."""
from actions import music_player as mp
from core import web_find
from core.web_find import Hit


def test_общие_просьбы_это_не_название():
    for q in ("хорошую музыку", "музыку", "что-нибудь", "какую-нибудь классную музыку", "музыку, хорош"):
        assert mp.is_generic(q), q
    for q in ("Растопи", "Mekhman Растопи", "Скриптонит", "музыку из Интерстеллара", ""):
        assert not mp.is_generic(q), q


def test_общая_просьба_включает_свой_плейлист(monkeypatch):
    played = []
    import actions.spotify_premium as sp
    monkeypatch.setattr(sp, "play_favorite", lambda fav: None)
    monkeypatch.setattr(mp, "_play", lambda q, url="", player=None: played.append((q, url)) or "Играет.")
    monkeypatch.setattr(web_find, "search", lambda *a, **k: (_ for _ in ()).throw(AssertionError("искал в интернете")))
    assert mp.music_player({"action": "play", "query": "хорошую музыку"}) == "Играет."
    assert played == [("", mp.favorite_playlist())]


def test_spotify_один_поиск_трек_первым(monkeypatch):
    calls = []

    def fake(q, limit=8, **kw):
        calls.append(kw.get("backend"))
        return [Hit("a", "https://open.spotify.com/artist/AAAAAAAAAAAAAAAAAAAAAA"),
                Hit("t", "https://open.spotify.com/track/BBBBBBBBBBBBBBBBBBBBBB")]
    monkeypatch.setattr(web_find, "search", fake)
    assert web_find.spotify_uri("x", "track") == "spotify:track:BBBBBBBBBBBBBBBBBBBBBB"
    assert calls == [web_find.SPOTIFY_BACKEND]


def test_трек_без_spotify_api_играет_на_youtube(monkeypatch):
    opened = []
    monkeypatch.setattr(mp, "_is_spotify_installed", lambda: True)
    monkeypatch.setattr(mp, "_spotify_search_track_uri", lambda q: None)
    monkeypatch.setattr(mp, "_find_youtube_direct_url", lambda q: "https://www.youtube.com/watch?v=MXUOec-mRSk&autoplay=1")
    monkeypatch.setattr(mp, "_spotify_now", lambda: None)
    monkeypatch.setattr(mp, "browser_control", lambda p, player=None: opened.append(p["url"]) or "ok")
    assert mp._play("Mekhman Растопи") == "Включил «Mekhman Растопи» на YouTube."
    assert opened == ["https://www.youtube.com/watch?v=MXUOec-mRSk&autoplay=1"]
