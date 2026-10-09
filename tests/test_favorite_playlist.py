"""«Мой / любимый плейлист» — свой плейлист владельца, а не поиск чужих в Spotify."""
import pytest

from actions import music_player as mp


@pytest.mark.parametrize("query", [
    "мой плейлист", "из моего плейлиста", "любимого плейлиста", "любимый плейлист",
    "мои лайки", "любимые песни", "мою музыку", "понравившиеся", "лайкнутые треки",
    "мой любимый плейлист", "избранное",
])
def test_favorite_phrases(query):
    assert mp.is_favorite(query)


@pytest.mark.parametrize("query", [
    "мой плейлист americans", "скриптонит", "любимые песни мамы", "believer", "", "музыку",
])
def test_not_favorite(query):
    assert not mp.is_favorite(query)


def test_saved_playlist_url_wins(monkeypatch):
    monkeypatch.setattr(mp, "load_api_keys",
                        lambda: {"spotify_favorite_playlist": "https://open.spotify.com/playlist/37i9dQZF1F5p3rmiWPIYgZ"})
    assert mp.favorite_playlist() == "spotify:playlist:37i9dQZF1F5p3rmiWPIYgZ"


def test_without_saved_playlist_liked_songs(monkeypatch):
    monkeypatch.setattr(mp, "load_api_keys", lambda: {})
    assert mp.favorite_playlist() == mp.LIKED_SONGS


def test_favorite_request_does_not_search_strangers(monkeypatch):
    calls = {}
    monkeypatch.setattr(mp, "favorite_playlist", lambda: "spotify:playlist:ABC")

    import actions.spotify_premium as sp
    monkeypatch.setattr(sp, "play", lambda q: calls.setdefault("search", q))
    monkeypatch.setattr(sp, "play_favorite", lambda uri: calls.setdefault("fav", uri) and "ok")
    out = mp.music_player({"action": "play", "query": "любимого плейлиста"})
    assert calls == {"fav": "spotify:playlist:ABC"} and out == "ok"


def test_favorite_falls_back_to_desktop_app(monkeypatch):
    seen = {}
    monkeypatch.setattr(mp, "favorite_playlist", lambda: "spotify:playlist:ABC")
    import actions.spotify_premium as sp
    monkeypatch.setattr(sp, "play_favorite", lambda uri: None)
    monkeypatch.setattr(mp, "_play", lambda q, url, player=None: seen.update(q=q, url=url) or "desk")
    assert mp.music_player({"action": "play", "query": "мой плейлист"}) == "desk"
    assert seen == {"q": "", "url": "spotify:playlist:ABC"}
