"""Rede: logos, teste de canais e download de listas."""
import hashlib
import threading
import time
import urllib.request

from PyQt6.QtCore import QObject, Qt, QUrl, pyqtSignal
from PyQt6.QtGui import QPixmap
from PyQt6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest

from . import xtream
from .discovery import list_urls
from .log import log
from .paths import LOGO_CACHE

USER_AGENT = b"VLC/3.0.21 LibVLC/3.0.21"


def make_request(url, ch=None, timeout=8000, user_agent=USER_AGENT):
    req = QNetworkRequest(QUrl(url))
    req.setTransferTimeout(timeout)
    req.setRawHeader(b"User-Agent", user_agent)
    for o in (ch.opts if ch else []):
        if o.startswith(":http-user-agent="):
            req.setRawHeader(b"User-Agent", o.split("=", 1)[1].encode())
        elif o.startswith(":http-referrer="):
            req.setRawHeader(b"Referer", o.split("=", 1)[1].encode())
    return req


def http_ok(reply):
    code = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
    return bool(code) and 200 <= int(code) < 400


class LogoLoader(QObject):
    """Baixa logos sob demanda (só dos itens visíveis) com cache em disco."""
    loaded = pyqtSignal(str)
    MAX_ACTIVE = 8

    def __init__(self, parent=None):
        super().__init__(parent)
        self.nam = QNetworkAccessManager(self)
        self.pix, self.failed, self.pending, self.queue, self.active = {}, set(), set(), [], 0

    @staticmethod
    def _file(url):
        return LOGO_CACHE / (hashlib.md5(url.encode()).hexdigest() + ".img")

    @staticmethod
    def _scale(pm):
        return pm.scaled(240, 150, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)

    def get(self, url):
        if not url or url in self.failed:
            return None
        if url in self.pix:
            return self.pix[url]
        f = self._file(url)
        if f.exists():
            pm = QPixmap(str(f))
            if not pm.isNull():
                self.pix[url] = self._scale(pm)
                return self.pix[url]
            self.failed.add(url)
            return None
        if url not in self.pending:
            self.pending.add(url)
            self.queue.append(url)
            self._pump()
        return None

    def _pump(self):
        while self.active < self.MAX_ACTIVE and self.queue:
            url = self.queue.pop()  # mais recente primeiro = itens visíveis agora
            self.active += 1
            reply = self.nam.get(make_request(url))
            reply.finished.connect(lambda r=reply, u=url: self._done(r, u))

    def _done(self, reply, url):
        self.active -= 1
        self.pending.discard(url)
        data = bytes(reply.readAll()) if reply.error() == QNetworkReply.NetworkError.NoError else b""
        reply.deleteLater()
        pm = QPixmap()
        if data and pm.loadFromData(data):
            try:
                self._file(url).write_bytes(data)
            except OSError:
                pass
            self.pix[url] = self._scale(pm)
        else:
            self.failed.add(url)
        self.loaded.emit(url)
        self._pump()


class Scanner(QObject):
    """Testa em paralelo se os canais respondem (HTTP 2xx/3xx)."""
    result = pyqtSignal(str, bool)
    progress = pyqtSignal(int, int)
    finished = pyqtSignal()
    MAX_ACTIVE = 12

    def __init__(self, parent=None, max_active=None):
        super().__init__(parent)
        self.nam = QNetworkAccessManager(self)
        self.running = False
        self.cancelled = False  # parado com stop(): finished é emitido, mas a rodada não terminou
        self.replies = []
        if max_active:
            self.MAX_ACTIVE = max_active

    def start(self, channels):
        self.queue, self.total, self.done = list(channels), len(channels), 0
        self.active, self.decided, self.running = 0, set(), True
        self.cancelled = False
        self._pump()

    def stop(self):
        if self.running:
            self.cancelled = True
        self.running = False
        self.queue = []
        for r in list(self.replies):
            r.abort()

    def _pump(self):
        while self.running and self.active < self.MAX_ACTIVE and self.queue:
            ch = self.queue.pop(0)
            self.active += 1
            reply = self.nam.get(make_request(ch.url, ch, timeout=7000))
            self.replies.append(reply)
            reply.readyRead.connect(lambda r=reply, u=ch.url: self._ready(r, u))
            reply.finished.connect(lambda r=reply, u=ch.url: self._done(r, u))
        if self.active == 0 and not self.queue:
            self.running = False
            self.finished.emit()

    def _decide(self, url, ok):
        if url not in self.decided:
            self.decided.add(url)
            self.result.emit(url, ok)

    def _ready(self, reply, url):
        if http_ok(reply):
            self._decide(url, True)
            reply.abort()  # streams contínuos nunca terminam; basta o primeiro byte

    def _done(self, reply, url):
        ok = reply.error() == QNetworkReply.NetworkError.NoError and http_ok(reply)
        if self.running or url in self.decided:
            self._decide(url, ok)
        self.replies.remove(reply)
        reply.deleteLater()
        self.active -= 1
        self.done += 1
        self.progress.emit(self.done, self.total)
        self._pump()


class ListDownloader(QObject):
    """Baixa várias listas M3U em paralelo e grava no cache."""
    one_done = pyqtSignal(str, bool)       # chave, sucesso
    progress = pyqtSignal(int, int)
    finished = pyqtSignal(int, int)        # sucessos, falhas
    _built = pyqtSignal(str, object, bytes)  # lista Xtream montada numa tarefa: chave, destino, dados
    MAX_ACTIVE = 4

    def __init__(self, parent=None):
        super().__init__(parent)
        self.nam = QNetworkAccessManager(self)
        self.running = False
        self.old_urls = {}                 # chave -> links da versão anterior (para achar canais novos)
        self._built.connect(self._store)

    def start(self, jobs):
        """jobs: lista de (chave, url, caminho_destino)."""
        self.queue, self.total, self.done, self.ok, self.active = list(jobs), len(jobs), 0, 0, 0
        self.running = bool(jobs)
        if not jobs:
            self.finished.emit(0, 0)
        self._pump()

    def _pump(self):
        while self.active < self.MAX_ACTIVE and self.queue:
            key, url, dest = self.queue.pop(0)
            self.active += 1
            if isinstance(url, tuple):
                threading.Thread(target=self._join_lists, args=(key, url, dest), daemon=True,
                                 name="listas").start()
                continue
            if xtream.is_api_url(url):
                threading.Thread(target=self._build_xtream, args=(key, url, dest), daemon=True,
                                 name="xtream").start()
                continue
            reply = self.nam.get(make_request(url, timeout=90000, user_agent=b"IPTV-Player"))
            reply.finished.connect(lambda r=reply, k=key, d=dest: self._done(r, k, d))

    def _join_lists(self, key, urls, dest):
        """Baixa as listas da categoria e junta numa só. O GitHub às vezes recusa muitos pedidos seguidos:
        cada lista tem 3 tentativas e, se alguma faltar, a versão anterior completa é mantida."""
        parts, missing = [], 0
        for url in urls:
            text = None
            for attempt in range(3):
                try:
                    req = urllib.request.Request(url, headers={"User-Agent": "IPTV-Player"})
                    with urllib.request.urlopen(req, timeout=60) as r:
                        text = r.read().decode("utf-8", "replace")
                    break
                except OSError as e:
                    if attempt == 2:
                        log.warning("Lista %s não baixou (%s)", url.rsplit("/", 1)[-1], e)
                    else:
                        time.sleep(3 * (attempt + 1))
            if text is None:
                missing += 1
                continue
            parts.append("\n".join(ln for ln in text.splitlines() if not ln.startswith("#EXTM3U")))
            time.sleep(0.3)
        if missing and dest.exists():
            data = b""  # fica com a cópia completa de antes
        else:
            data = ("#EXTM3U\n" + "\n".join(parts) + "\n").encode("utf-8") if parts else b""
        self._built.emit(key, dest, data)

    def _build_xtream(self, key, url, dest):
        try:
            data = xtream.build_m3u(url)
        except xtream.XtreamError as e:
            log.warning("Xtream: %s (%s)", e, xtream.redact(url))
            data = b""
        self._built.emit(key, dest, data)

    def _done(self, reply, key, dest):
        data = bytes(reply.readAll()) if reply.error() == QNetworkReply.NetworkError.NoError else b""
        reply.deleteLater()
        self._store(key, dest, data)

    def _store(self, key, dest, data):
        good = b"#EXTINF" in data[:200000]
        if good:
            try:
                tmp = dest.with_suffix(".part")
                tmp.write_bytes(data)
                self.old_urls[key] = list_urls(dest) if dest.exists() else None
                tmp.replace(dest)
            except OSError:
                good = False
        self.active -= 1
        self.done += 1
        self.ok += good
        self.one_done.emit(key, good)
        self.progress.emit(self.done, self.total)
        if self.done == self.total:
            self.running = False
            self.finished.emit(self.ok, self.total - self.ok)
        else:
            self._pump()
