"""Фильмы (VK Видео), ролики и клипы (YouTube) и голосовое управление просмотром.

Всё идёт в окне браузера Джарвиса (core/browser_cdp.py): поиск на самом
сайте, переход на страницу видео, запуск, полный экран — и дальше пауза,
перемотка, время, громкость, mute напрямую через плеер страницы. Каждое
действие проверяется по состоянию плеера, ответ — то, что произошло.
"""
from __future__ import annotations

from datetime import date
from dataclasses import dataclass
import math
from pathlib import PureWindowsPath
import json
import logging
import re
import threading
import time
import urllib.parse

from websockets.exceptions import WebSocketException

from core import browser_cdp as cdp
from core.paths import get_user_data_dir
from core.storage import atomic_write_json, safe_read_json

logger = logging.getLogger(__name__)

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
_FILM_MIN_SEC = 40 * 60          # фильм — от 40 минут; короче — трейлер или нарезка
_movie_launch_lock = threading.Lock()


def film_target_active() -> bool:
    return _movie_launch_lock.locked() or cdp.has_embedded_video_target()


class MovieLaunch:
    def __init__(self, timeout: float = 40, cancelled: threading.Event | None = None):
        self.deadline = time.monotonic() + max(0.0, timeout)
        self.cancelled = cancelled or threading.Event()
        self.outcome: str | None = None

    def remaining(self, maximum: float = 40) -> float:
        left = self.deadline - time.monotonic()
        if self.cancelled.is_set() or left <= 0:
            raise TimeoutError("Время запуска фильма истекло")
        return min(left, maximum)


# ── время: «1:20:00», «5 минут», «полчаса» → секунды ─────────────────────────

_WORD_NUM = {"одну": 1, "одна": 1, "один": 1, "две": 2, "два": 2, "три": 3, "пять": 5,
             "десять": 10, "пятнадцать": 15, "двадцать": 20, "тридцать": 30, "сорок": 40}


def parse_seconds(text, default: float | None = None) -> float | None:
    s = str(text or "").strip().lower().replace(",", ".")
    if not s:
        return default
    m = re.fullmatch(r"(?:(\d+):)?(\d{1,2}):(\d{2})", s)
    if m:
        h, mi, se = int(m.group(1) or 0), int(m.group(2)), int(m.group(3))
        return h * 3600 + mi * 60 + se
    if "полчас" in s:
        return 1800
    if "полминут" in s:
        return 30
    total, found = 0.0, False
    for num, unit in re.findall(r"(\d+(?:\.\d+)?|[а-я]+)?\s*(час|мин|сек|h|m|s)\w*", s):
        n = float(num) if num and num[0].isdigit() else _WORD_NUM.get(num, 1)
        total += n * {"час": 3600, "h": 3600, "мин": 60, "m": 60}.get(unit, 1)
        found = True
    if found:
        return total
    m = re.search(r"\d+(?:\.\d+)?", s)
    return float(m.group()) if m else default


def fmt_time(sec: float) -> str:
    sec = int(max(0, sec))
    h, rest = divmod(sec, 3600)
    m, s = divmod(rest, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _say_duration(sec: float) -> str:
    sec = int(max(0, sec))
    h, rest = divmod(sec, 3600)
    m, s = divmod(rest, 60)
    parts = [f"{amount} {unit}" for amount, unit in ((h, "ч"), (m, "мин"), (s, "с")) if amount]
    return " ".join(parts) or f"{sec} с"


# ── общий запуск: открыть страницу, дождаться плеера, play, полный экран ─────

def _start(url: str, fullscreen: bool = True, wait_sec: float = 15.0,
           embedded: bool = False, launch: MovieLaunch | None = None,
           episode: tuple[int, int] | None = None, watch_title: str = "",
           position: int = 0) -> dict | None:
    # Панель браузера рядом с шаром отдаёт окно: фильм — на весь экран.
    try:
        from core import browser_panel
        browser_panel.release_for_video()
    except Exception as exc:
        logger.debug("Панель браузера: %s", exc)
    t = cdp.tab()
    if not t:
        return None
    if launch:
        launch.remaining()
    t.navigate(url)
    if embedded:
        t.enable_embedded_video()
        started = time.monotonic()
        deadline = started + (launch.remaining(wait_sec) if launch else wait_sec)
        try_alternate = episode is None and _kinogo_page(url)
        selected = episode is None
        minimum_duration = 5 * 60 if episode else _FILM_MIN_SEC
        while True:
            if launch:
                launch.remaining()
            now = time.monotonic()
            if not selected and episode and now - started >= 1:
                try:
                    selected = _select_kinogo_episode(t, episode)
                except (RuntimeError, TimeoutError):
                    logger.debug("Плеер сериала Kinogo ещё загружается", exc_info=True)
            if selected and cdp.pin_film_player(
                t, minimum_duration=minimum_duration,
                frame_selector=_KINOGO_SERIES_FRAME if episode else None,
            ):
                break
            if now >= deadline:
                return None
            if try_alternate and now - started >= 1:
                try:
                    try_alternate = not t.eval(_KINOGO_ALTERNATE_PLAYER, timeout=2)
                except (RuntimeError, TimeoutError):
                    logger.debug("Вкладки плеера Kinogo пока не загрузились", exc_info=True)
            time.sleep(0.2)
    elif not t.wait(f"(v => v && v.readyState >= 1)({cdp._VIDEO})",
                    timeout=launch.remaining(wait_sec) if launch else wait_sec):
        return None
    if position:
        st = cdp.video_js(
            f"if (!Number.isFinite(v.duration) || {position} >= v.duration) return null; "
            f"v.currentTime = {position}", t)
        if not st or abs(st.get("t", -10) - position) > 2:
            cdp.video_js("v.pause()", t)
            return {"resume_error": True}
    expiry = round((time.time() + launch.remaining()) * 1000) if launch else None
    guard = (f"if (Date.now() >= {expiry}) return null; "
             f"const timer = setTimeout(() => v.pause(), {expiry} - Date.now()); " if expiry else "")
    cleanup = "finally { clearTimeout(timer); }" if expiry else ""
    st = cdp.video_js(guard + "v.muted = false; if (v.volume < 0.05) v.volume = 1; "
                      "try { await v.play() } catch (e) {} " + cleanup, t)
    for _ in range(10):                              # до ~2 с: убедиться, что пошло
        if _played(st):
            break
        if launch:
            launch.remaining()
        time.sleep(0.2)
        st = cdp.video_state(t)
        if st and not st["paused"]:
            break
    if position and _played(st) and abs(st.get("t", -10) - position) > 3:
        cdp.video_js("v.pause()", t)
        return {"resume_error": True}
    # Сначала окно вперёд, потом полный экран: вывод вперёд после него
    # (фокус, Alt для SetForegroundWindow) сбрасывал полный экран плеера.
    cdp.bring_to_front()
    full = cdp.fullscreen(True, t) if fullscreen and _played(st) else False
    st = cdp.video_state(t) or st
    if _played(st) and episode and watch_title:
        _track_series(t, watch_title, url, episode)
    return dict(st, fs=full or st.get("fs", False)) if st else None


def _played(st: dict | None) -> bool:
    return bool(st) and not st.get("paused", True)


_KINOGO_HOME = "https://kz6.kinogo-films.lol/"
# Kinogo переезжает по зеркалам kzN (28.11: kz5 → kz6, проверка «это Kinogo?»
# перестала проходить, и серии не запускались). Годится любое зеркало kzN.
_KINOGO_HOST_RE = re.compile(r"^kz\d+\.kinogo-films\.lol$")
_KINOGO_ORIGIN_JS = r"/^https:\/\/kz\d+\.kinogo-films\.lol$/.test(location.origin)"
_KINOGO_HOST = urllib.parse.urlsplit(_KINOGO_HOME).hostname
_KINOGO_ALTERNATE_PLAYER = r"""(() => {
    if (!(__KINOGO_ORIGIN_OK__) || document.readyState !== 'complete') return false;
    const frame = document.querySelector('.js-player-container iframe');
    if (!frame || frame.getAttribute('src') || frame.getAttribute('data-src')) return false;
    const tab = document.querySelector('.js-player-tabs > [data-provider]:not(.current)');
    if (!tab || !tab.getClientRects().length) return false;
    tab.click();
    return true;
})()""".replace("__KINOGO_ORIGIN_OK__", _KINOGO_ORIGIN_JS)
_KINOGO_SERIES_PROVIDER = r"""(() => {
    if (!(__KINOGO_ORIGIN_OK__)) return false;
    const frame = document.querySelector('.js-player-container iframe');
    if (frame?.getAttribute('src') && new URL(frame.src).hostname === 'bd.shizahd.pro') return true;
    const tab = [...document.querySelectorAll('.js-player-tabs > [data-provider]')]
        .find(item => item.innerText.trim() === 'ShizaHD');
    if (tab?.getClientRects().length) tab.click();
    return false;
})()""".replace("__KINOGO_ORIGIN_OK__", _KINOGO_ORIGIN_JS)
_KINOGO_SERIES_FRAME = ("location.hostname === 'bd.shizahd.pro' && "
                        "Boolean(document.querySelector('.pjsplplayerscroll > pjsdiv'))")
_KINOGO_EPISODE = r"""(([season, episode]) => {
    if (location.hostname !== 'bd.shizahd.pro' || typeof window.player?.api !== 'function') return false;
    const player = window.player;
    const current = player.api('playlist_id');
    if (!/^veo\d+_\d+_\d+$/.test(current)) return false;
    const folders = player.api('playlist_folders');
    if (!Array.isArray(folders) || !folders.some(folder => folder.title === 'Сезон ' + season)) return false;
    const episodeId = "veo" + season + "_" + episode + "_0";
    if (current !== episodeId && player.api('find', episodeId) !== true) return false;
    const active = [...document.querySelectorAll('.pjs-player-active-pl')].map(item => item.innerText.trim());
    if (player.api('playlist_id') !== episodeId ||
        !active.includes('Сезон ' + season) || !active.includes(episode + ' серия')) return false;
    return player.api('playing') === true || player.api('play', 'id:' + episodeId) === true;
})"""


def _select_kinogo_episode(t: cdp.Tab, episode: tuple[int, int]) -> bool:
    if not t.eval(_KINOGO_SERIES_PROVIDER, timeout=2):
        return False
    frame = t.video_frame(_KINOGO_SERIES_FRAME)
    if not frame or not frame.session_id:
        return False
    # API провайдера доступен в основном контексте iframe, а не в изолированном мире CDP.
    result = t.call("Runtime.evaluate", timeout=2, session_id=frame.session_id,
                    expression=f"{_KINOGO_EPISODE}({json.dumps(episode)})", returnByValue=True,
                    userGesture=True)
    return result.get("result", {}).get("value") is True


_KINOGO_ACCESS_DENIED = r"/^(access denied|forbidden|403|just a moment)/i.test(document.title.trim())"
_KINOGO_SEARCH = r"""(title => {
    const field = document.querySelector('form input[name=story], form input[type=search], form input[name=q]');
    if (!field || new URL(field.form.action, location.href).origin !== location.origin) return false;
    field.value = title;
    field.dispatchEvent(new Event('input', {bubbles: true}));
    field.form.requestSubmit();
    return true;
})"""
_KINOGO_LINKS = r"""(() => Array.from(document.querySelectorAll('a[href]'), a => {
    const card = a.closest('article, .shortstory, .short, .th-item, .movie-item, .search-item');
    const yearField = card?.querySelector('[itemprop=datePublished], [data-year]');
    const yearValue = yearField?.getAttribute('content') || yearField?.dataset.year || yearField?.textContent || '';
    const cardYear = card?.innerText?.match(/(?:год(?: выпуска)?|год выхода)\s*:?\s*((?:19|20)\d{2})/i)?.[1];
    return {
        url: a.href,
        title: (a.innerText || '').trim().split('\n')[0],
        alternate: (a.getAttribute('title') || '').trim(),
        year: yearValue.match(/^\s*((?:19|20)\d{2})\b/)?.[1] || cardYear || ''
    };
}).filter(link => (link.title || link.alternate) && link.url.startsWith(location.origin + '/')))()"""

def _kinogo_page(url: str) -> bool:
    parts = urllib.parse.urlsplit(url)
    return (parts.scheme == "https" and bool(_KINOGO_HOST_RE.match(parts.netloc)) and
            parts.path not in ("", "/"))


def _film_name(text: str) -> str:
    text = re.split(r"\s+смотреть\s+онлайн\b", text.casefold(), maxsplit=1)[0]
    return " ".join(re.findall(r"\w+", text.replace("ё", "е")))


def _film_title_year(text: str) -> tuple[str, str | None]:
    name = _film_name(text)
    match = re.fullmatch(r"(.+) ((?:19|20)\d{2})", name)
    if match and int(match[2]) <= date.today().year + 2:
        return match[1], match[2]
    return name, None


def _kinogo_matches(title: str, links: list[dict], season: int | None = None) -> set[str]:
    wanted, wanted_year = _film_title_year(title)
    if not wanted:
        return set()
    matches = set()
    for link in links:
        url = link.get("url", "")
        if not _kinogo_page(url):
            continue
        for field in ("title", "alternate"):
            text = link.get(field, "")
            seasons = re.search(r"\s*\((\d+)(?:\s*[-–]\s*(\d+))?\s+сезон(?:ы|ов)?\)\s*$", text, re.I)
            if season is not None:
                if not seasons or not int(seasons[1]) <= season <= int(seasons[2] or seasons[1]):
                    continue
                text = text[:seasons.start()]
                text = re.sub(r"^Сериал\s+", "", text, flags=re.I)
            for name, title_year in map(_film_title_year, re.split(r"\s+[/|]\s+", text)):
                year = title_year or link.get("year")
                if name == wanted and (wanted_year is None or str(year) == wanted_year):
                    matches.add(urllib.parse.urldefrag(url).url)
                    break
    return matches


_SERIES_PROGRESS = r"""(() => {
    if (location.hostname !== 'bd.shizahd.pro') return null;
    const id = window.player?.api?.('playlist_id');
    const match = /^veo(\d{1,3})_(\d{1,3})_0$/.exec(id || '');
    if (!match) return null;
    const labels = [...document.querySelectorAll('.pjs-player-active-pl')].map(el => el.innerText.trim());
    if (!labels.includes('Сезон ' + match[1]) || !labels.includes(match[2] + ' серия')) return null;
    const video = [...document.querySelectorAll('video')]
        .filter(v => v.readyState >= 2 && v.getBoundingClientRect().width > 50 &&
                     Number.isFinite(v.duration) && v.duration >= 300)
        .sort((a, b) => b.duration - a.duration)[0];
    if (!video || !video.currentSrc) return null;
    return {season: Number(match[1]), episode: Number(match[2]), source: video.currentSrc,
            t: video.currentTime, d: video.duration, paused: video.paused};
})()"""


@dataclass
class _Watching:
    tab: cdp.Tab
    frame: object
    title: str
    url: str
    episode: tuple[int, int]
    source: str = ""
    last_position: float = 0


_watch_lock = threading.Lock()
_watching: _Watching | None = None
_WATCH_POLL_SECONDS = 3
_WATCH_ERRORS = (OSError, RuntimeError, TimeoutError, WebSocketException)


def _watch_file():
    return get_user_data_dir() / "watch_progress.json"


def _watch_key(title: str) -> str:
    return _film_name(title)


def _valid_bookmark(row) -> bool:
    if not isinstance(row, dict):
        return False
    try:
        valid_url = isinstance(row.get("url"), str) and _kinogo_page(row["url"])
    except ValueError:
        return False
    return (isinstance(row.get("title"), str) and bool(row["title"].strip()) and valid_url
            and all(type(row.get(key)) is int and 1 <= row[key] <= 999
                    for key in ("season", "episode"))
            and type(row.get("position")) is int and 0 <= row["position"] < 86400
            and type(row.get("duration")) is int and row["position"] <= row["duration"] < 86400)


def _watch_history() -> list[dict]:
    data = safe_read_json(_watch_file(), default=[])
    return [row for row in data[:50] if _valid_bookmark(row)] if isinstance(data, list) else []


def _bookmark(title: str = "") -> dict | None:
    with _watch_lock:
        history = _watch_history()
    return next((row for row in history if not title or _watch_key(row["title"]) == _watch_key(title)), None)


def _save_watch(watch: _Watching, state: dict) -> None:
    seconds, duration = state.get("t"), state.get("d")
    if not all(type(value) in (int, float) and math.isfinite(value) for value in (seconds, duration)):
        return
    if not 0 <= seconds <= duration < 86400 or duration < 300:
        return
    row = {"title": watch.title, "url": watch.url, "season": watch.episode[0],
           "episode": watch.episode[1], "position": int(seconds), "duration": round(duration)}
    with _watch_lock:
        if _watching is not watch:
            return
        history = _watch_history()
        if history and {k: v for k, v in history[0].items() if k != "at"} == row:
            return
        row["at"] = time.time()                  # когда смотрели — для «какой продолжить?»
        key = _watch_key(watch.title)
        history = [item for item in history if _watch_key(item["title"]) != key]
        atomic_write_json(_watch_file(), [row, *history[:49]])


def _checkpoint(watch: _Watching) -> bool | None:
    tab = watch.tab
    if not getattr(tab, "_film_player_required", False):
        return None
    tab_url = tab.eval("location.href", timeout=2)
    if not tab_url or urllib.parse.urlsplit(tab_url)[:3] != urllib.parse.urlsplit(watch.url)[:3]:
        return None

    # Read the playlist ID and the actual video position in one browser evaluation.
    frame = tab.video_frame(_KINOGO_SERIES_FRAME)
    if not frame or not frame.session_id:
        return False
    result = tab.call("Runtime.evaluate", timeout=2, session_id=frame.session_id,
                      returnByValue=True, expression=_SERIES_PROGRESS)
    state = result.get("result", {}).get("value")
    if not state:
        return False
    episode = state["season"], state["episode"]
    source, position = state["source"], state["t"]
    if episode != watch.episode:
        # Playlist UI often advances before the old stream has been replaced.
        reset = position < 20 and position + 3 < watch.last_position
        if not watch.source or (source == watch.source and not reset) or state["paused"]:
            return False
    pinned = getattr(tab, "_film_player", None)
    if (episode != watch.episode or frame.session_id != getattr(pinned, "session_id", None)
            or not cdp.video_state(tab)):
        if not cdp.pin_film_player(tab, minimum_duration=300, frame_selector=_KINOGO_SERIES_FRAME):
            return False
    watch.frame = tab._film_player
    watch.episode, watch.source, watch.last_position = episode, source, position
    _save_watch(watch, state)
    return True


def _poll_watch(watch: _Watching) -> None:
    while _watching is watch:
        time.sleep(_WATCH_POLL_SECONDS)
        if _watching is not watch:
            break
        if not _movie_launch_lock.acquire(blocking=False):
            continue
        try:
            if _watching is not watch or _checkpoint(watch) is None:
                return
        except _WATCH_ERRORS as exc:
            logger.warning("Сохранение просмотра: %s", exc)
        finally:
            _movie_launch_lock.release()


def _track_series(tab, title: str, url: str, episode: tuple[int, int]) -> None:
    global _watching
    frame = getattr(tab, "_film_player", None)
    if not getattr(frame, "session_id", None):
        return
    watch = _Watching(tab, frame, title, url, episode)
    with _watch_lock:
        _watching = watch
    try:
        _checkpoint(watch)
    except _WATCH_ERRORS as exc:
        logger.warning("Сохранение просмотра: %s", exc)
    threading.Thread(target=_poll_watch, args=(watch,), name="watch-progress", daemon=True).start()


def _save_current_watch() -> _Watching | None:
    watch = _watching
    if watch is not None and _movie_launch_lock.acquire(blocking=False):
        try:
            if _checkpoint(watch):
                return watch
        except _WATCH_ERRORS as exc:
            logger.warning("Сохранение просмотра: %s", exc)
        finally:
            _movie_launch_lock.release()
    return None


RECENT_SERIES_SEC = 3 * 24 * 3600


def recent_series(now: float | None = None) -> list[dict]:
    """Сериалы, которые смотрели за последние 3 дня (свежие первыми)."""
    now = time.time() if now is None else now
    with _watch_lock:
        history = _watch_history()
    return [r for r in history if isinstance(r.get("at"), (int, float)) and now - r["at"] <= RECENT_SERIES_SEC]


def _which_series() -> str:
    """«Продолжи сериал», а недавно шло несколько — спросить, какой (просьба 28.11).
    Один — включаем сразу, как раньше."""
    recent = recent_series()
    if len(recent) < 2:
        return ""
    names = "; ".join(f"«{r['title']}» (сезон {r['season']}, серия {r['episode']}, {fmt_time(r['position'])})"
                      for r in recent[:4])
    return (f"Недавно смотрели несколько: {names}. Спроси владельца, какой продолжить, и вызови "
            "series_resume с title. Ничего не включай до ответа.")


def save_watch_progress() -> None:
    if _watching is not None:
        _save_current_watch()


def series_context(action: str, title: str, player=None, launch=None,
                   *, season: int | None = None, episode: int | None = None) -> str:
    if _movie_launch_lock.locked():
        return "Подожди, пока загрузится текущая серия."
    active = _save_current_watch()
    if not title and action in ("series_resume", "series_continue"):
        choice = _which_series()
        if choice:
            if launch:
                launch.outcome = "success"
            return choice
    row = _bookmark(title)
    if not row:
        if action == "series_continue" and title:
            return play_kinogo(title, player, launch, episode=(1, 1))
        return f"Для «{title}» ещё нет сохранённого просмотра." if title else "Сохранённого просмотра пока нет."
    name = row["title"]
    current = row["season"], row["episode"]
    position = row["position"]
    if action == "series_position":
        if launch:
            launch.outcome = "success"
        return f"«{name}», сезон {current[0]}, серия {current[1]}, на {fmt_time(position)}."
    if action in ("series_resume", "series_continue"):
        if row["duration"] - position <= 5:
            return f"Серия {current[1]} сериала «{name}» почти закончилась. Скажи «следующая серия», чтобы перейти дальше."
        if (active is not None and _watch_key(active.title) == _watch_key(name)
                and active.episode == current and active.url == row["url"]
                and _movie_launch_lock.acquire(blocking=False)):
            try:
                if getattr(active.tab, "_film_player", None) is active.frame:
                    state = cdp.video_js("if (v.paused) await v.play()", active.tab)
                    if _played(state):
                        if launch:
                            launch.outcome = "success"
                        return f"Продолжаю «{name}», сезон {current[0]}, серия {current[1]}, с {fmt_time(state['t'])}."
            finally:
                _movie_launch_lock.release()
        return play_kinogo(row["url"], player, launch, episode=current,
                           watch_title=name, position=position)
    if action == "series_restart":
        target = current
    elif action == "series_next":
        if current[1] >= 999:
            return "Уточни сезон и номер следующей серии."
        target = current[0], current[1] + 1
    else:
        target = (
            season if season is not None else current[0],
            episode if episode is not None else 1,
        )
    source = row["url"] if target[0] == current[0] else name
    return play_kinogo(source, player, launch, episode=target, watch_title=name)


_FILM_HESITATION = re.compile(r"[,.;!?…—]\s*(?:э+|эм+|м{2,})\s*(?:[,.;!?…—]+\s*|$)", re.I)


def _kinogo_search_queries(title: str):
    # Keep the original first: punctuation or a hesitation-like syllable may belong to a title.
    cleaned = _FILM_HESITATION.sub(" ", title).strip()
    # ASR often omits punctuation around a spoken hesitation ("Бэтмен э начало").
    # Keep that as a fallback query rather than searching the filler as part of the title.
    cleaned = re.sub(r"(?i)(?<!\w)(?:э{1,3}|эм+|м-м)(?!\w)", " ", cleaned)
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip()
    seen = set()
    for candidate in dict.fromkeys((title, cleaned)):
        if not candidate:
            continue
        search_title, requested_year = _film_title_year(candidate)
        query = candidate.strip('«»" ').casefold()
        if requested_year:
            query = re.sub(rf"[\s(,]+{requested_year}\)?\s*$", "", query).rstrip('»" ')
        queries = [query]
        if ":" not in query and len(search_title.split()) == 2:
            first, second = search_title.split()
            queries.append(f"{first}: {second}")
        for query in queries:
            if query and query not in seen:
                seen.add(query)
                yield query, candidate


def play_kinogo(title: str, player=None, launch: MovieLaunch | None = None, *,
                episode: tuple[int, int] | None = None, watch_title: str = "",
                position: int = 0) -> str:
    if launch is not None:
        launch.outcome = "error"
    if episode is not None and any(type(number) is not int or not 1 <= number <= 999 for number in episode):
        return "Не удалось разобрать номер сезона или серии."
    title = (title or "").strip()
    if not title:
        return "Какой фильм включить на Kinogo, сэр?"
    _save_current_watch()
    if not _movie_launch_lock.acquire(blocking=False):
        return "Уже загружаю фильм."
    try:
        if not cdp.ensure_browser(timeout=launch.remaining(14) if launch else 14):
            return "Не удалось открыть браузер для Kinogo."
        if player:
            player.write_log(f"SYS: 🎬 Kinogo — ищу «{title}»")
        if _kinogo_page(title):
            url = title
        else:
            t = cdp.tab()
            if not t:
                return "Не удалось открыть вкладку Kinogo."
            t.navigate(_KINOGO_HOME)
            form = "document.querySelector('form input[name=story], form input[type=search], form input[name=q]')"
            if not t.wait(f"Boolean({form}) || ({_KINOGO_ACCESS_DENIED})",
                          timeout=launch.remaining(8) if launch else 8):
                return "Поиск Kinogo не загрузился. Можно указать прямую ссылку на фильм."
            if t.eval(_KINOGO_ACCESS_DENIED):
                return "Kinogo отклонил доступ к странице. Фильм не запустился."
            if not t.eval(_KINOGO_ORIGIN_JS):
                return "Kinogo перенаправил на другой сайт. Фильм не запустился."
            if launch:
                launch.remaining()
            url = None
            matches = set()
            for query, candidate in _kinogo_search_queries(title):
                if not t.eval(f"{_KINOGO_SEARCH}({json.dumps(query)})"):
                    return "Не удалось запустить поиск Kinogo. Можно указать прямую ссылку на фильм."
                empty_title = json.dumps(f"поиск {query}, найдено: 0 ")
                empty = f"document.title.trim().toLowerCase().startsWith({empty_title})"
                deadline = time.monotonic() + 7
                while time.monotonic() < deadline:
                    if launch:
                        launch.remaining()
                    try:
                        if t.eval(_KINOGO_ACCESS_DENIED, timeout=2):
                            return "Kinogo отклонил доступ к поиску. Фильм не запустился."
                        matches = _kinogo_matches(candidate, t.eval(_KINOGO_LINKS, timeout=2) or [],
                                                   season=episode[0] if episode else None)
                        if len(matches) == 1:
                            url = next(iter(matches))
                            break
                        if t.eval(empty, timeout=2):
                            break
                    except (RuntimeError, TimeoutError):
                        logger.debug("Страница результатов Kinogo ещё загружается", exc_info=True)
                    time.sleep(0.25)
                if matches:
                    break
            if not url:
                if len(matches) > 1:
                    if _film_title_year(title)[1]:
                        return f"Не удалось выбрать фильм «{title}». Kinogo показал несколько карточек. Можно указать прямую ссылку."
                    return f"Не удалось выбрать фильм «{title}». Kinogo показал несколько карточек. Уточните год или дайте ссылку."
                return f"Не удалось найти подходящую карточку для «{title}» на Kinogo. Можно указать прямую ссылку."
        series_options = {"watch_title": watch_title or title, "position": position} if episode else {}
        state = _start(url, fullscreen=True, embedded=True, launch=launch, episode=episode, **series_options)
    except TimeoutError:
        return "Не успел запустить фильм на Kinogo."
    except (OSError, RuntimeError, ValueError) as exc:
        logger.warning("Kinogo: %s", exc)
        return "Не удалось открыть или запустить фильм на Kinogo."
    finally:
        _movie_launch_lock.release()
    if not state:
        if episode:
            return f"Открыл «{title}» на Kinogo, но не удалось запустить сезон {episode[0]}, серию {episode[1]}."
        return "Открыл страницу Kinogo, но не получил доступного видеоплеера."
    if state.get("resume_error"):
        return f"Открыл «{watch_title}», но перемотать на {fmt_time(position)} не удалось. Закладка сохранена."
    if not _played(state):
        return "Плеер Kinogo открыт. Воспроизведение пока не началось."
    if launch is not None:
        launch.outcome = "success" if state.get("fs") else "partial"
    what = f"«{watch_title or title}», сезон {episode[0]}, серия {episode[1]}" if episode else "фильм"
    if position:
        return (f"Продолжил {what} с {fmt_time(position)}."
                if state.get("fs") else f"Продолжил {what} с {fmt_time(position)}. Полный экран недоступен.")
    return (f"Включил {what} на Kinogo во весь экран." if state.get("fs") else
            f"Включил {what} на Kinogo. Полный экран включить не удалось.")


# ── VK Видео ──────────────────────────────────────────────────────────────────

# Ссылки на видео со страницы результатов + длительность из карточки.
_VK_RESULTS_JS = r"""(() => {
  const out = [], seen = new Set();
  for (const a of document.querySelectorAll('a[href]')) {
    const m = a.href.match(/\/video(-?\d+_\d+)/);
    if (!m || seen.has(m[1])) continue;
    const card = a.closest('[class*="card" i], [class*="item" i], li, article') || a.parentElement;
    const text = ((card && card.innerText) || a.innerText || '').trim();
    // длительность: «2:04:47», даже вплотную к тексту («HD2:04:47»)
    const dur = (text.match(/(?<![\d:])(?:\d{1,2}:)?\d{1,2}:\d{2}(?![\d:])/) || [''])[0];
    const title = (a.getAttribute('title') || a.getAttribute('aria-label') || a.innerText || text)
                  .trim().split('\n')[0];
    seen.add(m[1]);
    out.push({id: m[1], title, dur});
  }
  return out.slice(0, 30);
})()"""


def _pick_vk(results: list[dict], query: str, film: bool) -> dict | None:
    """Лучший результат: похожее название, для фильма — длинное видео."""
    if not results:
        return None
    try:
        from rapidfuzz import fuzz
        sim = lambda t: fuzz.token_set_ratio(query.lower(), (t or "").lower())  # noqa: E731
    except ImportError:
        sim = lambda t: 100 if query.lower() in (t or "").lower() else 50  # noqa: E731

    def score(r):
        dur = parse_seconds(r.get("dur")) or 0
        s = sim(r.get("title", ""))
        if film:
            s += 40 if dur >= _FILM_MIN_SEC else (-30 if dur and dur < 20 * 60 else 0)
            if re.search(r"трейлер|trailer|тизер|обзор|разбор", r.get("title", ""), re.I):
                s -= 50
        return s
    return max(results, key=score)


def vk_find(title: str, film: bool = True) -> str | None:
    """Страница видео на VK: поиск прямо на сайте в окне Джарвиса (там вход
    пользователя и нет капчи), запасной путь — поисковик."""
    t = cdp.tab()
    q = urllib.parse.quote(title)
    if t:
        for search in (f"https://vkvideo.ru/?q={q}", f"https://vk.com/video?q={q}&section=search"):
            try:
                t.navigate(search)
                results = t.wait(_VK_RESULTS_JS, timeout=7)
            except Exception as exc:
                logger.debug("VK поиск: %s", exc)
                results = None
            best = _pick_vk(results or [], title, film)
            if best:
                return f"https://vkvideo.ru/video{best['id']}"
    from core import web_find
    return web_find.film_page(title + (" фильм" if film else ""), "vk")


_FAIL_COOLDOWN_SEC = 90.0        # столько не повторяем заведомо провальный фильм
_last_fail: tuple[str, float, str] | None = None


def _remember_fail(title: str, answer: str) -> None:
    global _last_fail
    _last_fail = (title.casefold(), time.monotonic(), answer)


def _recent_fail(title: str) -> str | None:
    """Тот же фильм только что не пошёл — не тратим ещё 18 секунд впустую."""
    if not _last_fail:
        return None
    was, when, answer = _last_fail
    if was == title.casefold() and time.monotonic() - when < _FAIL_COOLDOWN_SEC:
        return answer
    return None


def play_film(title: str, player=None, launch: MovieLaunch | None = None) -> str:
    if launch is not None:
        launch.outcome = "error"
    title = (title or "").strip()
    if not title:
        return "Какой фильм включить, сэр?"
    repeat = _recent_fail(title)
    if repeat:
        logger.info("Фильм «%s» только что не пошёл — не повторяю", title)
        return repeat
    if player:
        player.write_log(f"SYS: 🎬 VK Видео — ищу «{title}»")
    if not cdp.ensure_browser():
        return "Не нашёл Chrome или Edge, чтобы открыть VK Видео, сэр."
    url = vk_find(title)
    if not url:
        return f"Не нашёл «{title}» на VK Видео, сэр."
    try:
        st = _start(url, fullscreen=True, launch=launch) if launch else _start(url, fullscreen=True)
    except TimeoutError:
        return "Не успел запустить фильм на VK Видео."
    if not st:
        # «Открыл …, но плеер не загрузился» модель читала как «почти вышло» и
        # звала инструмент снова: в живом журнале семь заходов по 18 секунд,
        # 27 секунд молчания и убитая по таймауту сессия Gemini. Говорим прямо,
        # что повтор не поможет, и до чего это лечится.
        answer = (f"Не смог включить «{title}»: плеер VK не загрузился. "
                  "Повторять бесполезно — нужно один раз войти в VK в окне Джарвиса.")
        _remember_fail(title, answer)
        return answer
    if not _played(st):
        return f"Открыл «{title}» на VK Видео, но видео не запустилось — скажите «продолжи»."
    long = f", {_say_duration(st['d'])}" if st.get("d") else ""
    full = ", полный экран" if st.get("fs") else ""
    if launch is not None:
        launch.outcome = "success" if st.get("fs") else "partial"
    if not full:
        return f"Включил «{title}» на VK Видео{long}. Полный экран включить не удалось."
    return f"Включил «{title}» на VK Видео{full}{long}."


# ── YouTube ───────────────────────────────────────────────────────────────────

def _yt_html(url: str) -> str:
    import requests
    r = requests.get(url, headers={"User-Agent": _UA, "Accept-Language": "ru-RU,ru;q=0.9"},
                     cookies={"CONSENT": "YES+"}, timeout=5)
    r.raise_for_status()
    return r.text


def yt_search(query: str) -> str | None:
    """Первое обычное видео в выдаче (без шортсов и каналов) — id."""
    try:
        html = _yt_html("https://www.youtube.com/results?search_query=" + urllib.parse.quote(query))
        m = re.search(r'"videoRenderer":\{"videoId":"([\w-]{11})"', html)
        if m:
            return m.group(1)
    except Exception as exc:
        logger.debug("YouTube поиск (HTML): %s", exc)
    t = cdp.tab()                                    # запасной путь — в самом окне
    if not t:
        return None
    t.navigate("https://www.youtube.com/results?search_query=" + urllib.parse.quote(query))
    href = t.wait("(document.querySelector('ytd-video-renderer a#video-title') || {}).href", timeout=8)
    m = re.search(r"v=([\w-]{11})", href or "")
    return m.group(1) if m else None


def yt_latest(channel: str) -> str | None:
    """Последнее видео канала: находим канал, берём первое на его «Видео»."""
    try:
        html = _yt_html("https://www.youtube.com/results?sp=EgIQAg%253D%253D&search_query="
                        + urllib.parse.quote(channel))
        m = re.search(r'"canonicalBaseUrl":"(/@[^"]+)"', html)
        if m:
            vids = _yt_html(f"https://www.youtube.com{m.group(1)}/videos")
            v = re.search(r'"videoId":"([\w-]{11})"', vids)
            if v:
                return v.group(1)
    except Exception as exc:
        logger.debug("YouTube канал: %s", exc)
    return yt_search(f"{channel} новое видео")


def play_youtube(query: str = "", channel: str = "", player=None) -> str:
    what = (channel or query or "").strip()
    if not what:
        return "Что включить на YouTube, сэр?"
    if player:
        player.write_log(f"SYS: ▶ YouTube — {'последнее у ' + channel if channel else query}")
    if not cdp.ensure_browser():
        return "Не нашёл Chrome или Edge, чтобы открыть YouTube, сэр."
    vid = yt_latest(channel) if channel else yt_search(query)
    if not vid:
        return f"Не нашёл на YouTube «{what}», сэр."
    st = _start(f"https://www.youtube.com/watch?v={vid}", fullscreen=True)
    t = cdp.tab(create=False)
    title = ""
    if t:
        title = (t.eval("document.title") or "").replace(" - YouTube", "").strip()
    name = f"«{title}»" if title else f"«{what}»"
    if not st:
        return f"Открыл {name} на YouTube, но плеер не загрузился."
    if not _played(st):
        return f"Открыл {name} на YouTube — скажите «продолжи», если не пошло."
    return f"Включил на YouTube {name}."


# ── управление тем, что идёт ──────────────────────────────────────────────────

def pause_current() -> str:
    """Выбирает активный источник, иначе задаёт вопрос без изменения плееров."""
    from core import media_session, win_apps

    if _movie_launch_lock.locked():
        return "Плеер фильма пока загружается. Уточните, что поставить на паузу."
    tab = cdp.tab(create=False) if cdp.has_embedded_video_target() else None
    state = cdp.video_state(tab) if tab else None
    embedded_playing = bool(state and not state.get("paused", True) and state.get("ready", 0) >= 2)
    targets = media_session.playing_sessions()
    if targets is None:
        return "Не удалось проверить, что играет в Windows. Уточните, что поставить на паузу."
    if embedded_playing:
        try:
            title = (tab.eval("document.title") or "").strip().casefold().removesuffix(" - youtube")
        except (OSError, RuntimeError):
            title = ""
        # Встроенное видео нередко отражается в списке Windows как тот же ролик.
        if title:
            browser = cdp.browser_exe()
            browser_app = PureWindowsPath(browser).stem.casefold() if browser else ""
            targets = [entry for entry in targets if not (
                browser_app and entry.app.casefold() in (browser_app, browser_app + ".exe")
                and (entry.title or "").strip().casefold().removesuffix(" - youtube") == title
            )]
    window = win_apps.foreground() if targets and (embedded_playing or len(targets) > 1) else None
    if window and window.exe:
        executable = window.exe.casefold().removesuffix(".exe")
        in_front = [entry for entry in targets if entry.app.casefold() in (executable, executable + ".exe")]
        if len(in_front) == 1:
            targets, embedded_playing = in_front, False
    if embedded_playing and not targets:
        if _movie_launch_lock.locked() or not getattr(tab, "_embedded_video", False):
            return "Выбранное видео сменилось. Повторите команду."
        try:
            after = cdp.video_js("v.pause()", t=tab)
        except (RuntimeError, OSError):
            after = None
        return "Видео на паузе." if after and after.get("paused") else "Не удалось поставить это видео на паузу."
    if not embedded_playing and len(targets) == 1:
        if media_session.pause_matching(targets[0]):
            return "Поставил воспроизведение на паузу."
        return "Не удалось поставить выбранный плеер на паузу. Возможно, источник уже сменился."
    if not embedded_playing and not targets:
        return "Ничего не играет."
    return "Сейчас играет несколько источников. Уточните, что поставить на паузу."


def control(action: str, value=None) -> str:
    """Пауза, продолжить, перемотка, время, громкость, mute, полный экран…"""
    if _movie_launch_lock.locked():
        return "Плеер фильма пока недоступен."
    st = cdp.video_state()
    if not st:
        return ""                                     # видео нет — решит вызывающий
    a = (action or "").strip().lower()

    if a in ("pause", "stop"):
        st = cdp.video_js("v.pause()")
        _save_current_watch()
        return "Пауза." if st and st["paused"] else "Не получилось поставить на паузу."
    if a in ("resume", "play"):
        st = cdp.video_js("await v.play()")
        return "Продолжаю." if _played(st) else "Не получилось продолжить."
    if a == "toggle":
        st = cdp.video_js("v.paused ? await v.play() : v.pause()")
        return "Пауза." if st and st["paused"] else "Продолжаю."

    if a in ("seek_forward", "seek_back"):
        sec = parse_seconds(value, 10) or 10
        sign = 1 if a == "seek_forward" else -1
        st = cdp.video_js(f"v.currentTime = Math.max(0, Math.min(v.duration || 1e9, v.currentTime + {sign * sec}))")
        if not st:
            return "Не получилось перемотать видео."
        _save_current_watch()
        return f"{'Вперёд' if sign > 0 else 'Назад'} на {_say_duration(sec)} — {fmt_time(st['t'])}."
    if a == "seek_to":
        sec = parse_seconds(value)
        if sec is None:
            return "На какое время перемотать, сэр?"
        st = cdp.video_js(f"v.currentTime = Math.min(v.duration || 1e9, {sec})")
        if not st:
            return "Не получилось перемотать видео."
        _save_current_watch()
        return f"Перемотал на {fmt_time(st['t'])}."
    if a == "restart":
        st = cdp.video_js("v.currentTime = 0; await v.play()")
        return "С начала." if _played(st) and st["t"] < 1 else "Не получилось начать с начала."

    if a == "time":
        if not st.get("d"):
            return f"Идёт {fmt_time(st['t'])}."
        left = st["d"] - st["t"]
        return (f"Идёт {fmt_time(st['t'])} из {fmt_time(st['d'])}, "
                f"осталось {_say_duration(left)}.")

    if a in ("volume_up", "volume_down", "volume_set"):
        from actions.computer_settings import parse_level
        cur = round(st["vol"] * 100)
        if a == "volume_set":
            new = parse_level(value, 50)
        else:
            step = parse_level(value, 15) or 15
            new = min(100, cur + step) if a == "volume_up" else max(0, cur - step)
        st = cdp.video_js(f"v.muted = false; v.volume = {new / 100}")
        if not st:
            return "Не получилось изменить громкость видео."
        return f"Громкость видео {round(st['vol'] * 100)}%."
    if a == "mute":
        st = cdp.video_js("v.muted = true")
        return "Звук видео выключен." if st and st["muted"] else "Не получилось выключить звук видео."
    if a == "unmute":
        st = cdp.video_js("v.muted = false; if (v.volume < 0.05) v.volume = 0.5")
        return "Звук видео включён." if st and not st["muted"] else "Не получилось включить звук видео."

    if a == "fullscreen":
        # Видео открыто в панели рядом с шаром — сначала окно на экран,
        # иначе полный экран случился бы в окне за его краем.
        try:
            from core import browser_panel
            browser_panel.release_for_video()
        except Exception as exc:
            logger.debug("Панель браузера: %s", exc)
        cdp.bring_to_front()
        ok = cdp.fullscreen(True)
        return "Полный экран." if ok else "Не получилось развернуть на весь экран."
    if a == "exit_fullscreen":
        ok = cdp.fullscreen(False)
        return "Вышел из полного экрана." if ok else "Не удалось выйти из полного экрана."
    if a == "speed":
        rate = parse_seconds(value, 1.0) or 1.0
        rate = max(0.25, min(3.0, rate))
        cdp.video_js(f"v.playbackRate = {rate}")
        return f"Скорость ×{rate:g}."
    if a == "next":
        t = cdp.tab(create=False)
        ok = t and t.eval("(b => b ? (b.click(), true) : false)(document.querySelector('.ytp-next-button'))")
        return "Следующее видео." if ok else "Тут нет следующего видео."
    if a in ("close", "exit"):
        _save_current_watch()
        cdp.fullscreen(False)
        cdp.video_js("v.pause()")
        t = cdp.tab(create=False)
        if t:
            t.navigate("about:blank")
        return "Закрыл видео."
    return f"Не понял команду для видео: «{action}»."


def video_playing() -> bool:
    st = cdp.video_state() if cdp.running() else None
    return bool(st) and not st.get("paused", True)
