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


class TrailerError(Exception):
    pass


def resolve_trailer(trailer_id="", title="", year=""):
    """Endereço tocável do trailer. Usa o id do YouTube que a conta mandou; se não houver,
    procura no YouTube pelo nome do filme. Devolve (endereço, título do vídeo, link da página)."""
    try:
        import yt_dlp
    except ImportError:
        raise TrailerError("esta versão não abre trailers (falta o yt-dlp)") from None
    if trailer_id:
        target = f"https://www.youtube.com/watch?v={trailer_id}"
    elif title:
        termo = f"{title} {year} trailer".strip()
        target = f"ytsearch1:{termo} oficial"
    else:
        raise TrailerError("sem trailer para este título")
    # Sem pedir um formato: o YouTube moderno manda vídeo e áudio separados (precisariam do ffmpeg).
    # O VLC toca sozinho o manifesto HLS/DASH, que já traz imagem e som juntos.
    opts = {"quiet": True, "no_warnings": True, "noplaylist": True, "skip_download": True,
            "logger": _Mute()}
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(target, download=False, process=False)
            if "entries" in info:   # resultado de busca ou /live: resolve o primeiro de verdade
                first = next((x for x in info["entries"] if x), None)
                if not first:
                    raise TrailerError("não achei um trailer para este filme")
                info = ydl.extract_info(first.get("url") or first.get("id"),
                                        download=False, process=True)
    except TrailerError:
        raise
    except Exception as e:  # noqa: BLE001
        raise TrailerError(re.sub(r"^ERROR:\s*(\[[^\]]+\]\s*)*", "", str(e))[:120]) from None
    url = _playable_format(info.get("formats") or [])
    if not url:
        raise TrailerError("o YouTube não entregou o vídeo")
    return url, info.get("title") or title, info.get("webpage_url") or target


def _playable_format(formats):
    """O melhor jeito do VLC tocar sem ffmpeg: 1) formato já com vídeo+áudio; 2) o manifesto HLS/DASH."""
    muxed = [f for f in formats if f.get("acodec") not in (None, "none")
             and f.get("vcodec") not in (None, "none") and f.get("url")]
    if muxed:
        muxed.sort(key=lambda f: f.get("height") or 0)
        pick = next((f for f in muxed if (f.get("height") or 0) <= 720), muxed[-1])
        return pick["url"]
    return next((f["manifest_url"] for f in formats if f.get("manifest_url")), None)


class TrailerResolver(QObject):
    """Resolve o trailer em segundo plano (leva uns 2-4 s) e avisa a janela."""
    resolved = pyqtSignal(object, str, str, str)   # chave, endereço, título do vídeo, link da página
    failed = pyqtSignal(object, str)               # chave, motivo

    def start(self, key, trailer_id="", title="", year=""):
        threading.Thread(target=self._work, args=(key, trailer_id, title, year),
                         daemon=True, name="trailer").start()

    def _work(self, key, trailer_id, title, year):
        try:
            url, vtitle, page = resolve_trailer(trailer_id, title, year)
            self.resolved.emit(key, url, vtitle, page)
        except TrailerError as e:
            log.info("Trailer: %s (%s / %s)", e, trailer_id, title)
            self.failed.emit(key, str(e))


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
