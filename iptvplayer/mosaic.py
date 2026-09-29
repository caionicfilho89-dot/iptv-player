"""Mosaico: vários canais ao mesmo tempo (2x2 ou 3x3)."""
import time

from PyQt6.QtCore import QTimer, pyqtSignal
from PyQt6.QtWidgets import QComboBox, QFrame, QGridLayout, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from .theme import T, app_icon
from .vlcload import vlc
from .theme import icon
from .widgets import VideoFrame, make_btn

CONNECT_TIMEOUT = 15


class TileHeader(QWidget):
    clicked = pyqtSignal()
    doubleClicked = pyqtSignal()

    def mousePressEvent(self, e):
        self.clicked.emit()

    def mouseDoubleClickEvent(self, e):
        self.doubleClicked.emit()


class MosaicTile(QFrame):
    focus_me = pyqtSignal(object)
    open_me = pyqtSignal(object)
    next_me = pyqtSignal(object)

    def __init__(self, instance):
        super().__init__(objectName="tile")
        self.ch = None
        self.instance = instance
        self.player = instance.media_player_new()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(2, 2, 2, 2)
        lay.setSpacing(0)
        self.header = TileHeader(objectName="tileHeader")
        self.header.setFixedHeight(32)
        self.header.clicked.connect(lambda: self.focus_me.emit(self))
        self.header.doubleClicked.connect(lambda: self.open_me.emit(self))
        h = QHBoxLayout(self.header)
        h.setContentsMargins(10, 0, 4, 0)
        h.setSpacing(2)
        self.audio_icon = QLabel()
        h.addWidget(self.audio_icon)
        self.title = QLabel("—")
        h.addWidget(self.title, 1)
        h.addWidget(make_btn("next", "Trocar por outro canal", lambda: self.next_me.emit(self),
                             kind="tilebtn", icon_size=15, icon_color="white"))
        h.addWidget(make_btn("maximize", "Assistir este na tela principal", lambda: self.open_me.emit(self),
                             kind="tilebtn", icon_size=15, icon_color="white"))
        lay.addWidget(self.header)
        self.video = VideoFrame(min_size=(200, 112))
        self.video.doubleClicked.connect(lambda: self.open_me.emit(self))
        lay.addWidget(self.video, 1)
        self.player.set_hwnd(int(self.video.winId()))
        self.player.video_set_mouse_input(False)
        self.player.video_set_key_input(False)

    def play(self, ch):
        self.ch = ch
        self.title.setText(ch.name)
        media = self.instance.media_new(ch.url)
        for o in ch.opts:
            media.add_option(o)
        self.player.set_media(media)
        self.player.play()
        self.started = time.monotonic()
        self.video.set_message("Conectando…")

    def failed(self):
        st = self.player.get_state()
        if st in (vlc.State.Error, vlc.State.Ended):
            return True
        if st == vlc.State.Playing and self.player.has_vout():
            self.video.set_message("")
            return False
        return time.monotonic() - self.started > CONNECT_TIMEOUT

    def set_audio(self, on):
        self.player.audio_set_mute(not on)
        self.setProperty("active", on)
        self.style().unpolish(self)
        self.style().polish(self)
        self.audio_icon.setPixmap(icon("volume" if on else "mute", "white", 15).pixmap(15, 15))

    def release(self):
        self.player.stop()
        self.player.release()


class MosaicWindow(QWidget):
    closed = pyqtSignal(object)   # canal escolhido para a tela principal (ou None)
    channel_failed = pyqtSignal(str)

    def __init__(self, instance, pool, size=2, volume=80):
        super().__init__()
        self.setWindowTitle("IPTV Player — Mosaico")
        self.setWindowIcon(app_icon())
        self.instance = instance
        self.pool = pool
        self.volume = volume
        self.tiles = []
        self.active = None
        self.chosen = None
        self.used = set()
        self.failed_urls = set()
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 8, 10, 10)
        root.setSpacing(8)
        bar = QHBoxLayout()
        bar.addWidget(QLabel("Mosaico", objectName="h2"))
        hint = QLabel("Clique no nome para ouvir o canal · duplo clique para assistir em tela grande",
                      objectName="muted")
        bar.addWidget(hint, 1)
        self.size_box = QComboBox()
        self.size_box.addItems(["2 × 2  (4 canais)", "3 × 3  (9 canais)", "1 × 2  (2 canais)"])
        self.size_box.setCurrentIndex({2: 0, 3: 1, 1: 2}.get(size, 0))
        self.size_box.currentIndexChanged.connect(lambda i: self._build([2, 3, 1][i]))
        bar.addWidget(self.size_box)
        bar.addWidget(make_btn("close", "Fechar mosaico", self.close, kind="tool"))
        root.addLayout(bar)
        self.grid = QGridLayout()
        self.grid.setSpacing(6)
        root.addLayout(self.grid, 1)
        self.apply_style()
        self._build(size)
        self.monitor = QTimer(self, interval=1000, timeout=self._tick)
        self.monitor.start()

    def apply_style(self):
        self.setStyleSheet(f"""
            QFrame#tile {{ background: #000; border: 2px solid {T['border']}; border-radius: 8px; }}
            QFrame#tile[active="true"] {{ border: 2px solid {T['accent']}; }}
            QWidget#tileHeader {{ background: #141821; border-top-left-radius: 6px; border-top-right-radius: 6px; }}
            QFrame#tile[active="true"] QWidget#tileHeader {{ background: qlineargradient(x1:0,y1:0,x2:1,y2:0,
                stop:0 {T['accent']}, stop:1 {T['accent2']}); }}
            QWidget#tileHeader QLabel {{ color: white; font-weight: 600; background: transparent; }}
            QPushButton#tilebtn {{ background: transparent; border: none; border-radius: 6px;
                min-width: 26px; max-width: 26px; min-height: 24px; max-height: 24px; padding: 0; }}
            QPushButton#tilebtn:hover {{ background: rgba(255,255,255,0.2); }}
        """)

    @property
    def size(self):
        return {0: 2, 1: 3, 2: 1}[self.size_box.currentIndex()]

    def _build(self, size):
        for t in self.tiles:
            t.release()
            t.setParent(None)
            t.deleteLater()
        self.tiles, self.used = [], set()
        rows, cols = (1, 2) if size == 1 else (size, size)
        for i in range(rows * cols):
            t = MosaicTile(self.instance)
            t.focus_me.connect(self._focus)
            t.open_me.connect(self._open)
            t.next_me.connect(self._next)
            self.grid.addWidget(t, i // cols, i % cols)
            self.tiles.append(t)
        for t in self.tiles:
            self._next(t)
        self._focus(self.tiles[0])

    def _pick(self, after=None):
        n = len(self.pool)
        if not n:
            return None
        start = self.pool.index(after) + 1 if after in self.pool else 0
        for k in range(n):
            ch = self.pool[(start + k) % n]
            if ch.url not in self.used and ch.url not in self.failed_urls:
                return ch
        return None

    def _next(self, tile):
        ch = self._pick(tile.ch)
        if tile.ch:
            self.used.discard(tile.ch.url)
        if ch:
            self.used.add(ch.url)
            tile.play(ch)
            tile.player.audio_set_volume(self.volume)
            tile.set_audio(tile is self.active)
        else:
            tile.video.set_message("Sem mais canais disponíveis")

    def _focus(self, tile):
        self.active = tile
        for t in self.tiles:
            t.set_audio(t is tile)

    def _open(self, tile):
        self.chosen = tile.ch
        self.close()

    def _tick(self):
        for t in self.tiles:
            if t.ch and t.failed():
                self.failed_urls.add(t.ch.url)
                self.channel_failed.emit(t.ch.url)
                self._next(t)

    def closeEvent(self, e):
        self.monitor.stop()
        for t in self.tiles:
            t.release()
        self.closed.emit(self.chosen)
        super().closeEvent(e)
