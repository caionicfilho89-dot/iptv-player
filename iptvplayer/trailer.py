"""Janela do trailer: abre por cima da página do filme para o usuário ver o trailer
enquanto decide. Tem o player de vídeo próprio (VLC), então não atrapalha o que já estava tocando."""
import time
import webbrowser

import vlc
from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QGuiApplication
from PyQt6.QtWidgets import (QHBoxLayout, QLabel, QSizeGrip, QVBoxLayout, QWidget)

from . import youtube
from .log import log
from .theme import T
from .widgets import VideoFrame, make_btn, refresh_icons, set_btn_icon

CONNECT_TIMEOUT = 25   # s esperando o trailer começar antes de desistir


class _Header(QWidget):
    dragStart = pyqtSignal()

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.dragStart.emit()


class TrailerWindow(QWidget):
    """Criada uma vez e reaproveitada. instance = a mesma libvlc do programa."""

    def __init__(self, instance):
        super().__init__(None, Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool)
        self.setWindowTitle("Pudim — trailer")
        self.setMinimumSize(360, 260)
        self.instance = instance
        self.player = instance.media_player_new()
        self.player.audio_set_mute(False)
        self._req = 0            # id do pedido atual (ignora trailer antigo que chega atrasado)
        self._page_url = ""      # link do vídeo no YouTube (botão "abrir no YouTube")
        self._started = 0.0

        self.resolver = youtube.TrailerResolver()
        self.resolver.resolved.connect(self._on_resolved)
        self.resolver.failed.connect(self._on_failed)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.header = _Header(objectName="trHeader")
        self.header.setFixedHeight(36)
        self.header.setCursor(Qt.CursorShape.SizeAllCursor)
        self.header.dragStart.connect(lambda: self.windowHandle().startSystemMove())
        h = QHBoxLayout(self.header)
        h.setContentsMargins(12, 2, 6, 2)
        h.setSpacing(2)
        self.title = QLabel("Trailer")
        self.title.setObjectName("trTitle")
        h.addWidget(self.title, 1)
        self.yt_btn = make_btn("globe", "Abrir no YouTube", self._open_youtube, kind="pipbtn",
                               icon_size=16, icon_color="white")
        h.addWidget(self.yt_btn)
        h.addWidget(make_btn("close", "Fechar o trailer", self.stop, kind="pipbtn",
                             icon_size=16, icon_color="white"))
        lay.addWidget(self.header)

        self.video = VideoFrame(min_size=(320, 180))
        self.video.doubleClicked.connect(self._toggle_fullscreenish)
        lay.addWidget(self.video, 1)

        bar = QWidget(objectName="trBar")
        bar.setFixedHeight(42)
        b = QHBoxLayout(bar)
        b.setContentsMargins(10, 4, 10, 4)
        b.setSpacing(6)
        self.play_btn = make_btn("pause", "Pausar", self._toggle_play, kind="pipbtn",
                                 icon_size=18, icon_color="white")
        self.mute_btn = make_btn("volume", "Mudo", self._toggle_mute, kind="pipbtn",
                                 icon_size=18, icon_color="white")
        b.addWidget(self.play_btn)
        b.addWidget(self.mute_btn)
        b.addStretch(1)
        grip = QSizeGrip(bar)
        grip.setFixedSize(14, 14)
        b.addWidget(grip)
        lay.addWidget(bar)

        self._poll = QTimer(self, timeout=self._tick)
        self.apply_style()
        scr = QGuiApplication.primaryScreen().availableGeometry()
        self.resize(640, 440)
        self.move(scr.center().x() - 320, scr.center().y() - 220)

    def apply_style(self):
        self.setStyleSheet(f"""
            QWidget {{ background: #0b0d12; }}
            QWidget#trHeader {{ background: qlineargradient(x1:0,y1:0,x2:1,y2:0,
                stop:0 {T['panel']}, stop:1 {T['surface']}); }}
            QLabel#trTitle {{ color: {T['text']}; font-weight: 700; }}
            QWidget#trBar {{ background: {T['panel']}; }}
        """)
        refresh_icons(self)

    # ------------------------------------------------------------------ abrir
    def open(self, title, year="", trailer_id=""):
        self._req += 1
        self._page_url = ""
        self.title.setText(f"Trailer — {title}" if title else "Trailer")
        self.video.set_message("Procurando o trailer…")
        set_btn_icon(self.play_btn, "pause", "white")
        self.play_btn.setToolTip("Pausar")
        self.show()
        self.raise_()
        self.activateWindow()
        self.player.set_hwnd(int(self.video.winId()))
        self.resolver.start(self._req, trailer_id, title, year)

    def _on_resolved(self, key, url, vtitle, page):
        if key != self._req or not self.isVisible():
            return
        self._page_url = page
        if vtitle:
            self.title.setText(f"Trailer — {vtitle}")
        self.video.set_message("Carregando…")
        media = self.instance.media_new(url)
        media.add_option(":network-caching=2500")
        self.player.set_media(media)
        self.player.play()
        self._started = time.monotonic()
        self._poll.start(400)

    def _on_failed(self, key, reason):
        if key != self._req or not self.isVisible():
            return
        self._poll.stop()
        self.video.set_message(f"Não consegui abrir o trailer.\n{reason}")

    def _tick(self):
        st = self.player.get_state()
        if st == vlc.State.Playing and self.player.has_vout():
            self.video.set_message("")
            self._poll.stop()
        elif st in (vlc.State.Error, vlc.State.Ended):
            self._poll.stop()
            self.video.set_message("O trailer terminou ou não abriu."
                                   if st == vlc.State.Ended else "Não consegui tocar o trailer.")
        elif time.monotonic() - self._started > CONNECT_TIMEOUT:
            self._poll.stop()
            self.video.set_message("O trailer está demorando demais.")

    # ------------------------------------------------------------------ controles
    def _toggle_play(self):
        self.player.pause()
        paused = self.player.get_state() == vlc.State.Paused
        set_btn_icon(self.play_btn, "play" if paused else "pause", "white")
        self.play_btn.setToolTip("Continuar" if paused else "Pausar")

    def _toggle_mute(self):
        self.player.audio_toggle_mute()
        muted = self.player.audio_get_mute() == 1
        set_btn_icon(self.mute_btn, "mute" if muted else "volume", "white")

    def _toggle_fullscreenish(self):
        if self.isMaximized():
            self.showNormal()
        else:
            self.showMaximized()

    def _open_youtube(self):
        if self._page_url:
            try:
                webbrowser.open(self._page_url)
            except Exception as e:  # noqa: BLE001
                log.info("Trailer: não abriu o navegador (%s)", e)

    def stop(self):
        self._req += 1          # invalida qualquer resolução em andamento
        self._poll.stop()
        try:
            self.player.stop()
        except Exception:  # noqa: BLE001
            pass
        self.hide()

    def closeEvent(self, e):
        self.stop()
        e.ignore()              # fechar = só esconder e parar; a janela é reaproveitada
