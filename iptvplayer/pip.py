"""Janela flutuante (picture-in-picture), sempre visível sobre as outras."""
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QGuiApplication
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QSizeGrip, QVBoxLayout, QWidget

from .theme import T
from .widgets import VideoFrame, make_btn, refresh_icons


class PipHeader(QWidget):
    dragStart = pyqtSignal()
    doubleClicked = pyqtSignal()

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.dragStart.emit()

    def mouseDoubleClickEvent(self, e):
        self.doubleClicked.emit()


class PipWindow(QWidget):
    restore = pyqtSignal()     # volta para a janela principal
    close_stop = pyqtSignal()  # fecha e para o canal
    prev = pyqtSignal()
    next = pyqtSignal()

    def __init__(self):
        super().__init__(None, Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool)
        self.setWindowTitle("IPTV Player — mini")
        self.setMinimumSize(280, 190)
        self.closing = False  # True quando o próprio app fecha a janela
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.header = PipHeader()
        self.header.setObjectName("pipHeader")
        self.header.setFixedHeight(34)
        self.header.setCursor(Qt.CursorShape.SizeAllCursor)
        self.header.dragStart.connect(lambda: self.windowHandle().startSystemMove())
        self.header.doubleClicked.connect(self.restore.emit)
        h = QHBoxLayout(self.header)
        h.setContentsMargins(10, 2, 4, 2)
        h.setSpacing(2)
        self.title = QLabel("")
        h.addWidget(self.title, 1)
        for name, tip, sig in (("prev", "Canal anterior", self.prev), ("next", "Próximo canal", self.next),
                               ("maximize", "Voltar para a janela principal", self.restore),
                               ("close", "Fechar e parar", self.close_stop)):
            h.addWidget(make_btn(name, tip, sig.emit, kind="pipbtn", icon_size=16, icon_color="white"))
        lay.addWidget(self.header)
        self.video = VideoFrame(min_size=(240, 135))
        self.video.doubleClicked.connect(self.restore.emit)
        lay.addWidget(self.video, 1)
        grip_row = QHBoxLayout()
        grip_row.setContentsMargins(0, 0, 0, 0)
        grip_row.addStretch(1)
        grip = QSizeGrip(self)
        grip.setFixedSize(14, 14)
        grip_row.addWidget(grip)
        lay.addLayout(grip_row)
        self.apply_style()
        scr = QGuiApplication.primaryScreen().availableGeometry()
        self.setGeometry(scr.right() - 500, scr.bottom() - 330, 480, 310)

    def apply_style(self):
        self.setStyleSheet(f"""
            QWidget {{ background: #0b0d12; }}
            QWidget#pipHeader {{ background: qlineargradient(x1:0,y1:0,x2:1,y2:0,
                stop:0 {T['accent']}, stop:1 {T['accent2']}); }}
            QLabel {{ color: white; font-weight: 600; background: transparent; }}
            QPushButton#pipbtn {{ background: transparent; border: none; border-radius: 6px;
                min-width: 28px; max-width: 28px; min-height: 26px; max-height: 26px; padding: 0; }}
            QPushButton#pipbtn:hover {{ background: rgba(255,255,255,0.22); }}
        """)
        refresh_icons(self)

    def closeEvent(self, e):
        if not self.closing:  # fechada pelo usuário (Alt+F4): volta para a janela principal
            self.closing = True
            self.close_stop.emit()
        super().closeEvent(e)

    def set_title(self, text):
        self.title.setText(self.title.fontMetrics().elidedText(text, Qt.TextElideMode.ElideRight, 200))
