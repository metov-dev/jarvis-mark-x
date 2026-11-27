import pytest

from core import quick


@pytest.mark.parametrize("year_suffix", ["", " года выпуска", " года выпуск", " года выхода", " год, выпуск"])
def test_film_request_never_reaches_music_search(monkeypatch, year_suffix):
    monkeypatch.setattr(quick, "enabled", lambda: True)
    phrase = f"поставь фильм бэтмен начало 2005{year_suffix}"
    for offline in (True, False):
        q = quick.match(phrase, offline=offline)
        assert q.tool == "movie_player"
        assert q.args == {"action": "play", "provider": "kinogo", "title": "бэтмен начало 2005"}


def test_kinogo_correction_resolves_pronoun_without_youtube(monkeypatch):
    monkeypatch.setattr(quick, "enabled", lambda: True)
    phrase = "джарвис поставь его через киного сайт а не ютуб"
    last = {"action": "play", "provider": "youtube", "title": "Бэтмен: Начало 2005"}
    q = quick.match(phrase, last_movie=last)
    assert q.tool == "movie_player"
    assert q.args == {**last, "provider": "kinogo"}
    assert not quick.youtube_requested(phrase)
    assert quick.match(phrase).args["title"] == ""


@pytest.mark.parametrize(("text", "provider"), [
    ("Джарвис, поставь фильм Бэтмен на YouTube", "youtube"),
    ("поставь фильм Бэтмен не на youtube", "kinogo"),
    ("поставь фильм Бэтмен через киного а не на youtube", "kinogo"),
    ("поставь Бэтмен на киного", "kinogo"),
])
def test_explicit_platform_request(text, provider):
    request = quick.movie_request(text)
    assert request["provider"] == provider
    assert request["title"] == "бэтмен"


def test_russian_season_and_episode_survive_selection():
    assert quick.movie_request("поставь сериал Менталист 3сезон и 5ая серия в киного") == {
        "title": "менталист", "season": 3, "episode": 5, "provider": "kinogo", "action": "play",
    }


def test_only_explicit_youtube_and_no_accidental_movie():
    assert quick.youtube_requested("Джарвис, поставь ютуб Бэтмен")
    assert not quick.youtube_requested("Джарвис, поставь Бэтмен, а не на ютубе")
    assert quick.movie_request("поставь песню Бэтмен") is None
    assert quick.movie_request("расскажи про фильм Бэтмен") is None



def test_series_numbers_can_be_spoken():
    assert quick.movie_request("Поставь сериал Менталист третий сезон пятая серия") == {
        "title": "менталист", "action": "play", "provider": "kinogo", "season": 3, "episode": 5,
    }
    assert quick.movie_request("Поставь седьмую серию") == {
        "action": "series_episode", "season": None, "episode": 7,
    }
