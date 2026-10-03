"""Ponto de entrada."""
import sys

from PyQt6.QtWidgets import QApplication, QMessageBox

from . import APP_VERSION
from .config import Config
from .log import log, setup as setup_log
from .theme import apply_theme, build_style
from .vlcload import VLC_MISSING_MSG, vlc


def main():
    if sys.platform == "win32":
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("iptvplayer.app")
    setup_log(APP_VERSION)
    if "--autoteste" in sys.argv:
        from .selftest import run
        sys.exit(0 if run() else 1)
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    cfg = Config()
    apply_theme(cfg["theme"], cfg["accent"])
    app.setStyleSheet(build_style())
    if vlc is None:
        log.error("VLC não encontrado")
        QMessageBox.critical(None, "IPTV Player", VLC_MISSING_MSG)
        sys.exit(1)
    from .mainwindow import MainWindow
    w = MainWindow(cfg)
    w.showMaximized() if w.start_maximized else w.show()
    code = app.exec()
    log.info("Programa fechado")
    sys.exit(code)
