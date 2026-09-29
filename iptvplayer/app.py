"""Ponto de entrada."""
import sys

from PyQt6.QtWidgets import QApplication, QMessageBox

from .config import Config
from .theme import apply_theme, build_style
from .vlcload import VLC_MISSING_MSG, vlc


def main():
    if sys.platform == "win32":
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("iptvplayer.app")
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    cfg = Config()
    apply_theme(cfg["theme"], cfg["accent"])
    app.setStyleSheet(build_style())
    if vlc is None:
        QMessageBox.critical(None, "IPTV Player", VLC_MISSING_MSG)
        sys.exit(1)
    from .mainwindow import MainWindow
    w = MainWindow(cfg)
    w.show()
    sys.exit(app.exec())
