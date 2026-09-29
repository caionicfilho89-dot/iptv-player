"""Pastas do programa e dos dados do usuário."""
import os
import sys
from pathlib import Path

FROZEN = getattr(sys, "frozen", False)
# pasta do programa: ao lado do .exe quando empacotado, raiz do projeto no código-fonte
BASE = Path(sys.executable).resolve().parent if FROZEN else Path(__file__).resolve().parent.parent


def _writable(d):
    try:
        d.mkdir(parents=True, exist_ok=True)
        probe = d / ".write_test"
        probe.write_text("ok")
        probe.unlink()
        return True
    except OSError:
        return False


# modo portátil (dados ao lado do programa); se a pasta for protegida, usa %LOCALAPPDATA%
DATA_DIR = BASE if _writable(BASE) else Path(os.environ.get("LOCALAPPDATA", Path.home())) / "IPTV Player"
CACHE = DATA_DIR / ".cache"
LOGO_CACHE = CACHE / "logos"
LIST_CACHE = CACHE / "lists"
EPG_CACHE = CACHE / "epg"
CONFIG_FILE = DATA_DIR / "player_config.json"
for _d in (LOGO_CACHE, LIST_CACHE, EPG_CACHE):
    _d.mkdir(parents=True, exist_ok=True)
