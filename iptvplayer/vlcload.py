"""Carrega o libVLC: primeiro o VLC embutido (pasta vlc/), depois o VLC instalado."""
import os
import sys
from pathlib import Path

from .paths import BASE

VLC_MISSING_MSG = ("O VLC (64 bits) não foi encontrado neste computador.\n\n"
                   "Instale gratuitamente em https://www.videolan.org/vlc/ e abra o IPTV Player de novo.")


def _load():
    if sys.platform == "win32":
        for d in (BASE / "vlc", Path(r"C:\Program Files\VideoLAN\VLC")):
            if (d / "libvlc.dll").exists():
                os.add_dll_directory(str(d))
                os.environ["PYTHON_VLC_LIB_PATH"] = str(d / "libvlc.dll")
                if (d / "plugins").is_dir():
                    os.environ["PYTHON_VLC_MODULE_PATH"] = str(d / "plugins")
                break
    try:
        import vlc as mod
        if getattr(mod, "dll", None) is None:
            return None
        return mod
    except (ImportError, OSError, NotImplementedError):
        return None


vlc = _load()
