"""Мгновенные ответы: частые команды без раздумий облака.

Раньше каждое «пауза» шло полным кругом: Gemini дослушивает, думает, зовёт
инструмент, получает ответ, пишет фразу, Fish её синтезирует — 2–4 секунды на
то, что человек делает одной кнопкой. Теперь частые команды («пауза»,
«громче», «следующий трек», «который час», «таймер на 5 минут», «спасибо»)
узнаются прямо по расшифровке речи и выполняются на ПК сразу. Получилось —
короткий звук «готово» и галочка в капсуле, без слов (как у Siri): голос
только тратил время. Голосом — когда не вышло или нужен ответ («который
час»), из заранее озвученных фраз — без сети и без ожидания.

- Узнаётся только фраза ЦЕЛИКОМ («громче» — да; «громче и открой хром» — нет,
  это уйдёт в Gemini как обычно): лучше отдать облаку, чем сделать не то.
- Команда не вышла («Spotify не запущен») — Джарвис говорит, что ответил
  инструмент, а не «Готово».
- Ответ, где нужны данные («который час», «что играет»), — это ответ самого
  инструмента, озвученный сразу, без круга через модель.
- Готовые фразы синтезируются один раз (voice_cache/) и дальше звучат
  мгновенно; у каждой команды несколько вариантов, чтобы не звучать роботом.
JARVIS_QUICK=0 — выключить.
"""
from __future__ import annotations

import hashlib
import logging
import os
import random
import re
from dataclasses import dataclass, field
from pathlib import Path

from core.wake_vosk import WAKE_RE

logger = logging.getLogger(__name__)

# Сколько тишины в расшифровке считать концом фразы (если ответ модели ещё
# не начался — он начинается, когда её VAD решил, что человек договорил).
QUIET_SEC = 0.45


def enabled() -> bool:
    return os.getenv("JARVIS_QUICK", "1").strip() != "0"


# ── Готовые фразы ─────────────────────────────────────────────────────────────
ACK = ("Есть, сэр.", "Сделано, сэр.", "Готово, сэр.")
PHRASES: dict[str, tuple[str, ...]] = {
    "ack": ACK,
    "pause": ("Пауза, сэр.", "Поставил на паузу, сэр."),
    "resume": ("Продолжаю, сэр.", "Играет дальше, сэр."),
    "next": ("Следующий трек, сэр.", "Переключаю, сэр."),
    "previous": ("Возвращаю предыдущий, сэр.", "Предыдущий трек, сэр."),
    "louder": ("Громче, сэр.", "Прибавил, сэр."),
    "quieter": ("Тише, сэр.", "Убавил, сэр."),
    "mute": ("Звук выключен, сэр.",),
    "unmute": ("Звук включён, сэр.",),
    "fullscreen": ("На весь экран, сэр.", "Разворачиваю, сэр."),
    "exit_fullscreen": ("Обычный экран, сэр.",),
    "desktop": ("Сворачиваю всё, сэр.", "Рабочий стол, сэр."),
    "eyes_open": ("Смотрю, сэр.", "Вижу экран, сэр."),
    "thanks": ("Всегда пожалуйста, сэр.", "Рад помочь, сэр.", "К вашим услугам, сэр."),
    "here": ("Здесь, сэр.", "Слушаю, сэр.", "На месте, сэр."),
}

# Частые ответы самого Gemini: озвучены заранее — звучат сразу, без синтеза (~1 с).
COMMON = ("Секунду, сэр.", "Минуту, сэр.", "Сейчас, сэр.", "Включаю, сэр.", "Открываю, сэр.",
          "Закрываю, сэр.", "Выполняю, сэр.", "Уже делаю, сэр.", "Да, сэр.", "Нет, сэр.", "Хорошо, сэр.",
          "Понял, сэр.", "Конечно, сэр.", "Слушаюсь, сэр.", "Одну секунду, сэр.", "Смотрю, сэр.",
          "Ищу, сэр.", "Запомнил, сэр.", "Записал, сэр.", "Напомню, сэр.", "Не получилось, сэр.",
          "Доброе утро, сэр.", "Добрый день, сэр.", "Добрый вечер, сэр.", "Спокойной ночи, сэр.")


def all_phrases() -> list[str]:
    """Всё, что озвучивается заранее: ответы мгновенных команд и частые ответы Gemini."""
    out = [p for variants in PHRASES.values() for p in variants]
    return out + [p for p in COMMON if p not in out]


# ── Разбор фразы ──────────────────────────────────────────────────────────────
@dataclass
class Quick:
    tool: str | None                 # None — ответ без действия («спасибо»)
    args: dict = field(default_factory=dict)
    reply: str = ""                  # ключ PHRASES; "" — озвучить ответ инструмента
    text: str = ""                   # что узнали (нормализованное)

    def phrase(self) -> str:
        return random.choice(PHRASES[self.reply]) if self.reply else ""


_FILLER = re.compile(r"\b(пожалуйста|сэр|ну|а|давай|слушай|эй|окей|ок|быстро|плиз)\b")


def normalize(text: str) -> str:
    t = (text or "").lower().replace("ё", "е")
    t = re.sub(r"[^\w\s:%]", " ", t)
    t = " ".join(w for w in t.split() if not WAKE_RE.search(w))   # имя — целым словом
    t = _FILLER.sub(" ", t)
    return re.sub(r"\s+", " ", t).strip()


def _level(v: str) -> str | None:
    from actions.computer_settings import parse_level
    return v if parse_level(v) is not None else None


def _duration(d: str) -> str | None:
    from core.clock import parse_duration
    return d if parse_duration({"duration": d}) else None


_MUSIC = r"(музык[аиуе]|spotify|спотифа[йя]|песн[июя]|трек[а]?)"
_VIDEO = r"(фильм[аe]?|видео|ролик[а]?)"
_NUM = r"(?P<v>(на |до )?[\w% ]{1,30})"

# (шаблон всей фразы, инструмент, аргументы, ключ ответа).
# Аргумент-строка вида "@v" берётся из группы шаблона и проверяется.
_RULES: list[tuple[str, str | None, dict, str]] = [
    ('(?:включи|поставь|запусти|открой|найди|посмотри) (?:фильм |сериал )?(?P<title>\\w[\\w :\\-]{0,149}?) (?:на |в )(?:kinogo|киног[оа])', 'movie_player', {'action': 'play', 'provider': 'kinogo', 'title': '@title'}, ''),
    ('(?:поставь это на паузу|это на паузу|приостанови это|поставь на паузу то что играет)', 'video_control', {'action': 'pause', 'target': 'context'}, ''),
    ('на чем я остановил(?:ся|ась)|на чем мы остановились|над чем я работал(?:а)?', 'desktop_context', {'action': 'resume'}, ''),
    # Пауза и продолжение: video_control сам уходит в Spotify, если видео нет.
    # Голое «стоп» — не здесь: им перебивают самого Джарвиса.
    (r"(поставь )?(на )?паузу|пауза|(стоп|останови) (" + _MUSIC + "|" + _VIDEO + ")|"
     r"(поставь )?(музыку|видео|фильм) на паузу", "video_control", {"action": "pause"}, "pause"),
    (r"продолж(и|ай)( играть| " + _MUSIC + "| " + _VIDEO + ")?|сними с паузы|играй дальше|"
     r"сними (музыку|видео|фильм) с паузы", "video_control", {"action": "resume"}, "resume"),
    (r"(включи |поставь )?следующ(ий|ая|ую|ее)( трек| песн[юя])?|переключи( трек| песню)?|"
     r"другую песню|дальше трек", "music_player", {"action": "next"}, "next"),
    (r"(включи |поставь )?предыдущ(ий|ая|ую|ее)( трек| песн[юя])?|верни (прошлую|предыдущую)( песню)?|"
     r"прошлую песню|прошлый трек", "music_player", {"action": "previous"}, "previous"),
    (r"что (сейчас )?(играет|за песня|за трек|за музыка)|кто (это )?поет|как называется (песня|трек)",
     "music_player", {"action": "now_playing"}, ""),
    # Громкость: музыка → Spotify, фильм → видео, без уточнения → система.
    (r"(сделай )?" + _MUSIC + r" (по)?громче|(по)?громче " + _MUSIC,
     "music_player", {"action": "volume_up"}, "louder"),
    (r"(сделай )?" + _MUSIC + r" (по)?тише|(по)?тише " + _MUSIC,
     "music_player", {"action": "volume_down"}, "quieter"),
    (r"громкость " + _MUSIC + " " + _NUM + r"|" + _MUSIC + r" на (?P<v2>[\w% ]{1,30})",
     "music_player", {"action": "volume_set", "value": "@v"}, "ack"),
    (r"(сделай )?" + _VIDEO + r" (по)?громче|(по)?громче " + _VIDEO,
     "video_control", {"action": "volume_up"}, "louder"),
    (r"(сделай )?" + _VIDEO + r" (по)?тише|(по)?тише " + _VIDEO,
     "video_control", {"action": "volume_down"}, "quieter"),
    (r"громкость " + _VIDEO + " " + _NUM,
     "video_control", {"action": "volume_set", "value": "@v"}, "ack"),
    (r"(сделай )?(по)?громче|прибавь( звук| громкость)?|громче", "computer_control",
     {"action": "volume_up"}, "louder"),
    (r"(сделай )?(по)?тише|убавь( звук| громкость)?|тише", "computer_control",
     {"action": "volume_down"}, "quieter"),
    (r"(громкость|звук|поставь громкость|сделай громкость) " + _NUM, "computer_control",
     {"action": "volume_set", "value": "@v"}, "ack"),
    (r"выключи звук|без звука|отключи звук", "computer_control", {"action": "mute"}, "mute"),
    (r"включи звук|верни звук", "computer_control", {"action": "unmute"}, "unmute"),
    ('(сделай )?(экран (по)?ярче|(по)?ярче( экран)?)', 'computer_control', {'action': 'brightness_up'}, ''),
    ('(сделай )?(экран (по)?темнее|(по)?темнее( экран)?)', 'computer_control', {'action': 'brightness_down'}, ''),
    ('(поставь |сделай )?яркость (?P<v>(на |до )?[\\w% ]{1,30})', 'computer_control', {'action': 'brightness_set', 'value': '@v'}, ''),
    # Экран.
    (r"(разверни |сделай )?(" + _VIDEO + " )?(на )?(весь|полный) экран", "video_control",
     {"action": "fullscreen"}, "fullscreen"),
    (r"выйди из полного экрана|обычный экран|сверни (видео|фильм)", "video_control",
     {"action": "exit_fullscreen"}, "exit_fullscreen"),
    (r"сверни все( окна)?|покажи рабочий стол|рабочий стол", "window_control",
     {"action": "minimize_all"}, "desktop"),
    # Часы.
    (r"(который|сколько) (сейчас )?(час|времени)|сколько время|какое (сегодня )?число|"
     r"какой (сегодня )?день", "clock", {"action": "now"}, ""),
    (r"(поставь )?таймер на паузу|приостанови таймер", "clock",
     {"action": "timer_pause"}, ""),
    (r"(поставь |запусти |заведи |засеки )?таймер на (?P<d>.+)", "clock",
     {"action": "timer_set", "duration": "@d"}, ""),
    (r"(отмени|сбрось|удали|выключи|останови) таймер|таймер стоп|стоп таймер", "clock",
     {"action": "timer_cancel"}, ""),
    (r"возобнови таймер|сними таймер с паузы|продолжи таймер", "clock",
     {"action": "timer_resume"}, ""),
    (r"(добавь|прибавь) (к таймеру )?(?P<d>.+)( к таймеру)?", "clock",
     {"action": "timer_add", "duration": "@d"}, ""),
    (r"сколько (осталось|там) (на таймере|таймеру)", "clock", {"action": "timer_list"}, ""),
    (r"(запусти |включи |засеки )?секундомер", "clock", {"action": "stopwatch_start"}, ""),
    (r"(останови|стоп|выключи) секундомер|секундомер стоп", "clock", {"action": "stopwatch_stop"}, ""),
    (r"выключи будильник|я (встал|проснулся)|хватит звенеть", "clock", {"action": "alarm_stop"}, ""),
    (r"отложи( будильник)?( на (?P<m>\d+) минут[уы]?)?", "clock",
     {"action": "alarm_snooze", "minutes": "@m"}, ""),
    (r"(поставь |заведи |поставь мне )?будильник (на |в )(?P<t>[\d: ]{1,5}( утра| вечера| ночи| дня)?)|"
     r"разбуди( меня)? (на |в )(?P<t2>[\d: ]{1,5}( утра| вечера| ночи| дня)?)", "clock",
     {"action": "alarm_set", "when": "@t"}, ""),
    # Погода: короткий вопрос — сразу ответ, без раздумий облака.
    (r"(какая |что с )?(сейчас |сегодня )?погода( сейчас| сегодня| на улице)?", "weather", {}, ""),
    (r"(какая )?(сейчас |сегодня )?погода в (?P<city>[\w -]{2,30})", "weather", {"city": "@city"}, ""),
    # Глаза.
    (r"смотри на экран|будь моими глазами", "eyes", {"action": "open", "source": "screen"}, "eyes_open"),
    (r"закрой глаза|не смотри|хватит смотреть", "eyes", {"action": "close"}, ""),
    # Как у Alfred: «открой», «включи», «найди на ютубе» — сразу, без раздумий
    # облака и без слов. Только одна команда целиком: «открой хром и найди…»
    # (союз «и») уходит в Gemini. Не вышло — инструмент скажет почему.
    (r"(открой|запусти) (ютуб|youtube)", "browser", {"action": "go_to", "url": "youtube.com"}, ""),
    (r"(найди|включи|поставь|покажи) (на |в )?(ютубе|ютуб|youtube) (?P<yq>.+)", "youtube_player",
     {"action": "play", "query": "@yq"}, ""),
    (r"(поставь |включи )?последнее видео (?P<ch>.+)", "youtube_player",
     {"action": "latest", "channel": "@ch"}, ""),
    (r"(открой|запусти) (программу |приложение )?(?P<app>.+)", "open_app", {"app_name": "@app"}, ""),
    (r"(включи|поставь|сыграй|играй|запусти) (песню |трек |музыку |альбом )?(?P<song>.+)", "music_player",
     {"action": "play", "query": "@song"}, ""),
    # Вежливость.
    (r"спасибо( большое| тебе)?|благодарю|спс", None, {}, "thanks"),
    (r"ты (тут|здесь)|ты меня слышишь|ты на связи", None, {}, "here"),
]
_COMPILED = [(re.compile(p), tool, args, reply) for p, tool, args, reply in _RULES]
_NOT_APP = re.compile(r"\b(и|сайт|страниц\w*|ютуб\w*|youtube|гугл\w*|google|вк|вконтакте|окн\w*|глаза|"
                      r"камер\w*|папк\w*|файл\w*|ссылк\w*|новост\w*|почт\w*|видео|фильм\w*)\b")
_NOT_SONG = re.compile(r"^(музык\w*|что[- ]нибудь|видео|фильм\w*|клип\w*|ролик\w*|мульт\w*|сериал\w*|"
                       r"звук|свет|камер\w*|микрофон|режим\b.*|таймер.*|будильник.*|секундомер|компьютер|комп|"
                       r"вай[- ]?фай|wi-?fi|блютуз|bluetooth|экран.*|яркость.*|глаза|новост\w*|погод\w*|радио|"
                       r"ютуб.*|youtube.*|программ\w*.*|приложени\w*.*)$|\bи\b|"
                       r"\b(по)?(громче|тише)\b|\bпауз\w*|\bдальше\b|\bзаново\b|\bснова\b|"
                       # настройки и режимы Джарвиса — это не песни, пусть решает Gemini
                       r"\b(голос\w*|озвучк\w*|субтитр\w*|уведомлени\w*|напоминани\w*|перевод\w*|"
                       r"т[её]мн\w*|ночн\w*|фонарик|вентилятор|автозапуск|запис\w*|трансляци\w*|"
                       r"демонстраци\w*|стрим\w*|игр[уаы]|интернет|vpn|впн)\b")


def _app(v: str) -> str | None:
    v = v.strip()
    return v if 1 < len(v) <= 40 and not _NOT_APP.search(v) else None


def _song(v: str) -> str | None:
    v = v.strip()
    return v if 1 < len(v) <= 80 and not _NOT_SONG.search(v) else None


def _query(v: str) -> str | None:
    v = v.strip()
    return v if 1 < len(v) <= 100 and not re.search(r"\bи\b", v) else None


def _alarm_time(v: str) -> str | None:
    from core.clock import parse_hhmm
    try:
        parse_hhmm(v)
    except ValueError:
        return None
    return v


def _city(v: str) -> str | None:
    v = v.strip()
    return v if 2 < len(v) <= 30 and not re.search(r"(и|был\w*|будет|завтра|вчера)", v) else None


_CHECK = {"t": _alarm_time, "t2": _alarm_time, "city": _city, "v": _level, "v2": _level, "d": _duration, "app": _app, "song": _song, "yq": _query, "ch": _query}


_OFFLINE_ACTIONS = {
    "desktop_context": {"resume"},
    "computer_control": {"volume_up", "volume_down", "volume_set", "mute", "unmute",
                         "brightness_up", "brightness_down", "brightness_set"},
    "clock": {"now", "timer_set", "timer_list", "timer_cancel", "timer_pause",
              "timer_resume", "timer_add", "stopwatch_start", "stopwatch_stop",
              "alarm_stop", "alarm_snooze"},
}


_FILM_PLAY = r"(?:поставь|включи|открой|запусти|воспроизведи|найди|покажи|посмотри|хочу посмотреть|поставить)"
_KINO = r"(?:киног[оа]|kinogo)"
_YT = r"(?:ютуб(?:е|а)?|youtube)"
_PROVIDER = r"(?:через|на|в)\s+(?:сайт(?:е)?\s+)?"
_NO_YT = re.compile(rf"\bне\s+(?:{_PROVIDER})?{_YT}\b", re.I)
_LAST_FILM = re.compile(r"(?:его|её|ее|это|этот фильм|этот сериал|то же|тот же)", re.I)


def youtube_requested(text: str) -> bool:
    from core.wake_vosk import split_address

    _, text = split_address(text or "", allow_alias=os.getenv("JARVIS_WAKE_CHARLIE", "1") != "0")
    return not _NO_YT.search(text) and bool(
        re.search(rf"\b{_PROVIDER}{_YT}\b|^\s*{_FILM_PLAY}\s+{_YT}\b", text, re.I)
        or re.search(r"https?://(?:www\.)?(?:youtube\.com|youtu\.be)/", text, re.I)
    )


_SERIES_NUMBERS = {
    word: number
    for number, forms in enumerate((
        "один одна первый первая первую", "два две второй вторая вторую",
        "три третий третья третью", "четыре четвёртый четвёртая четвёртую",
        "пять пятый пятая пятую", "шесть шестой шестая шестую",
        "семь седьмой седьмая седьмую", "восемь восьмой восьмая восьмую",
        "девять девятый девятая девятую", "десять десятый десятая десятую",
    ), 1)
    for word in forms.replace("ё", "е").split()
}
_SERIES_NUMBER = r"(?:\d{1,3}|" + "|".join(_SERIES_NUMBERS) + r")"


def _episode_number(token: str) -> int:
    return int(token) if token.isdecimal() else _SERIES_NUMBERS[token.casefold().replace("ё", "е")]


def _series_request(command: str) -> dict | None:
    command = command.casefold().replace("ё", "е").strip(" ,.!?")
    if re.fullmatch(r"(?:а |ну )?(?:это |сейчас )?(?:какая|какой)(?: это| сейчас)? (?:сезон(?: и серия)?|серия)(?: сейчас| идет)?", command):
        return {"action": "series_position"}
    if re.fullmatch(r"(?:(?:где) (?:я |мы )?остановил(?:ся|ась|ись)(?: (?:в сериале|с сериалом))?|"
        r"(?:на чем|на чём) (?:я |мы )?остановил(?:ся|ась|ись) (?:в сериале|с сериалом))", command):
        return {"action": "series_position"}
    if re.fullmatch(r"(?:(?:включи|поставь|открой|давай) )?(?:следующую серию|следующая серия)", command):
        return {"action": "series_next"}
    if re.fullmatch(r"(?:начни|поставь|включи) (?:(?:эту|текущую) )?серию (?:с начала|сначала)", command):
        return {"action": "series_restart"}
    resume = re.fullmatch(
        r"(?:продолжи(?:ть)? (?:смотреть )?сериал(?:\s+(.+))?|"
        r"продолжи(?:ть)? (?:просмотр|с того места(?: где (?:я )?остановился)?)|"
        r"(?:включи|поставь) с того места(?: где (?:я )?остановился)?)",
        command.replace(",", ""),
    )
    if resume:
        return {"action": "series_resume", "title": (resume[1] or "").strip(" «»\"")}
    episode = re.fullmatch(
        rf"(?:(?:включи|поставь|открой) )?(?:({_SERIES_NUMBER})\s*сезон[ ,]+)?"
        rf"(?:({_SERIES_NUMBER})(?:\s*[-–]?\s*(?:ая|ую|я))?\s*сери[яю]|серия\s+({_SERIES_NUMBER}))",
        command,
    )
    if episode:
        return {"action": "series_episode", "season": _episode_number(episode[1]) if episode[1] else None,
                "episode": _episode_number(episode[2] or episode[3])}
    return None


def movie_request(text: str, last_movie: dict | None = None) -> dict | None:
    from core.wake_vosk import split_address

    _, command = split_address(text or "", allow_alias=os.getenv("JARVIS_WAKE_CHARLIE", "1") != "0")
    command = re.sub(r"^\s*(?:пожалуйста|давай)[,\s]+", "", command.strip(), flags=re.I)
    contextual = _series_request(command)
    if contextual:
        return contextual
    hit = re.fullmatch(rf"{_FILM_PLAY}\s+(?:(фильм|сериал|кино)\b\s*)?(.*)", command, re.I)
    if not hit:
        return None
    kind, tail = hit.groups()
    if re.fullmatch(r"(?:на\s+)?(?:паузу|пауза|весь экран|полный экран)", tail.strip(" ,.!?"), re.I):
        return None
    kinogo = bool(re.search(rf"\b{_PROVIDER}{_KINO}\b", tail, re.I))
    denied = _NO_YT.search(tail)
    youtube = youtube_requested(command)
    tail = re.sub(rf"(?:\s*,?\s+а)?\s*,?\s+\bне\s+(?:{_PROVIDER})?{_YT}\b.*$", "", tail, flags=re.I)
    title = re.sub(rf"\b{_PROVIDER}(?:{_KINO}|{_YT})(?:\s+сайт(?:е)?)?\b", "", tail, flags=re.I)
    title = title.strip(" ,.!?«»\"")
    if not kind and not kinogo and not (denied and _LAST_FILM.fullmatch(title)) and not (
        youtube and last_movie and _LAST_FILM.fullmatch(title)
    ):
        return None
    provider = "youtube" if youtube and not kinogo else "kinogo"
    if not title or _LAST_FILM.fullmatch(title):
        return {**(last_movie or {"title": ""}), "action": "play", "provider": provider}
    result = {"action": "play", "provider": provider}
    if kind and kind.casefold() == "сериал":
        number = _SERIES_NUMBER.replace("четверт", "четв[её]рт")
        ordinal = rf"(?P<num>{number})(?:\s*[-–]?\s*(?:ый|ой|ий|ая|ую|я|й))?"
        while title:
            title = title.rstrip(" ,.;!?")
            for key, noun in (("season", r"сезон(?:а|е)?"), ("episode", r"(?:сери[яюи]|эпизод(?:а)?)")):
                suffix = re.search(
                    rf"(?:^|[\s,]+(?:и\s+)?)(?:{ordinal}\s*{noun}|{noun}\s*(?P<after>{number}))$",
                    title, re.I)
                if suffix:
                    title = title[:suffix.start()].rstrip(" ,")
                    result[key] = _episode_number(suffix["num"] or suffix["after"])
                    break
            else:
                break
        if not {"season", "episode"} & result.keys() and provider == "kinogo":
            result["action"] = "series_continue"
        else:
            result.setdefault("season", 1)
            result.setdefault("episode", 1)
    result["title"] = re.sub(r"\b((?:19|20)\d{2})\s+год(?:а|у)?(?:[\s,]+(?:выпуск(?:а)?|выход(?:а)?))?$", r"\1", title, flags=re.I).casefold()
    return result


def match(text: str, *, offline: bool = False, last_movie: dict | None = None) -> Quick | None:
    """Команда целиком — Quick; иначе None (фразу разбирает Gemini)."""
    if not enabled():
        return None
    request = movie_request(text, last_movie)
    if request is not None:
        return Quick("movie_player", request, "", text)
    t = normalize(text)
    if not t:
        return None
    if not offline:
        # Свои команды и паки (core/macros.py) — первыми: их выбрал сам владелец.
        try:
            from core.macros import macros
            hit = macros().match(text)
        except Exception as exc:
            logger.debug("Свои команды: %s", exc)
            hit = None
        if hit:
            cmd, _slots = hit
            if cmd.confirm:
                return None                           # переспросит Gemini
            return Quick("macro", {"action": "run", "phrase": text}, "ack", t)
    for rx, tool, args, reply in _COMPILED:
        if offline and args.get("action") not in _OFFLINE_ACTIONS.get(tool, ()) and args.get("target") != "context":
            continue
        m = rx.fullmatch(t)
        if not m:
            continue
        out = {}
        for k, v in args.items():
            if isinstance(v, str) and v.startswith("@"):
                group = v[1:]
                raw = m.groupdict().get(group) or m.groupdict().get(group + "2")
                if raw is None:
                    continue                      # необязательное: «отложи» без минут
                raw = raw.strip()
                check = _CHECK.get(group)
                if check and not check(raw):
                    return None                   # «громкость какая-то» — пусть решает Gemini
                out[k] = raw
            else:
                out[k] = v
        if tool == "video_control" and re.search(r"\b" + _VIDEO + r"\b", t):
            out["target"] = "video"
        return Quick(tool, out, reply, t)
    try:
        from core.dialog_context import context
        ctx_quick = context().resolve(t)
        if offline:
            return ctx_quick if ctx_quick and ctx_quick.tool == "movie_player" else None
        if ctx_quick is not None:
            return ctx_quick
    except Exception as exc:
        logger.debug("dialog_context resolve error: %s", exc)
    return None


# ── Неудачу видно по ответу инструмента ──────────────────────────────────────
_FAIL_RE = re.compile(r"(не удалось|не получилось|не нашел|не нашёл|не найден|ошибк|не могу|недоступ|"
                      r"нет связи|не запущен|ничего не|не подключ|не играет|не отвечает|не успело|"
                      r"не выполнено|нет активн|не понял|уточните)", re.I)


def failed(result) -> bool:
    return bool(_FAIL_RE.search(str(result or "")))


# Где ответ нужен словами — само содержимое: время, что играет, сколько осталось.
_ANSWERS = {("desktop_context", "resume"), ("weather", ""), ("clock", "alarm_set"), ("clock", "now"), ("clock", "timer_list"),
            ("clock", "timer_cancel"), ("clock", "timer_add"), ("clock", "timer_modify"),
            ("clock", "timer_pause"), ("clock", "timer_resume"),
            ("clock", "stopwatch_stop"), ("clock", "stopwatch_status"), ("music_player", "now_playing")}


def voice_for_actions() -> bool:
    """Настройка «Отвечать голосом на простые команды» (по умолчанию — нет)."""
    return os.getenv("JARVIS_QUICK_VOICE", "0").strip().lower() in ("1", "true", "yes", "on")


def silent(q: Quick, result) -> bool:
    """Простое действие получилось — подтвердить звуком, без слов (как у Siri):
    «пауза», «громче», «следующий трек», «таймер на 5 минут», свои команды.
    Голосом — только если не вышло, если нужен ответ («который час») или
    это разговор («спасибо», «ты тут?»)."""
    if q.tool is None or voice_for_actions():
        return False
    if (q.tool, str(q.args.get("action", ""))) in _ANSWERS:
        return False
    return not failed(result)


def reply_for(q: Quick, result) -> str:
    """Что сказать после команды: готовая фраза, а если не вышло или ответ
    содержательный — сам ответ инструмента."""
    text = str(result or "").strip()
    if q.tool is None:
        return q.phrase()
    if q.reply and not failed(text):
        return q.phrase()
    # Ответ инструмента бывает с кодом ошибки, путём или ссылкой — вслух и в
    # чат идёт только человеческая часть.
    from core.speech_text import for_speech
    text = for_speech(text).strip()
    if text.startswith("Ошибка:"):
        text = "Не получилось, сэр: " + text[len("Ошибка:"):].strip()
    return text or q.phrase() or random.choice(ACK)


# ── Кэш озвученных фраз ───────────────────────────────────────────────────────
class VoiceCache:
    """PCM готовых фраз на диске: ключ — голос, частота и сам текст."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self._mem: dict[str, bytes] = {}

    @staticmethod
    def voice() -> str:
        try:
            from telegram_bot import tts_fish
            if tts_fish.is_configured():
                return "fish:" + tts_fish._voice_id()
        except Exception:
            pass
        return "edge:" + os.getenv("EDGE_VOICE", "ru-RU-DmitryNeural")

    MAX_LEARNED = 400          # сколько коротких фраз запоминать сверх готовых
    LEARN_CHARS = 40

    @staticmethod
    def norm(text: str) -> str:
        """«Готово, сэр.» и «готово сэр» — одна фраза (Gemini ставит запятые как придётся).
        Знак в конце сохраняем: вопрос звучит иначе, чем утверждение."""
        t = (text or "").lower().replace("ё", "е").strip()
        end = "?" if t.endswith("?") else "!" if t.endswith("!") else "."
        return " ".join(re.findall(r"\w+", t)) + end

    def _key(self, text: str, rate: int, voice: str | None) -> str:
        raw = f"{voice or self.voice()}|{rate}|{self.norm(text)}"
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:20]

    def get(self, text: str, rate: int, voice: str | None = None) -> bytes | None:
        k = self._key(text, rate, voice)
        if k in self._mem:
            return self._mem[k]
        p = self.root / f"{k}.pcm"
        try:
            data = p.read_bytes()
        except OSError:
            return None
        if data:
            self._mem[k] = data
        return data or None

    def put(self, text: str, rate: int, pcm: bytes, voice: str | None = None):
        if not pcm:
            return
        k = self._key(text, rate, voice)
        self._mem[k] = pcm
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            tmp = self.root / f"{k}.tmp"
            tmp.write_bytes(pcm)
            tmp.replace(self.root / f"{k}.pcm")
        except OSError as exc:
            logger.debug("Кэш голоса не записан: %s", exc)

    def wanted(self, text: str) -> bool:
        """Готовые фразы — всегда; ещё короткие без чисел («Открываю Telegram, сэр.»):
        тот же текст — тот же звук, второй раз он звучит сразу. Числа (время,
        температура, счёт) меняются — такое не храним, как и длинные ответы."""
        t = text.strip()
        if self.norm(t) in _PHRASE_SET:
            return True
        if len(t) > self.LEARN_CHARS or re.search(r"\d", t) or not re.search(r"\w", t):
            return False
        try:
            return sum(1 for _ in self.root.glob("*.pcm")) < len(_PHRASE_SET) + self.MAX_LEARNED
        except OSError:
            return False


_PHRASE_SET = {VoiceCache.norm(p) for p in all_phrases()}
_cache: VoiceCache | None = None


def voice_cache() -> VoiceCache:
    global _cache
    if _cache is None:
        try:
            from core.paths import get_data_root
            root = Path(get_data_root()) / "voice_cache"
        except Exception:
            root = Path(__file__).resolve().parent.parent / "voice_cache"
        _cache = VoiceCache(root)
    return _cache


async def prewarm(synth, rate: int) -> int:
    """Озвучить заранее всё, чего ещё нет в кэше. synth(text) → pcm.
    Сколько озвучено."""
    cache = voice_cache()
    voice = cache.voice()
    done = 0
    for text in all_phrases():
        if cache.get(text, rate, voice):
            continue
        try:
            pcm = await synth(text)
        except Exception as exc:
            logger.debug("Готовая фраза «%s» не озвучена: %s", text, exc)
            continue
        if pcm:
            cache.put(text, rate, pcm, voice)
            done += 1
    if done:
        logger.info("Готовые фразы озвучены: %d", done)
    return done
