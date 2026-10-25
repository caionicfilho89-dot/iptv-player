"""Tela Jogos: a agenda esportiva (hoje e amanhã) tirada do guia, com Assistir e Avisar em cada jogo."""
import time

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QCheckBox, QFrame, QHBoxLayout, QLabel, QScrollArea, QVBoxLayout, QWidget

from . import sports
from .theme import T
from .widgets import make_btn, set_btn_icon

WEEKDAYS = ("segunda", "terça", "quarta", "quinta", "sexta", "sábado", "domingo")


def day_label(ts):
    today = time.localtime()
    d = time.localtime(ts)
    delta = (time.mktime((d.tm_year, d.tm_mon, d.tm_mday, 0, 0, 0, 0, 0, -1)) -
             time.mktime((today.tm_year, today.tm_mon, today.tm_mday, 0, 0, 0, 0, 0, -1))) // 86400
    if delta == 0:
        return "Hoje"
    if delta == 1:
        return "Amanhã"
    return f"{WEEKDAYS[d.tm_wday].capitalize()}, {time.strftime('%d/%m', d)}"


def status_text(game, now):
    if game.start <= now < game.stop:
        return "● AGORA", T["bad"]
    mins = int((game.start - now) // 60)
    if mins < 60:
        return f"em {max(1, mins)} min", T["accent"]
    return "", T["muted"]


class GameRow(QFrame):
    def __init__(self, view, game, channels):
        super().__init__(objectName="gameRow")
        self.view, self.game, self.channels = view, game, channels
        self.setStyleSheet(f"QFrame#gameRow {{ background: {T['surface']}; border: 1px solid {T['border']};"
                           " border-radius: 12px; }} QFrame#gameRow QLabel {{ background: transparent; }}")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 10, 12, 10)
        lay.setSpacing(14)
        when = QLabel(time.strftime("%H:%M", time.localtime(game.start)))
        when.setStyleSheet(f"font-size: 15pt; font-weight: 800; color: {T['text']};")
        when.setFixedWidth(64)
        lay.addWidget(when)
        mid = QVBoxLayout()
        mid.setSpacing(2)
        title = QLabel(f"{game.emoji}  {game.teams}")
        title.setStyleSheet(f"font-size: 12.5pt; font-weight: 700; color: {T['text']};")
        title.setWordWrap(True)
        mid.addWidget(title)
        now = time.time()
        st, color = status_text(game, now)
        bits = [game.competition or game.sport_name]
        names = [c.name for c in channels[:3]]
        if names:
            bits.append(", ".join(names) + (f" +{len(channels) - 3}" if len(channels) > 3 else ""))
        else:
            bits.append("canal fora das suas listas")
        if game.live:
            bits.append("AO VIVO")
        if game.replay:
            bits.append("reprise")
        sub = QLabel("  ·  ".join(bits) + (f"   <span style='color:{color}; font-weight:700'>{st}</span>"
                                            if st else ""))
        sub.setTextFormat(Qt.TextFormat.RichText)
        sub.setStyleSheet(f"color: {T['muted']};")
        sub.setWordWrap(True)
        mid.addWidget(sub)
        lay.addLayout(mid, 1)
        self.watch = make_btn("play", "Assistir no canal", self._watch, kind="ghost", text="  Assistir",
                              icon_size=16)
        self.watch.setEnabled(bool(channels))
        lay.addWidget(self.watch)
        self.bell = make_btn("bell", "Avisar quando for começar", self._bell, kind="ghost", text="  Avisar",
                             icon_size=16)
        self.bell.setVisible(game.start > now)
        lay.addWidget(self.bell)
        self.refresh_bell()

    def refresh_bell(self):
        on = self.view.page.win.game_alert_on(self.game)
        set_btn_icon(self.bell, "bell", "accent" if on else "text")
        self.bell.setText("  Vou avisar" if on else "  Avisar")

    def _watch(self):
        self.view.page.win.watch_game(self.game, self.channels)

    def _bell(self):
        self.view.page.win.toggle_game_alert(self.game)
        self.refresh_bell()


class SportsView(QWidget):
    def __init__(self, page):
        super().__init__()
        self.page = page
        self.sport = None            # filtro de esporte (None = todos)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 8, 18, 0)
        lay.setSpacing(8)
        head = QHBoxLayout()
        self.title = QLabel("Jogos", objectName="h1")
        head.addWidget(self.title)
        self.count = QLabel("", objectName="muted")
        head.addWidget(self.count)
        head.addStretch(1)
        self.mine = QCheckBox("Só nos meus canais")
        self.mine.setToolTip("Mostrar só os jogos que passam em canais das suas listas")
        self.mine.toggled.connect(self._mine_changed)
        head.addWidget(self.mine)
        self.replays = QCheckBox("Mostrar reprises")
        self.replays.toggled.connect(lambda: self.refresh())
        head.addWidget(self.replays)
        lay.addLayout(head)
        from .browse import ChipBar   # aqui dentro: browse importa este módulo
        self.chips = ChipBar()
        self.chips.chosen.connect(self._chip)
        lay.addWidget(self.chips)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.verticalScrollBar().setSingleStep(40)
        self.body = QWidget(objectName="homeBody")
        self.list = QVBoxLayout(self.body)
        self.list.setContentsMargins(0, 4, 10, 24)
        self.list.setSpacing(8)
        self.scroll.setWidget(self.body)
        lay.addWidget(self.scroll, 1)

    def _mine_changed(self, on):
        self.page.win.cfg["sports_mine"] = on
        self.refresh()

    def _chip(self, path):
        self.sport = path[0] if path and path[0] != "*" else None
        self.refresh(keep_chips=True)

    def refresh(self, keep_chips=False):
        win = self.page.win
        self.mine.blockSignals(True)
        self.mine.setChecked(win.cfg["sports_mine"])
        self.mine.blockSignals(False)
        games = [(g, chs) for g, chs in win.sport_games()
                 if (self.replays.isChecked() or not g.replay) and (chs or not self.mine.isChecked())]
        kinds = []
        for g, _c in games:
            if g.sport not in kinds:
                kinds.append(g.sport)
        if not keep_chips:
            if self.sport not in kinds:
                self.sport = None
            order = [k for k, *_r in sports.SPORTS if k in kinds]
            chips = [("Todos", ("*",))] + [(f"{sports.SPORT_INFO[k][1]} {sports.SPORT_INFO[k][0]}", (k,))
                                           for k in order]
            self.chips.set_chips(chips, (self.sport or "*",))
            self.chips.setVisible(len(chips) > 2)
        shown = [(g, c) for g, c in games if self.sport in (None, g.sport)]
        while self.list.count():
            item = self.list.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        if not win.epg.has_data():
            msg = "O guia de programação ainda está carregando…" if win.cfg["epg_enabled"] else \
                "Ligue o guia de programação nas Configurações para ver os jogos."
            self.list.addWidget(QLabel(msg, objectName="muted"))
        elif not shown:
            self.list.addWidget(QLabel("Nenhum jogo encontrado no guia para hoje e amanhã"
                                       + (" nos seus canais." if self.mine.isChecked() else "."), objectName="muted"))
        day = None
        for g, chs in shown:
            d = day_label(g.start)
            if d != day:
                day = d
                lbl = QLabel(d, objectName="h2")
                lbl.setContentsMargins(2, 10, 0, 2)
                self.list.addWidget(lbl)
            self.list.addWidget(GameRow(self, g, chs))
        self.list.addStretch(1)
        n = len(shown)
        self.count.setText(f"   {n} jogo{'s' if n != 1 else ''} hoje e amanhã" if n else "")
