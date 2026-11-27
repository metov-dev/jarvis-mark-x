"""Проверка анкеты «Обо мне»: что вписано наугад или противоречит тому, что Джарвис знает сам.

Владелец 28.11: «я написал всё наугад и по приколу — он что, не проверит и не исправит?»
Раньше Джарвис верил анкете слепо. Теперь он сверяет каждое поле:
  • с правдоподобием (дата рождения — реальный возраст, время — «7:30», а не «25:99»);
  • с тем, что знает сам (город — геокодер; клуб — список клубов; язык — список языков);
  • с фактами, которые запомнил в разговорах (тот же ключ — другое значение);
  • на «мусор» (набор букв, «хз», «по приколу», один символ).
Сам ничего не меняет: отдаёт список вопросов — Gemini спрашивает владельца по одному.
"""
from __future__ import annotations

import re
from datetime import date

_JOKE = re.compile(r"^(?:хз|х\.з|не знаю|нет|ничего|прикол\w*|по приколу|лол|ахах\w*|хаха\w*|"
                   r"asd\w*|qwe\w*|фыв\w*|йцу\w*|test|тест|\.+|-+|\?+|\d{1,2})$", re.IGNORECASE)
_LANGS = {"русский", "казахский", "узбекский", "английский", "турецкий", "немецкий", "французский",
          "испанский", "китайский", "корейский", "японский", "арабский", "киргизский", "кыргызский",
          "таджикский", "туркменский", "украинский", "татарский", "итальянский", "russian", "english",
          "kazakh", "uzbek", "turkish", "german", "french"}
_MONTHS = {"январ": 1, "феврал": 2, "март": 3, "апрел": 4, "ма": 5, "июн": 6, "июл": 7, "август": 8,
           "сентябр": 9, "октябр": 10, "ноябр": 11, "декабр": 12}


# Ключи фактов из разговоров, которые соответствуют полю анкеты.
FACT_ALIASES = {"name": ("имя", "name"), "city": ("город", "city"), "birthday": ("день рождения", "birthday")}


def _gibberish(text: str) -> bool:
    """Набор букв: нет гласных, или одна буква повторяется, или клавиатурный ряд."""
    t = re.sub(r"[^a-zа-яё]", "", text.casefold())
    if not t and re.search(r"\d{1,2}[:.]\d{2}", text):
        return False                      # «9:00-14:00» — время, а не мусор
    if len(t) < 2:
        return True
    if not re.search(r"[aeiouyаеёиоуыэюя]", t):
        return True
    if len(set(t)) <= 2 and len(t) >= 4:
        return True
    return bool(_JOKE.match(text.strip().casefold()))


def _parse_birthday(text: str) -> date | None:
    t = text.casefold()
    m = re.search(r"(\d{1,2})[./-](\d{1,2})[./-](\d{4})", t)
    if m:
        d, mo, y = map(int, m.groups())
    else:
        m = re.search(r"(\d{1,2})\s+([а-я]+)\s+(\d{4})", t)
        if not m:
            return None
        d, y = int(m.group(1)), int(m.group(3))
        mo = next((n for stem, n in _MONTHS.items() if m.group(2).startswith(stem)), 0)
    try:
        return date(y, mo, d)
    except ValueError:
        return None


def _time_ok(text: str) -> bool:
    m = re.search(r"(\d{1,2})(?:[:.](\d{2}))?", text)
    return bool(m) and 0 <= int(m.group(1)) <= 23 and 0 <= int(m.group(2) or 0) <= 59


def check_field(key: str, value: str, today: date | None = None, geocode=None, clubs=None) -> str:
    """Что не так с одним полем — вопрос для владельца, или ''."""
    v = (value or "").strip()
    if not v:
        return ""
    if key == "birthday":
        born = _parse_birthday(v)
        if not born:
            return f"день рождения «{v}» — не похоже на дату"
        age = ((today or date.today()) - born).days // 365
        return "" if 5 <= age <= 100 else f"день рождения «{v}» — тогда вам {age} лет, верно?"
    if key in ("wake_time", "sleep_time"):
        return "" if _time_ok(v) else f"«{v}» — не похоже на время"
    if key == "languages":
        words = [w for w in re.split(r"[,;\s]+(?:и\s+)?", v.casefold()) if w and w != "и"]
        unknown = [w for w in words if w not in _LANGS]
        return f"языки: не знаю «{', '.join(unknown)}»" if unknown and len(unknown) == len(words) else ""
    if key == "city" and geocode is not None:
        if _gibberish(v):
            return f"город «{v}» — похоже на набор букв"
        place = geocode(v)
        return "" if place else f"город «{v}» — такого города не нашёл"
    if key == "club" and clubs is not None:
        low = v.casefold()
        if low in clubs or any(low in (a.casefold(), b.casefold()) for a, b in clubs.values()):
            return ""
        if re.search(r"ни за как|никак|не болею|нет", low):
            return ""
        return f"клуб «{v}» — такого не знаю"
    if key in ("name", "address_as"):
        return f"«{v}» — похоже на набор букв" if _gibberish(v) or len(v) > 40 else ""
    return f"«{v}» — похоже на шутку или набор букв" if _gibberish(v) else ""


def review(answers: dict[str, str], facts: list[tuple[str, str, str]], labels: dict[str, str],
           today: date | None = None, geocode=None, clubs=None, tg_name: str = "") -> list[str]:
    """Список вопросов к владельцу по анкете."""
    issues = []
    for key, value in answers.items():
        problem = check_field(key, value, today, geocode, clubs)
        if problem:
            issues.append(f"{labels.get(key, key)} (key={key}): {problem}")
    # Противоречия с тем, что Джарвис запомнил в разговорах.
    for key, names in FACT_ALIASES.items():
        mine = (answers.get(key) or "").strip().casefold()
        for _cat, fkey, fval in facts:
            if fkey.casefold() in names and mine and str(fval).strip().casefold() not in ("", mine):
                issues.append(f"{labels.get(key, key)} (key={key}): в анкете «{answers[key]}», "
                              f"а в разговоре вы говорили «{fval}» — что верно?")
                break
    from core.contacts import _cyr
    name = _cyr((answers.get("name") or "").strip().casefold())
    tg = _cyr(tg_name.strip().casefold())
    if tg and name and tg.split()[0][:4] not in name and name[:4] not in tg:
        name = (answers.get("name") or "").strip()
        issues.append(f"{labels.get('name', 'Имя')} (key=name): в анкете «{name}», а в Telegram — «{tg_name}»")
    return issues
