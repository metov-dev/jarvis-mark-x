"""Экран «Кино» (core/kino.py): три варианта с доски 28.11 в одном.

Сверху — крупно то, что выбрано (по умолчанию последнее): «Продолжить»,
«Следующая серия», сетка серий сезона (посмотрено / сейчас / следующая).
Ниже — вкладки «Смотрю / Хочу посмотреть / Досмотрел» списком. Клик по
строке выбирает её наверх. Всё запускается тем же плеером, что и голосом.
"""
from __future__ import annotations

import logging
import threading
import zlib

from PyQt6.QtCore import QRectF, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QLinearGradient, QPainter
from PyQt6.QtWidgets import (QButtonGroup, QDialog, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit,
                             QPushButton, QScrollArea, QVBoxLayout, QWidget)

from ui import C
from ui_icons import qicon
from ui_kit import STYLE, IconBadge, Progress, _label, _line

logger = logging.getLogger(__name__)

EXTRA = f"""
QFrame#row {{ background: transparent; border: none; border-radius: 10px; }}
QFrame#row:hover {{ background: {C.PANEL2}; }}
QFrame#row[sel="true"] {{ background: {C.PRI_GHO}; }}
QLabel#tag {{ color: {C.ACC}; font-size: 11px; font-weight: 700; letter-spacing: 1px; }}
QLabel#big {{ color: {C.WHITE}; font-size: 24px; font-weight: 700; }}
QLabel#where {{ color: {C.TEXT_MED}; font-size: 12.5px; }}
QLabel#rowt {{ color: {C.WHITE}; font-weight: 600; }}
QPushButton#ep {{ border: 1px solid {C.BORDER}; border-radius: 8px; padding: 0; min-width: 34px; min-height: 34px;
  max-width: 34px; max-height: 34px; color: {C.TEXT_DIM}; background: {C.PANEL2}; font-weight: 600; }}
QPushButton#ep[kind="seen"] {{ background: {C.PRI_GHO}; color: {C.PRI_DIM}; border-color: {C.PRI_GHO}; }}
QPushButton#ep[kind="now"] {{ background: {C.PRI}; color: {C.BG}; border-color: {C.PRI}; }}
QPushButton#ep:hover {{ border-color: {C.PRI_DIM}; color: {C.PRI}; }}
"""

_TABS = (("watching", "Смотрю"), ("want", "Хочу посмотреть"), ("done", "Досмотрел"))


def _tint(title: str) -> QColor:
    """Цвет «постера» от названия: у каждого сериала свой и всегда тот же."""
    hue = zlib.crc32(title.casefold().encode("utf-8")) % 360
    return QColor.fromHsv(hue, 120, 110)


class Poster(QWidget):
    """Место постера: цветная плашка с названием (настоящих картинок пока нет)."""

    def __init__(self, w: int, h: int, font_px: int = 15):
        super().__init__()
        self.setFixedSize(w, h)
        self.title = ""
        self._px = font_px

    def set_title(self, title: str):
        self.title = title
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect())
        g = QLinearGradient(0, 0, r.width() * 0.4, r.height())
        g.setColorAt(0, _tint(self.title or "?"))
        g.setColorAt(0.75, QColor(C.DARK))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(g)
        p.drawRoundedRect(r, 8, 8)
        f = QFont("Segoe UI", -1, QFont.Weight.Bold)
        f.setPixelSize(self._px)
        p.setFont(f)
        p.setPen(QColor(C.WHITE))
        small = r.width() < 60                    # в строке списка — только буква, название рядом
        text = (self.title[:1].upper() if small else self.title)
        if small:
            f.setPixelSize(int(r.height() * 0.4))
            p.setFont(f)
        p.drawText(r.adjusted(6, 6, -6, -6), (Qt.AlignmentFlag.AlignCenter if small else
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom) | Qt.TextFlag.TextWordWrap, text)


class KinoDialog(QDialog):
    _loaded = pyqtSignal(object)
    _status = pyqtSignal(str)

    def __init__(self, parent=None, auto_refresh: bool = True):
        super().__init__(parent)
        from core import kino
        self.K = kino
        self.setWindowTitle("ДЖАРВИС — кино")
        self.setStyleSheet(STYLE + EXTRA)
        self.resize(880, 760)
        self.items: list = []
        self.tab = "watching"
        self.sel_title = ""
        self._busy = False
        self._loaded.connect(self._apply)
        self._status.connect(self._set_status)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._header())
        root.addWidget(_line())
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        canvas = QWidget()
        canvas.setObjectName("canvas")
        outer = QHBoxLayout(canvas)
        outer.setContentsMargins(0, 0, 0, 0)
        col_w = QWidget()
        col_w.setMaximumWidth(860)
        self.col = QVBoxLayout(col_w)
        self.col.setContentsMargins(28, 22, 28, 28)
        self.col.setSpacing(14)
        self.col.addWidget(self._hero())
        self.col.addWidget(self._tabs())
        self.list_card = QFrame()
        self.list_card.setObjectName("card")
        self.list_lay = QVBoxLayout(self.list_card)
        self.list_lay.setContentsMargins(10, 8, 10, 8)
        self.list_lay.setSpacing(2)
        self.col.addWidget(self.list_card)
        self.col.addWidget(_label("Голосом то же: «Джарвис, продолжи Игру престолов», «включи следующую серию», "
                                  "«добавь Дюну в хочу посмотреть», «я досмотрел Менталиста».", "hint"))
        self.col.addStretch(1)
        outer.addStretch(1)
        outer.addWidget(col_w, 100)
        outer.addStretch(1)
        scroll.setWidget(canvas)
        root.addWidget(scroll, 1)
        self._tmr = QTimer(self)
        self._tmr.timeout.connect(self.refresh)
        self._auto = auto_refresh

    # ── вид ─────────────────────────────────────────────────────────────────
    def _header(self) -> QWidget:
        w = QFrame()
        w.setObjectName("bar")
        lay = QHBoxLayout(w)
        lay.setContentsMargins(24, 14, 24, 14)
        lay.setSpacing(14)
        lay.addWidget(IconBadge("play", 38))
        col = QVBoxLayout()
        col.setSpacing(1)
        col.addWidget(_label("Джарвис", "brand"))
        col.addWidget(_label("Кино", "h1", wrap=False))
        lay.addLayout(col)
        lay.addStretch(1)
        self.add_edit = QLineEdit()
        self.add_edit.setPlaceholderText("Хочу посмотреть… (Enter)")
        self.add_edit.setFixedWidth(240)
        self.add_edit.returnPressed.connect(self._add_want)
        lay.addWidget(self.add_edit)
        return w

    def _hero(self) -> QWidget:
        card = QFrame()
        card.setObjectName("card")
        lay = QHBoxLayout(card)
        lay.setContentsMargins(18, 18, 18, 18)
        lay.setSpacing(18)
        self.poster = Poster(150, 210, 18)
        lay.addWidget(self.poster, 0, Qt.AlignmentFlag.AlignTop)
        info = QVBoxLayout()
        info.setSpacing(6)
        self.tag = _label("ПРОДОЛЖИТЬ", "tag", wrap=False)
        self.big = _label("", "big")
        self.where = _label("", "where")
        self.bar = Progress()
        self.bar.setMaximumWidth(380)
        info.addWidget(self.tag)
        info.addWidget(self.big)
        info.addWidget(self.where)
        info.addWidget(self.bar)
        info.addSpacing(6)
        self.eps_cap = _label("", "hint")
        info.addWidget(self.eps_cap)
        self.eps = QGridLayout()
        self.eps.setHorizontalSpacing(6)
        self.eps.setVerticalSpacing(6)
        self.eps.setContentsMargins(0, 0, 0, 0)
        eps_w = QWidget()
        eps_w.setLayout(self.eps)
        info.addWidget(eps_w, 0, Qt.AlignmentFlag.AlignLeft)
        info.addSpacing(4)
        acts = QHBoxLayout()
        acts.setSpacing(8)
        self.play_btn = QPushButton("  Продолжить")
        self.play_btn.setObjectName("primary")
        self.play_btn.setIcon(qicon("play", 12, C.BG))
        self.play_btn.clicked.connect(self._play)
        self.next_btn = QPushButton("Следующая серия")
        self.next_btn.clicked.connect(self._next)
        self.done_btn = QPushButton("Досмотрел")
        self.done_btn.clicked.connect(lambda: self._mark(self.K.done))
        self.drop_btn = QPushButton("Убрать")
        self.drop_btn.setObjectName("danger")
        self.drop_btn.clicked.connect(lambda: self._mark(self.K.remove))
        for b in (self.play_btn, self.next_btn, self.done_btn, self.drop_btn):
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            acts.addWidget(b)
        acts.addStretch(1)
        info.addLayout(acts)
        self.status = _label("", "status")
        info.addWidget(self.status)
        info.addStretch(1)
        lay.addLayout(info, 1)
        self.hero_card = card
        return card

    def _tabs(self) -> QWidget:
        seg = QFrame()
        seg.setObjectName("seg")
        lay = QHBoxLayout(seg)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.setSpacing(4)
        self.tab_btns: dict[str, QPushButton] = {}
        group = QButtonGroup(self)
        for key, name in _TABS:
            b = QPushButton(name)
            b.setObjectName("seg")
            b.setCheckable(True)
            b.setChecked(key == self.tab)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(lambda _c=False, k=key: self._pick_tab(k))
            group.addButton(b)
            lay.addWidget(b)
            self.tab_btns[key] = b
        lay.addStretch(1)
        return seg

    @staticmethod
    def _clear(lay):
        while lay.count():
            it = lay.takeAt(0)
            if it.widget():
                it.widget().deleteLater()

    def _row(self, s) -> QFrame:
        row = QFrame()
        row.setObjectName("row")
        row.setProperty("sel", s.title == self.sel_title)
        lay = QHBoxLayout(row)
        lay.setContentsMargins(10, 8, 10, 8)
        lay.setSpacing(12)
        pst = Poster(40, 56, 9)
        pst.set_title(s.title)
        lay.addWidget(pst)
        mid = QVBoxLayout()
        mid.setSpacing(3)
        mid.addWidget(_label(s.title, "rowt"))
        if s.where():
            mid.addWidget(_label(s.where(), "where"))
        if s.duration:
            bar = Progress()
            bar.value = s.progress
            bar.setMaximumWidth(300)
            mid.addWidget(bar)
        lay.addLayout(mid, 1)
        play = QPushButton()
        play.setObjectName("ghost")
        play.setIcon(qicon("play", 14, C.PRI))
        play.setToolTip("Включить")
        play.setCursor(Qt.CursorShape.PointingHandCursor)
        play.clicked.connect(lambda _c=False, t=s.title: (self._select(t), self._play()))
        lay.addWidget(play)
        row.setCursor(Qt.CursorShape.PointingHandCursor)
        row.mouseReleaseEvent = lambda _e, t=s.title: self._select(t)
        return row

    # ── данные ──────────────────────────────────────────────────────────────
    def showEvent(self, ev):
        super().showEvent(ev)
        if self._auto:
            self.refresh()
            self._tmr.start(30_000)

    def hideEvent(self, ev):
        super().hideEvent(ev)
        self._tmr.stop()

    def refresh(self):
        def work():
            try:
                data = self.K.shows()
            except Exception as exc:
                logger.warning("Кино, экран: %s", exc)
                data = []
            self._loaded.emit(data)
        threading.Thread(target=work, daemon=True, name="kino-page").start()

    def _apply(self, items):
        self.items = list(items or [])
        titles = [s.title for s in self.items]
        if self.sel_title not in titles:
            watching = [s for s in self.items if s.status == "watching"]
            self.sel_title = (watching or self.items or [None])[0].title if self.items else ""
        for key, name in _TABS:
            n = sum(1 for s in self.items if s.status == key)
            self.tab_btns[key].setText(f"{name} · {n}" if n else name)
        self._render_hero()
        self._render_list()

    def _current(self):
        return next((s for s in self.items if s.title == self.sel_title), None)

    def _render_hero(self):
        s = self._current()
        self.hero_card.setVisible(True)
        if not s:
            self.poster.set_title("")
            self.tag.setText("ПОКА ПУСТО")
            self.big.setText("Ничего не смотрите")
            self.where.setText("Скажите «Джарвис, включи сериал …» — он появится здесь с местом, где остановились.")
            self.bar.hide()
            for b in (self.play_btn, self.next_btn, self.done_btn, self.drop_btn):
                b.hide()
            self._render_eps(None)
            return
        self.poster.set_title(s.title)
        self.tag.setText({"watching": "ПРОДОЛЖИТЬ", "want": "ХОЧУ ПОСМОТРЕТЬ", "done": "ДОСМОТРЕЛ"}[s.status])
        self.big.setText(s.title)
        self.where.setText(s.where() or ("Ещё не начинали" if s.status == "want" else ""))
        self.bar.setVisible(bool(s.duration))
        self.bar.value = s.progress
        self.bar.update()
        self.play_btn.show()
        self.play_btn.setText("  Продолжить" if s.status == "watching" else "  Смотреть")
        self.next_btn.setVisible(s.status == "watching" and s.is_series)
        self.done_btn.setVisible(s.status != "done")
        self.drop_btn.show()
        self._render_eps(s)

    def _render_eps(self, s):
        self._clear(self.eps)
        if not s or not s.is_series or s.status != "watching":
            self.eps_cap.hide()
            return
        # Сколько серий в сезоне, плеер не знает — показываем правду:
        # посмотренные, текущую и следующую.
        self.eps_cap.setText(f"Сезон {s.season}")
        self.eps_cap.show()
        last = s.episode + 1
        for n in range(1, last + 1):
            b = QPushButton(str(n))
            b.setObjectName("ep")
            kind = "seen" if n < s.episode else "now" if n == s.episode else "next"
            b.setProperty("kind", kind)
            b.setToolTip({"seen": f"Серия {n} — посмотрено", "now": f"Серия {n} — сейчас",
                          "next": f"Серия {n} — следующая"}[kind])
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(lambda _c=False, ep=n: self._play_episode(ep))
            self.eps.addWidget(b, (n - 1) // 12, (n - 1) % 12)

    def _render_list(self):
        self._clear(self.list_lay)
        rows = [s for s in self.items if s.status == self.tab]
        if not rows:
            empty = {"watching": "Здесь будет то, что вы смотрите через Джарвиса.",
                     "want": "Пусто. Впишите название сверху или скажите «добавь … в хочу посмотреть».",
                     "done": "Пока ничего не досмотрено."}[self.tab]
            w = _label(empty, "hint")
            w.setContentsMargins(10, 8, 10, 8)
            self.list_lay.addWidget(w)
            return
        for s in rows:
            self.list_lay.addWidget(self._row(s))

    # ── действия ────────────────────────────────────────────────────────────
    def _pick_tab(self, key: str):
        self.tab = key
        self._render_list()

    def _select(self, title: str):
        self.sel_title = title
        self._render_hero()
        self._render_list()

    def _set_status(self, text: str):
        self._busy = False
        self.status.setText(text)
        self.refresh()

    def _run(self, label: str, params: dict):
        """Тот же плеер, что голосом; в фоне — поиск на Киного идёт секунды."""
        if self._busy:
            return
        self._busy = True
        self.status.setText(label)

        def work():
            try:
                from actions.movie_player import movie_player
                text = movie_player(params)
            except Exception as exc:
                logger.warning("Кино: %s", exc)
                text = f"Не получилось: {exc}"
            self._status.emit(text)
        threading.Thread(target=work, daemon=True, name="kino-play").start()

    def _play(self):
        s = self._current()
        if not s:
            return
        if s.status == "watching":
            self._run(f"Продолжаю «{s.title}»…", {"action": "series_continue", "title": s.title})
        else:
            self._run(f"Ищу «{s.title}»…", {"action": "play", "title": s.title})

    def _next(self):
        s = self._current()
        if s:
            self._run(f"Включаю следующую серию «{s.title}»…", {"action": "series_next", "title": s.title})

    def _play_episode(self, ep: int):
        s = self._current()
        if s:
            self._run(f"Включаю серию {ep}…", {"action": "play", "title": s.title,
                                              "season": s.season, "episode": ep})

    def _mark(self, fn):
        s = self._current()
        if not s:
            return
        try:
            self.status.setText(fn(s.title))
        except Exception as exc:
            self.status.setText(f"Не сохранилось: {exc}")
        self.sel_title = ""
        self.refresh()

    def _add_want(self):
        title = self.add_edit.text().strip()
        if not title:
            return
        try:
            self.status.setText(self.K.want(title))
        except Exception as exc:
            self.status.setText(f"Не сохранилось: {exc}")
        self.add_edit.clear()
        self.sel_title = title
        self.tab = "want"
        self.tab_btns["want"].setChecked(True)
        self.refresh()
