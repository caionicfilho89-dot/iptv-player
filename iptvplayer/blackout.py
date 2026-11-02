"""Apagar a tela do PC: todos os monitores ficam pretos (e o Windows desliga a luz deles) enquanto o filme passa
na TV; qualquer tecla ou clique volta. O Pudim continua rodando por baixo, mandando o vídeo para a TV.
Também serve para o "dormir quando o filme acabar": mostra o aviso, apaga e, se ninguém mexer, o PC dorme."""
import ctypes

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor, QPainter
from PyQt6.QtWidgets import QApplication, QWidget

from .log import log

WM_SYSCOMMAND, SC_MONITORPOWER, MONITOR_OFF = 0x0112, 0xF170, 2
HINT_MS = 2500        # o aviso fica na tela antes de apagar de vez
DEFAULT_HINT = "Tela apagada · o filme continua na TV\nAperte qualquer tecla ou clique para voltar"


class _Black(QWidget):
    def __init__(self, screen, owner, text):
        super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
                         | Qt.WindowType.Tool)
        self.owner = owner
        self.text = text
        self.hint = True
        self.setCursor(Qt.CursorShape.BlankCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setGeometry(screen.geometry())

    def paintEvent(self, _e):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("black"))
        if self.hint:
            p.setPen(QColor(110, 110, 110))
            f = p.font()
            f.setPointSize(16)
            p.setFont(f)
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self.text)

    def keyPressEvent(self, _e):
        self.owner.wake()

    def mousePressEvent(self, _e):
        self.owner.wake()


class Blackout:
    def __init__(self):
        self.windows = []
        self.on_wake = None

    @property
    def active(self):
        return bool(self.windows)

    def start(self, text=DEFAULT_HINT, on_wake=None):
        if self.windows:
            return
        self.on_wake = on_wake
        for screen in QApplication.screens():
            w = _Black(screen, self, text)
            w.showFullScreen()
            w.setGeometry(screen.geometry())   # showFullScreen às vezes escolhe o monitor principal
            self.windows.append(w)
        self.windows[0].activateWindow()
        self.windows[0].setFocus()
        QTimer.singleShot(HINT_MS, self._dark)
        log.info("Tela do PC apagada (%d monitor(es))", len(self.windows))

    def _dark(self):
        if not self.windows:
            return
        for w in self.windows:
            w.hint = False
            w.update()
        QApplication.processEvents()
        try:   # pede ao Windows para desligar a luz dos monitores (volta sozinho com tecla ou mouse)
            ctypes.windll.user32.PostMessageW(int(self.windows[0].winId()), WM_SYSCOMMAND, SC_MONITORPOWER,
                                              MONITOR_OFF)
        except (AttributeError, OSError):
            pass   # fora do Windows: fica só a tela preta

    def wake(self):
        windows, self.windows = self.windows, []
        for w in windows:
            w.close()
        if windows:
            log.info("Tela do PC acesa de novo")
            callback, self.on_wake = self.on_wake, None
            if callback:
                callback()


def sleep_pc():
    """Põe o PC para dormir (suspender)."""
    log.info("Pondo o PC para dormir: o filme na TV acabou")
    try:
        ctypes.windll.powrprof.SetSuspendState(0, 1, 0)
    except (AttributeError, OSError) as e:
        log.warning("Não deu para pôr o PC para dormir: %s", e)
