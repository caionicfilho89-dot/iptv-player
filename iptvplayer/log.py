"""Registro de erros: iptvplayer.log na pasta de dados, para descobrir por que algo não funcionou."""
import faulthandler
import logging
import platform
import sys
import threading
from logging.handlers import RotatingFileHandler

from .paths import DATA_DIR, FROZEN

LOG_FILE = DATA_DIR / "iptvplayer.log"
CRASH_FILE = DATA_DIR / "queda.log"   # pilha gravada pelo faulthandler quando o programa cai
log = logging.getLogger("iptv")
_crash_fp = None


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
    _watch_native_crashes()


def _watch_native_crashes():
    """Quedas no código nativo (VLC, placa de vídeo) não passam pelos ganchos do Python. O faulthandler
    grava a pilha de todas as tarefas num arquivo à parte (o registro principal é renomeado ao girar);
    na abertura seguinte o conteúdo vai para o registro."""
    global _crash_fp
    try:
        old = CRASH_FILE.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        old = ""
    if old:
        lines = old.splitlines()
        log.critical("O programa caiu na última vez. Pilha das tarefas:\n%s", "\n".join(lines[-300:]))
    try:
        _crash_fp = open(CRASH_FILE, "w", encoding="utf-8")
        faulthandler.enable(_crash_fp, all_threads=True)
    except OSError:
        pass
