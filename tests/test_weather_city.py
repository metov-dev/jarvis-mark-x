"""Погода: город с падежом и ослышкой, русские описания неба."""
import io
import json

import pytest

from actions import weather


@pytest.mark.parametrize("spoken, first", [
    ("Шимкенте", "Шымкент"),
    ("шемкинте", "Шымкент"),
    ("Чимкент", "Шымкент"),
    ("Алмате", "Алматы"),
])
def test_alias_first(spoken, first):
    assert weather.city_candidates(spoken)[0] == first


@pytest.mark.parametrize("spoken, expected", [
    ("Москве", "Москва"),
    ("Астане", "Астана"),
    ("Ташкенте", "Ташкент"),
    ("Бишкеке", "Бишкек"),
])
def test_prepositional_case_is_removed(spoken, expected):
    assert expected in weather.city_candidates(spoken)


def test_nominative_stays_and_empty_is_empty():
    assert weather.city_candidates("Шымкент")[0] == "Шымкент"
    assert weather.city_candidates("  ") == []


@pytest.mark.parametrize("desc_en, expected", [
    ("Clear ", "Ясно"),
    ("Partly Cloudy ", "Переменная облачность"),
    ("partly cloudy", "Переменная облачность"),
    ("Light rain shower", "Лёгкий ливень"),
])
def test_description_is_russian(desc_en, expected):
    assert weather.describe(desc_en) == expected


def test_unknown_description_is_kept():
    assert weather.describe("Some Weird Thing") == "Some Weird Thing"


def _wttr_body():
    day = {"maxtempC": "20", "mintempC": "10",
           "hourly": [{"weatherCode": "113", "chanceofrain": "0",
                       "weatherDesc": [{"value": "Sunny "}]}] * 8}
    return json.dumps({
        "current_condition": [{"temp_C": "14", "FeelsLikeC": "11", "humidity": "36",
                               "windspeedKmph": "4", "weatherCode": "113",
                               "weatherDesc": [{"value": "Clear "}]}],
        "weather": [day, day, day],
    }).encode()


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_misheard_city_gets_weather_for_real_city(monkeypatch):
    asked = []

    def fake_urlopen(url, timeout=0):
        asked.append(url)
        return _Resp(_wttr_body())

    monkeypatch.setattr(weather.urllib.request, "urlopen", fake_urlopen)
    weather._WEATHER_CACHE.clear()
    text = weather.weather_action({"city": "Шимкенте"})
    assert "Shymkent" in asked[0]
    assert "Шымкент" in text and "Ясно" in text and "Clear" not in text


def test_unknown_city_tries_next_candidate_then_reports_city(monkeypatch):
    calls = []

    def fake_urlopen(url, timeout=0):
        calls.append(url)
        if "%D0%90%D0%B1%D0%B2" in url or len(calls) == 1:
            raise OSError("нет такого")
        return _Resp(_wttr_body())

    monkeypatch.setattr(weather.urllib.request, "urlopen", fake_urlopen)
    weather._WEATHER_CACHE.clear()
    text = weather.weather_action({"city": "Курагане"})
    assert len(calls) >= 2
    assert "Температура 14°C" in text


def test_all_failed_names_the_city(monkeypatch):
    def boom(url, timeout=0):
        raise OSError("сеть")

    monkeypatch.setattr(weather.urllib.request, "urlopen", boom)
    weather._WEATHER_CACHE.clear()
    text = weather.weather_action({"city": "Шимкенте"})
    assert "Шимкенте" in text or "Шымкент" in text
