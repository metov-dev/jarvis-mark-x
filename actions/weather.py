"""
Действие: погода через wttr.in (без API-ключа)
"""

import json
import logging
import time
import urllib.parse
import urllib.request

logger = logging.getLogger(__name__)


# Ослышки распознавания и падежи → именительный падеж, который знает _CITY_ALIASES.
_MISHEARD = {
    "питер": "Санкт-Петербург",
    "алма-ата": "Алматы",
    "алмата": "Алматы",
    "алмате": "Алматы",
    "нур-султан": "Астана",
    "чимкент": "Шымкент",
    "шемкент": "Шымкент",
    "шемкинт": "Шымкент",
    "шимкент": "Шымкент",
    "шимкинт": "Шымкент",
    "шымкинт": "Шымкент",
    "чимкинт": "Шымкент",
}

_CITY_ALIASES = {
    "москва": "Moscow",
    "питер": "Saint Petersburg",
    "санкт-петербург": "Saint Petersburg",
    "алматы": "Almaty",
    "алма-ата": "Almaty",
    "астана": "Astana",
    "нур-султан": "Astana",
    "кызылорда": "Kyzylorda",
    "шымкент": "Shymkent",
    "чимкент": "Shymkent",
    "ташкент": "Tashkent",
    "бишкек": "Bishkek",
    "минск": "Minsk",
    "киев": "Kyiv",
    "баку": "Baku",
    "тбилиси": "Tbilisi",
    "дубай": "Dubai",
}

_DESC_RU = {
    "Light rain shower": "Лёгкий ливень",
    "Moderate or heavy rain shower": "Сильный ливень",
    "Patchy light rain": "Местами лёгкий дождь",
    "Light drizzle": "Морось",
    "Patchy light drizzle": "Местами морось",
    "Patchy snow possible": "Местами снег",
    "Patchy light snow": "Местами лёгкий снег",
    "Light sleet": "Дождь со снегом",
    "Freezing fog": "Ледяной туман",
    "Haze": "Дымка",
    "Moderate or heavy rain with thunder": "Гроза с дождём",
    "Patchy light rain with thunder": "Гроза",
    "Sunny": "Солнечно",
    "Clear": "Ясно",
    "Partly cloudy": "Переменная облачность",
    "Cloudy": "Облачно",
    "Overcast": "Пасмурно",
    "Mist": "Туман",
    "Fog": "Густой туман",
    "Light rain": "Лёгкий дождь",
    "Moderate rain": "Умеренный дождь",
    "Heavy rain": "Сильный дождь",
    "Light snow": "Лёгкий снег",
    "Moderate snow": "Умеренный снег",
    "Heavy snow": "Сильный снег",
    "Thundery outbreaks possible": "Возможна гроза",
    "Blizzard": "Метель",
    "Patchy rain possible": "Местами дождь",
}


_DESC_RU_FOLDED = {k.casefold(): v for k, v in _DESC_RU.items()}


def describe(desc_en: str) -> str:
    """Описание неба по-русски; wttr.in присылает хвостовые пробелы и разный регистр."""
    clean = (desc_en or "").strip()
    return _DESC_RU_FOLDED.get(clean.casefold(), clean)


def city_candidates(spoken: str) -> list[str]:
    """Именительный падеж для сказанного «в Шымкенте», с учётом ослышек.

    Погодный сервис понимает только имя города, а не «Шымкенте» или «Шимкент».
    Порядок результата = порядок проб.
    """
    raw = (spoken or "").strip()
    if not raw:
        return []
    low = raw.lower()
    if low in _MISHEARD:
        return [_MISHEARD[low]]
    out: list[str] = [raw]
    if low.endswith("е"):
        out += [raw[:-1], raw[:-1] + "а"]
    elif low.endswith("и"):
        out += [raw[:-1], raw[:-1] + "ь", raw[:-1] + "я"]
    elif low.endswith(("у", "ю")):
        out.append(raw[:-1] + "а")
    resolved: list[str] = []
    for cand in out:
        cand = _MISHEARD.get(cand.lower(), cand)      # «шемкинте» → «шемкинт» → Шымкент
        if cand.lower() not in (c.lower() for c in resolved):
            resolved.append(cand)
    # то, что распознали по алиасу, пробуем первым
    resolved.sort(key=lambda c: c.lower() not in _CITY_ALIASES)
    return resolved


# Коды погоды WWO (их отдаёт wttr.in) → значок карточки.
_KIND_BY_CODE = {
    113: "sun", 116: "partly",
    119: "cloud", 122: "cloud", 143: "fog", 248: "fog", 260: "fog",
    200: "storm", 386: "storm", 389: "storm", 392: "storm", 395: "storm",
}
_RAIN = {176, 263, 266, 281, 284, 293, 296, 299, 302, 305, 308, 311, 314, 353, 356, 359}
_SNOW = {179, 182, 185, 227, 230, 317, 320, 323, 326, 329, 332, 335, 338, 350,
         362, 365, 368, 371, 374, 377}
_DAY_NAMES = ("Сегодня", "Завтра", "Послезавтра")


def weather_kind(code) -> str:
    try:
        code = int(code)
    except (TypeError, ValueError):
        return "cloud"
    if code in _RAIN:
        return "rain"
    if code in _SNOW:
        return "snow"
    return _KIND_BY_CODE.get(code, "cloud")


# Последний прогноз по городу — для карточки рядом с шаром.
last_forecast: dict[str, dict] = {}

_WEATHER_CACHE: dict[str, tuple[float, str]] = {}
_CACHE_TTL_SEC = 300  # 5 минут


_CITY_COORDS = {
    "moscow": (55.7558, 37.6173),
    "saint petersburg": (59.9343, 30.3351),
    "almaty": (43.2389, 76.8897),
    "astana": (51.1694, 71.4491),
    "shymkent": (42.3417, 69.5901),
    "tashkent": (41.2995, 69.2401),
    "bishkek": (42.8746, 74.5698),
    "kyzylorda": (44.8488, 65.4999),
    "minsk": (53.9006, 27.5590),
    "kyiv": (50.4501, 30.5234),
    "baku": (40.4093, 49.8671),
    "tbilisi": (41.7151, 44.8271),
    "dubai": (25.2048, 55.2708),
}

_WMO_MAP = {
    0: (113, "Clear"),
    1: (116, "Partly cloudy"),
    2: (116, "Partly cloudy"),
    3: (122, "Overcast"),
    45: (248, "Fog"),
    48: (260, "Freezing fog"),
    51: (266, "Light drizzle"),
    53: (266, "Light drizzle"),
    55: (266, "Light drizzle"),
    61: (296, "Light rain"),
    63: (302, "Moderate rain"),
    65: (308, "Heavy rain"),
    71: (326, "Light snow"),
    73: (332, "Moderate snow"),
    75: (338, "Heavy snow"),
    80: (353, "Light rain shower"),
    81: (356, "Moderate or heavy rain shower"),
    82: (356, "Moderate or heavy rain shower"),
    85: (368, "Light snow"),
    86: (371, "Heavy snow"),
    95: (389, "Moderate or heavy rain with thunder"),
    96: (392, "Moderate or heavy rain with thunder"),
    99: (395, "Moderate or heavy rain with thunder"),
}


def _fetch_open_meteo(city_en: str) -> dict:
    key = city_en.lower()
    coords = _CITY_COORDS.get(key)
    if not coords:
        geo_url = f"https://geocoding-api.open-meteo.com/v1/search?name={urllib.parse.quote(city_en)}&count=1"
        with urllib.request.urlopen(geo_url, timeout=3) as resp:
            geo_data = json.loads(resp.read().decode())
        results = geo_data.get("results")
        if not results:
            raise ValueError(f"Open-Meteo: координаты для {city_en} не найдены")
        coords = (results[0]["latitude"], results[0]["longitude"])

    lat, lon = coords
    url = (
        f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}"
        "&current=temperature_2m,relative_humidity_2m,apparent_temperature,weather_code,wind_speed_10m"
        "&daily=weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max&timezone=auto"
    )
    with urllib.request.urlopen(url, timeout=4) as resp:
        om = json.loads(resp.read().decode())

    curr = om.get("current", {})
    code, desc = _WMO_MAP.get(curr.get("weather_code", 0), (113, "Clear"))
    daily = om.get("daily", {})
    days_data = []
    times = daily.get("time", [])
    for i in range(min(3, len(times))):
        d_code_val = (daily.get("weather_code") or [0])[i]
        d_code, d_desc = _WMO_MAP.get(d_code_val, (113, "Clear"))
        max_t = str(int(round((daily.get("temperature_2m_max") or [0])[i])))
        min_t = str(int(round((daily.get("temperature_2m_min") or [0])[i])))
        rain_p = str(int(round((daily.get("precipitation_probability_max") or [0])[i])))
        days_data.append({
            "maxtempC": max_t,
            "mintempC": min_t,
            "hourly": [{
                "weatherDesc": [{"value": d_desc}],
                "chanceofrain": rain_p,
                "weatherCode": str(d_code),
            }]
        })

    return {
        "current_condition": [{
            "temp_C": str(int(round(curr.get("temperature_2m", 0)))),
            "FeelsLikeC": str(int(round(curr.get("apparent_temperature", 0)))),
            "weatherDesc": [{"value": desc}],
            "humidity": str(int(round(curr.get("relative_humidity_2m", 0)))),
            "windspeedKmph": str(int(round(curr.get("wind_speed_10m", 0)))),
            "weatherCode": str(code),
        }],
        "weather": days_data,
    }


def _fetch_wttr(city_en: str) -> dict:
    url = f"https://wttr.in/{urllib.parse.quote(city_en)}?format=j1"
    try:
        with urllib.request.urlopen(url, timeout=4) as resp:
            return json.loads(resp.read().decode())
    except Exception as exc:
        logger.debug("wttr.in сбой (%s) — переключаюсь на Open-Meteo", exc)
        return _fetch_open_meteo(city_en)


def weather_action(parameters: dict, player=None) -> str:
    city = (parameters.get("city") or "").strip()
    if not city:
        # «Какая погода?» без города — там, где пользователь (core/location.py).
        try:
            from core import location
            city = location.city()
        except Exception as exc:
            logger.debug("Местоположение для погоды: %s", exc)
    if not city:
        return "Для какого города, сэр? Не удалось определить, где вы."
    spoken_city = city
    candidates = city_candidates(city)
    city = candidates[0]
    city_en = _CITY_ALIASES.get(city.lower(), city)
    now = time.monotonic()

    # Проверка TTL-кэша
    cache_key = city_en.lower()
    if cache_key in _WEATHER_CACHE:
        cached_time, cached_result = _WEATHER_CACHE[cache_key]
        if now - cached_time < _CACHE_TTL_SEC:
            if player:
                player.write_log(f"SYS: Погода (кэш) — {city}")
            return cached_result

    try:
        data = None
        for cand in candidates:
            try:
                data = _fetch_wttr(_CITY_ALIASES.get(cand.lower(), cand))
            except Exception as exc:
                logger.debug("Погода для «%s» не получена: %s", cand, exc)
                continue
            city = cand
            break
        if data is None:
            raise RuntimeError("ни один вариант города не ответил")

        current = data["current_condition"][0]
        temp_c = current["temp_C"]
        feels = current["FeelsLikeC"]
        desc_en = current["weatherDesc"][0]["value"]
        humidity = current["humidity"]
        wind = current["windspeedKmph"]

        desc_ru = describe(desc_en)

        # Прогноз на три дня. Раньше бралась только погода «сейчас» — на
        # «погода на завтра» Джарвис отвечал про сегодня.
        days = []
        for i, day in enumerate(data.get("weather", [])[:3]):
            hourly = day.get("hourly") or [{}]
            noon = hourly[min(4, len(hourly) - 1)]            # 12:00
            d_en = (noon.get("weatherDesc") or [{}])[0].get("value", "")
            rain = max((int(h.get("chanceofrain", 0) or 0) for h in hourly), default=0)
            days.append({
                "name": _DAY_NAMES[i],
                "max": day.get("maxtempC", "?"), "min": day.get("mintempC", "?"),
                "desc": describe(d_en),
                "rain": rain, "kind": weather_kind(noon.get("weatherCode")),
            })

        result = (
            f"Погода, {city}: {desc_ru}. "
            f"Температура {temp_c}°C, ощущается как {feels}°C. "
            f"Влажность {humidity}%, ветер {wind} км/ч."
        )
        if days:
            parts = []
            for d in days:
                line = f"{d['name']}: {d['desc'].lower() or 'без описания'}, от {d['min']} до {d['max']}°C"
                if d["rain"] >= 30:
                    line += f", вероятность дождя {d['rain']}%"
                parts.append(line + ".")
            result += " Прогноз: " + " ".join(parts)

        last_forecast[city.lower()] = {
            "city": city, "temp": temp_c, "feels": feels, "desc": desc_ru,
            "humidity": humidity, "wind": wind,
            "kind": weather_kind(current.get("weatherCode")), "days": days,
        }

        _WEATHER_CACHE[cache_key] = (now, result)

        if player:
            player.write_log(f"SYS: Погода — {city}: {temp_c}°C, {desc_ru}")
        return result

    except Exception as exc:
        logger.warning("Погода для «%s» недоступна: %s", spoken_city, exc)
        return (f"Не удалось получить погоду для {spoken_city}: город не найден "
                "или нет связи с погодным сервисом.")
