"""Lives do YouTube: o VLC do programa não abre páginas do YouTube, então o yt-dlp acha o endereço do vídeo
(lista HLS com todas as qualidades) e o VLC toca esse endereço."""
import re
import threading
import time

from PyQt6.QtCore import QObject, pyqtSignal

from .log import log

_YT_RE = re.compile(r"^https?://(?:www\.|m\.)?(?:youtube\.com|youtu\.be)/", re.IGNORECASE)
_EXPIRE_RE = re.compile(r"/expire/(\d+)")
LIVE_MARK = b'"isLiveNow":true'   # aparece na página do canal quando ele está ao vivo

_cache = {}                        # link do YouTube -> (endereço do vídeo, vale até)


def is_youtube(url):
    return bool(_YT_RE.match(url or ""))


def playable(url):
    """Para quem abre o link direto no VLC (mosaico, gravação): o vídeo já achado, se houver."""
    hit = _cache.get(url)
    return hit[0] if hit and hit[1] > time.time() else url


class _Mute:
    def debug(self, msg):
        pass

    info = warning = error = debug


class NotLive(Exception):
    pass


def resolve(url):
    """Endereço HLS da live (ou NotLive). Fica guardado até perto de expirar (umas 6 h)."""
    hit = _cache.get(url)
    if hit and hit[1] > time.time():
        return hit[0]
    try:
        import yt_dlp
    except ImportError:
        raise NotLive("esta versão não abre YouTube (falta o yt-dlp)") from None
    # o programa instalado não tem console (stderr é None): as mensagens do yt-dlp vão para um registro mudo
    opts = {"quiet": True, "no_warnings": True, "noplaylist": True, "skip_download": True, "logger": _Mute()}
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False, process=False)
            if info.get("_type") == "url":  # /live do canal aponta para o vídeo que está passando
                info = ydl.extract_info(info["url"], download=False, process=False)
    except Exception as e:  # noqa: BLE001 — o yt-dlp levanta vários tipos
        msg = str(e)
        if "not currently live" in msg or "will begin" in msg or "Premieres" in msg:
            raise NotLive("o canal não está ao vivo agora") from None
        raise NotLive(re.sub(r"^ERROR:\s*(\[[^\]]+\]\s*)*", "", msg)[:120]) from None
    if info.get("live_status") not in (None, "is_live"):
        raise NotLive("o canal não está ao vivo agora")
    manifest = next((f.get("manifest_url") for f in info.get("formats") or [] if f.get("manifest_url")), None)
    if not manifest:
        raise NotLive("o YouTube não entregou o vídeo")
    m = _EXPIRE_RE.search(manifest)
    _cache[url] = (manifest, (int(m.group(1)) if m else time.time() + 3600) - 600)
    return manifest


class Resolver(QObject):
    """Resolve em segundo plano (leva uns 2-3 s) e avisa a janela."""
    resolved = pyqtSignal(str, str)   # link do YouTube, endereço do vídeo
    failed = pyqtSignal(str, str)     # link do YouTube, motivo

    def start(self, url):
        hit = _cache.get(url)
        if hit and hit[1] > time.time():
            self.resolved.emit(url, hit[0])
            return
        threading.Thread(target=self._work, args=(url,), daemon=True, name="youtube").start()

    def _work(self, url):
        try:
            self.resolved.emit(url, resolve(url))
        except NotLive as e:
            log.info("YouTube: %s (%s)", e, url)
            self.failed.emit(url, str(e))
