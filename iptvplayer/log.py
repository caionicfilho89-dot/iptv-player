"""Registro de erros: iptvplayer.log na pasta de dados, para descobrir por que algo não funcionou."""
import logging
import platform
import sys
import threading
from logging.handlers import RotatingFileHandler

from .paths import DATA_DIR, FROZEN

LOG_FILE = DATA_DIR / "iptvplayer.log"
log = logging.getLogger("iptv")


def setup(version):
    if log.handlers:
        return
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s [%(threadName)s] %(message)s", "%Y-%m-%d %H:%M:%S")
    try:
        h = RotatingFileHandler(LOG_FILE, maxBytes=1_000_000, backupCount=1, encoding="utf-8")
        h.setFormatter(fmt)
        log.addHandler(h)
    except OSError:
        pass  # pasta sem permissão: segue sem arquivo
    if not FROZEN and sys.stderr:
        s = logging.StreamHandler()
        s.setFormatter(fmt)
        log.addHandler(s)
    log.setLevel(logging.INFO)

    # erros não tratados vão para o registro em vez de fechar o programa sem explicação
    def hook(etype, value, tb):
        log.critical("Erro não tratado", exc_info=(etype, value, tb))
    sys.excepthook = hook
    threading.excepthook = lambda a: log.critical(
        f"Erro não tratado na tarefa {a.thread.name if a.thread else '?'}",
        exc_info=(a.exc_type, a.exc_value, a.exc_traceback))
    log.info("IPTV Player %s iniciado — Windows %s, Python %s", version, platform.version(),
             platform.python_version())
