"""Assistir sem internet: filmes e episódios baixados para o PC.

Usa a fila de downloads (downloads.py) só para baixar — os arquivos ficam em Vídeos\\PudimFlix Downloads.
Ao tocar um título baixado, o player abre o arquivo do disco em vez do link (funciona sem internet, e o
"continuar de onde parou" continua valendo, porque a chave é o link original). Sem internet, o programa
avisa e mostra a seção Baixados; capas, sinopses e listas já vistas vêm do cache.
"""
import socket
import threading
from pathlib import Path

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from .downloads import DownloadManager
from .log import log
from .m3u import Channel

CHECK_HOSTS = (("1.1.1.1", 53), ("8.8.8.8", 53), ("208.67.222.222", 53))
CHECK_EVERY_MS = 30_000


def has_internet(timeout=2.5):
    for host in CHECK_HOSTS:
        try:
            socket.create_connection(host, timeout=timeout).close()
            return True
        except OSError:
            continue
    return False


class OfflineLibrary(QObject):
    changed = pyqtSignal(str)        # link do título que mudou ("" = vários)
    online_changed = pyqtSignal(bool)

    def __init__(self, cfg, parent=None):
        super().__init__(parent)
        cfg.data.setdefault("dl_upload", False)   # o modo offline só baixa; nada é enviado para fora
        self.dm = DownloadManager(cfg)
        self.dm.changed.connect(self._on_changed)
        self.online = True
        self._checking = False
        self._timer = QTimer(self, interval=CHECK_EVERY_MS, timeout=self.check)
        self._result = None
        self._poll = QTimer(self, interval=300, timeout=self._deliver)

    def start(self):
        self.dm.start()
        self.check()
        self._timer.start()

    def shutdown(self):
        self.dm.shutdown()

    # ---- internet
    def check(self):
        """Testa a internet numa tarefa separada (nunca trava a tela)."""
        if self._checking:
            return
        self._checking = True

        def work():
            self._result = has_internet()
        threading.Thread(target=work, name="checa-internet", daemon=True).start()
        self._poll.start()

    def _deliver(self):
        if self._result is None:
            return
        self._poll.stop()
        ok, self._result, self._checking = self._result, None, False
        if ok != self.online:
            self.online = ok
            log.info("Internet: %s", "voltou" if ok else "sem conexão — modo offline")
            self.online_changed.emit(ok)

    # ---- títulos
    def _item(self, url):
        with self.dm.lock:
            return next((it for it in self.dm.items if it["url"] == url), None)

    def _on_changed(self, item_id):
        it = self.dm.get(item_id) if item_id else None
        self.changed.emit(it["url"] if it else "")

    def state(self, url):
        """(estado, porcentagem) — estado: None, "fila", "baixando", "pausado", "erro" ou "concluido"."""
        it = self._item(url)
        if not it:
            return None, 0
        st = it["state"]
        if st in ("baixado", "enviando"):
            st = "concluido"
        if st == "concluido" and not Path(it.get("local") or "").exists():
            return None, 0       # arquivo apagado fora do programa
        pct = int(100 * it["done"] / it["total"]) if it.get("total") else 0
        return st, pct

    def local_file(self, url):
        """Arquivo baixado do título, ou None."""
        it = self._item(url)
        if it and it["state"] in ("concluido", "baixado", "enviando") and Path(it.get("local") or "").exists():
            return it["local"]
        return None

    def download(self, items, folder=""):
        """items: Channel de filmes/episódios. Devolve quantos entraram na fila."""
        n = self.dm.add([(c.name, c.url, list(c.opts)) for c in items], folder)
        for c in items:   # o necessário para mostrar o título mesmo sem a lista carregada
            it = self._item(c.url)
            if it is not None and "logo" not in it:
                self.dm._set(it, save=False, logo=c.logo, group=c.group, kind=c.kind or "movie")
        self.dm.save()
        return n

    def retry(self, url):
        it = self._item(url)
        if it:
            self.dm.resume(it["id"])

    def remove(self, url):
        it = self._item(url)
        if it:
            self.dm.remove(it["id"], delete_file="sempre")

    def entries(self):
        """Títulos baixados ou baixando, do mais recente: [(Channel, estado, porcentagem)]."""
        out = []
        with self.dm.lock:
            items = list(self.dm.items)
        for it in sorted(items, key=lambda x: -x.get("added", 0)):
            st, pct = self.state(it["url"])
            if st is None:
                continue
            ch = Channel(name=it["name"], url=it["url"], logo=it.get("logo", ""), group=it.get("group", ""),
                         opts=list(it.get("opts", [])), kind=it.get("kind", "movie"))
            out.append((ch, st, pct))
        return out
