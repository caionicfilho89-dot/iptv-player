"""Comparar títulos: uma janela com até 4 cartões lado a lado (capa, nota, ano, gênero, duração, elenco e sinopse),
para decidir o que assistir. Os títulos indicados pelo Pudinho abrem aqui."""
import random

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from .catalog import CatalogNode, display_title
from .net import LogoLoader
from .theme import T
from .widgets import make_btn

MAX_CARDS = 4
SPIN_STEPS = 14          # sorteio: quantas vezes o destaque pula de cartão antes de parar
CARD_W = 260
POSTER = (180, 260)


def _duration(text):
    """"02:00:14" -> "2h00"; "95 min" e outros formatos ficam como vieram."""
    parts = str(text).split(":")
    if len(parts) == 3 and all(p.isdigit() for p in parts):
        h, m = int(parts[0]), int(parts[1])
        return f"{h}h{m:02d}" if h else f"{m} min"
    return str(text)


def _title(e):
    return display_title(e.label if isinstance(e, CatalogNode) else e.name)


class _Card(QFrame):
    def __init__(self, win, e, on_close):
        super().__init__(objectName="cmpCard")
        self.win, self.e = win, e
        self.setFixedWidth(CARD_W)
        v = QVBoxLayout(self)
        v.setContentsMargins(12, 10, 12, 12)
        v.setSpacing(6)
        top = QHBoxLayout()
        kind = QLabel("SÉRIE" if isinstance(e, CatalogNode) else "FILME", objectName="cmpKind")
        top.addWidget(kind)
        top.addStretch(1)
        top.addWidget(make_btn("close", "Tirar da comparação", lambda: on_close(self), kind="tool", icon_size=14))
        v.addLayout(top)
        self.poster = QLabel(alignment=Qt.AlignmentFlag.AlignCenter)
        self.poster.setFixedHeight(POSTER[1])
        v.addWidget(self.poster)
        self.name = QLabel(_title(e), objectName="cmpTitle", wordWrap=True)
        v.addWidget(self.name)
        self.facts = QLabel("", objectName="cmpFacts", wordWrap=True)
        v.addWidget(self.facts)
        self.cast = QLabel("", objectName="cmpMuted", wordWrap=True)
        v.addWidget(self.cast)
        self.plot = QLabel("", objectName="cmpPlot", wordWrap=True)
        self.plot.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        v.addWidget(self.plot, 1)
        row = QHBoxLayout()
        watch = QPushButton("▶ Assistir", objectName="cmpWatch")
        watch.setCursor(Qt.CursorShape.PointingHandCursor)
        watch.clicked.connect(self.watch)
        page = QPushButton("Ver página", objectName="cmpPage")
        page.setCursor(Qt.CursorShape.PointingHandCursor)
        page.clicked.connect(self.open_page)
        row.addWidget(watch, 1)
        row.addWidget(page, 1)
        v.addLayout(row)
        self.refresh()

    def refresh(self):
        home = self.win.home
        info = home.info_of(self.e, fetch=True) or {}
        facts = []
        if info.get("rating"):
            facts.append(f"★ {float(info['rating']):.1f}")
        if info.get("year"):
            facts.append(str(info["year"]))
        if info.get("duration"):
            facts.append(_duration(info["duration"]))
        if info.get("genre"):
            facts.append(info["genre"])
        self.facts.setText(" · ".join(facts) or "Sem detalhes ainda…")
        cast = [c.strip() for c in (info.get("cast") or "").split(",") if c.strip()][:4]
        self.cast.setText("Com: " + ", ".join(cast) if cast else "")
        self.cast.setVisible(bool(cast))
        plot = " ".join((info.get("plot") or "").split())
        self.plot.setText(plot[:650] + ("…" if len(plot) > 650 else "") if plot else "Sem sinopse.")
        self.load_poster()

    def load_poster(self):
        url = self.win.home.image_of(self.e)
        if not url:
            self.poster.setText("sem capa")
            return
        self.win.logos.get(url)   # baixa (ou já tem no disco); avisa por logos.loaded
        f = LogoLoader._file(url)
        pm = QPixmap(str(f)) if f.exists() else QPixmap()
        if pm.isNull():
            self.poster.setText("carregando capa…")
            return
        self.poster.setPixmap(pm.scaled(*POSTER, Qt.AspectRatioMode.KeepAspectRatio,
                                        Qt.TransformationMode.SmoothTransformation))

    def watch(self):
        home, e = self.win.home, self.e
        if isinstance(e, CatalogNode):
            return self.open_page()   # série: escolhe o episódio na página
        self.win.show_player() if hasattr(self.win, "show_player") else None
        home.play_movie(e, home.catalog.path_of(e))

    def open_page(self):
        self.win.showNormal() if self.win.isMinimized() else None
        self.win.go_home()
        self.win.home.activate(self.e, None)
        self.win.activateWindow()


class CompareWindow(QWidget):
    HEAD = "Comparar títulos  ·  clique em mais títulos no Pudinho para pôr lado a lado (até 4)"

    def __init__(self, win):
        super().__init__(win, Qt.WindowType.Window)
        self.win = win
        self.setWindowTitle("Comparar títulos · Pudim")
        self.setObjectName("cmpWindow")
        self.cards = []
        v = QVBoxLayout(self)
        v.setContentsMargins(14, 12, 14, 14)
        top = QHBoxLayout()
        self.head = QLabel(self.HEAD, objectName="cmpHead")
        top.addWidget(self.head, 1)
        self.spin_btn = QPushButton("🎲  Não consigo decidir", objectName="cmpSpin")
        self.spin_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.spin_btn.setToolTip("O Pudim sorteia um dos títulos para vocês")
        self.spin_btn.clicked.connect(self.spin)
        top.addWidget(self.spin_btn)
        v.addLayout(top)
        self._spin_timer = QTimer(self, singleShot=True, timeout=self._spin_step)
        self._spin_left, self._spin_at, self._spin_end = 0, 0, 0
        self.row = QHBoxLayout()
        self.row.setSpacing(12)
        self.row.addStretch(1)
        v.addLayout(self.row, 1)
        self.setStyleSheet(f"""
            #cmpWindow {{ background: {T['bg']}; }}
            #cmpHead {{ color: {T['muted']}; padding-bottom: 6px; }}
            #cmpCard {{ background: {T['surface']}; border: 1px solid {T['border']}; border-radius: 12px; }}
            #cmpCard[pick="true"] {{ border: 3px solid {T['accent']}; }}
            QPushButton#cmpSpin {{ background: {T['surface2']}; color: {T['text']}; border: 1px solid {T['border']};
                                   border-radius: 14px; padding: 6px 14px; font-weight: 600; }}
            QPushButton#cmpSpin:hover {{ border-color: {T['accent']}; }}
            #cmpKind {{ color: {T['accent']}; font-size: 10px; font-weight: 700; letter-spacing: 1px; }}
            #cmpTitle {{ color: {T['strong']}; font-size: 15px; font-weight: 700; }}
            #cmpFacts {{ color: {T['star']}; }}
            #cmpMuted {{ color: {T['muted']}; font-size: 11px; }}
            #cmpPlot {{ color: {T['text']}; }}
            QPushButton#cmpWatch {{ background: {T['accent']}; color: white; border: none; border-radius: 8px;
                                    padding: 7px; font-weight: 600; }}
            QPushButton#cmpPage {{ background: transparent; color: {T['text']}; border: 1px solid {T['border']};
                                   border-radius: 8px; padding: 7px; }}
        """)
        win.logos.loaded.connect(self._poster_ready)
        win.meta.movie_ready.connect(self._info_ready)
        win.meta.series_ready.connect(lambda: [c.refresh() for c in self.cards])

    def add(self, e):
        for c in self.cards:
            if c.e is e:
                return self._show()
        if len(self.cards) >= MAX_CARDS:   # cheio: sai o mais antigo
            self._remove(self.cards[0])
        card = _Card(self.win, e, self._remove)
        self.cards.append(card)
        self.row.insertWidget(self.row.count() - 1, card)
        self._spin_reset()
        self._show()

    def _remove(self, card):
        if card in self.cards:
            self.cards.remove(card)
            card.deleteLater()
        self._spin_reset()
        if not self.cards:
            self.hide()
        else:
            self.adjustSize()

    def _show(self):
        self.adjustSize()
        self.resize(max(self.sizeHint().width(), 320), max(self.height(), 640))
        self.show()
        self.raise_()
        self.activateWindow()

    # ---- sorteio ("Não consigo decidir")
    def _mark(self, chosen):
        for c in self.cards:
            c.setProperty("pick", c is chosen)
            c.style().unpolish(c)
            c.style().polish(c)

    def _spin_reset(self):
        self._spin_timer.stop()
        self._mark(None)
        self.head.setText(self.HEAD)
        self.spin_btn.setVisible(len(self.cards) >= 2)
        self.spin_btn.setEnabled(True)

    def spin(self):
        if len(self.cards) < 2:
            return
        self._spin_end = random.randrange(len(self.cards))
        # dá algumas voltas e para exatamente no sorteado
        self._spin_left = SPIN_STEPS + (self._spin_end - SPIN_STEPS) % len(self.cards)
        self._spin_at = 0
        self.spin_btn.setEnabled(False)
        self.head.setText("🎲  Sorteando…")
        self._spin_step()

    def _spin_step(self):
        if not self.cards:
            return
        self._mark(self.cards[self._spin_at % len(self.cards)])
        if self._spin_left <= 0:
            winner = self.cards[self._spin_at % len(self.cards)]
            self.head.setText(f"🏆  O Pudim escolheu: {_title(winner.e)}! É só apertar ▶ Assistir.")
            self.spin_btn.setEnabled(True)
            return
        self._spin_left -= 1
        self._spin_at += 1
        self._spin_timer.start(70 + (SPIN_STEPS + 4 - min(self._spin_left, SPIN_STEPS + 4)) * 22)   # vai freando

    def _poster_ready(self, url):
        for c in self.cards:
            if self.win.home.image_of(c.e) == url:
                c.load_poster()

    def _info_ready(self, url):
        for c in self.cards:
            if not isinstance(c.e, CatalogNode) and c.e.url == url:
                c.refresh()
