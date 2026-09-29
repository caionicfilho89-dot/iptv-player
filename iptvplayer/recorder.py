"""Gravação de canais e fotos da tela."""
import re
import time
from pathlib import Path

from PyQt6.QtCore import QStandardPaths

from .vlcload import vlc


def _user_dir(kind, sub="IPTV Player"):
    loc = {"videos": QStandardPaths.StandardLocation.MoviesLocation,
           "pictures": QStandardPaths.StandardLocation.PicturesLocation}[kind]
    d = Path(QStandardPaths.writableLocation(loc) or Path.home()) / sub
    d.mkdir(parents=True, exist_ok=True)
    return d


def safe_name(name):
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", name).strip()[:60] or "canal"


def snapshot_path(ch):
    return _user_dir("pictures") / f"{safe_name(ch.name)}_{time.strftime('%Y-%m-%d_%H-%M-%S')}.png"


class Recorder:
    """Grava o canal num player paralelo, sem imagem nem som (VLC 3 não grava pelo player principal)."""

    def __init__(self, instance):
        self.instance = instance
        self.player = None
        self.ch = None
        self.path = None
        self.started = 0

    @property
    def active(self):
        return self.player is not None

    def start(self, ch):
        self.stop()
        self.path = _user_dir("videos") / f"{safe_name(ch.name)}_{time.strftime('%Y-%m-%d_%H-%M-%S')}.ts"
        media = self.instance.media_new(ch.url)
        for o in ch.opts:
            media.add_option(o)
        dst = self.path.as_posix().replace('"', "")
        media.add_option(f':sout=#std{{access=file,mux=ts,dst="{dst}"}}')
        media.add_option(":sout-keep")
        self.player = self.instance.media_player_new()
        self.player.set_media(media)
        self.player.play()
        self.ch = ch
        self.started = time.monotonic()
        return self.path

    def stop(self):
        if not self.player:
            return None
        self.player.stop()
        self.player.release()
        self.player = None
        path, self.path = self.path, None
        return path

    def elapsed(self):
        return int(time.monotonic() - self.started) if self.active else 0

    def failed(self):
        return self.active and self.player.get_state() in (vlc.State.Error, vlc.State.Ended)

    def size(self):
        try:
            return self.path.stat().st_size if self.path else 0
        except OSError:
            return 0
