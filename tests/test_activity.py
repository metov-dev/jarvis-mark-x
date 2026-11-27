"""TASK-21: Джарвис сам знает, что владелец смотрит, даже если включил не он."""
from types import SimpleNamespace as NS

from core import activity as A


def _media(title, app="Yandex.Browser", artist="", playing=True):
    return NS(app=app, title=title, artist=artist, playing=playing)


def test_manual_series_becomes_known_after_two_minutes(tmp_path):
    now = [1000.0]
    tr = A.ActivityTracker(tmp_path / "a.json", now=lambda: now[0])
    act = A.from_media(_media("Игра престолов 1 сезон 4 серия смотреть онлайн — Kinogo"))
    assert act.kind == "video" and act.title.startswith("Игра престолов") and (act.season, act.episode) == (1, 4)
    assert tr.observe(act) is None                       # только начал — может быть трейлер
    now[0] += 130
    became = tr.observe(A.from_media(_media("Игра престолов 1 сезон 4 серия смотреть онлайн — Kinogo")))
    assert became and became.episode == 4
    assert "смотрит «Игра престолов" in tr.describe() and "серия 4" in tr.describe()
    # переживает перезапуск
    again = A.ActivityTracker(tmp_path / "a.json", now=lambda: now[0] + 600)
    assert "Недавно: «Игра престолов" in again.describe()


def test_music_is_listening_and_trailer_is_not_remembered(tmp_path):
    now = [0.0]
    tr = A.ActivityTracker(tmp_path / "a.json", now=lambda: now[0])
    tr.observe(A.from_media(_media("Трейлер", app="chrome")))
    now[0] += 30
    tr.observe(None)                                      # трейлер кончился
    assert tr.recent == [] and tr.describe() == ""
    song = A.from_media(_media("Люби меня", app="Spotify.exe", artist="MiyaGi"))
    assert song.kind == "music"
    tr.observe(song)
    assert "слушает «Люби меня» — MiyaGi" in tr.describe()


def test_paused_media_is_ignored():
    assert A.from_media(_media("Фильм", playing=False)) is None
    assert A.from_media(None) is None


def test_jarvis_observer_feeds_tracker(monkeypatch, tmp_path):
    import main as jarvis_main
    from core import media_session
    j = jarvis_main.Jarvis.__new__(jarvis_main.Jarvis)
    now = [0.0]
    j._activity = A.ActivityTracker(tmp_path / "a.json", now=lambda: now[0])
    monkeypatch.setattr(media_session, "playing_sessions",
                        lambda: [_media("Jarvis voice", app="JARVIS.exe"), _media("Менталист 3 сезон 5 серия")])
    j._observe_activity({"window": "Менталист 3 сезон 5 серия — Яндекс Браузер"})
    now[0] += 200
    j._observe_activity({"window": "Менталист 3 сезон 5 серия — Яндекс Браузер"})
    assert "Менталист" in j._activity.describe() and "сезон 3, серия 5" in j._activity.describe()
