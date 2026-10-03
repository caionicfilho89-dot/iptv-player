"""Atualização com um clique: baixa o instalador da versão nova no GitHub e instala sozinho."""
import subprocess

from PyQt6.QtCore import QFile, QIODevice, QObject, pyqtSignal
from PyQt6.QtNetwork import QNetworkAccessManager, QNetworkReply

from .log import log
from .net import http_ok, make_request
from .paths import BASE, CACHE, FROZEN

UPDATE_DIR = CACHE / "update"


def can_self_update():
    """Só a versão instalada (com desinstalador) se atualiza sozinha; a portátil abre a página."""
    return FROZEN and (BASE / "unins000.exe").exists()


def setup_asset(release):
    """(url, tamanho) do instalador .exe de um release do GitHub (JSON da API)."""
    for a in release.get("assets", []):
        name = a.get("name", "")
        if "setup" in name.lower() and name.lower().endswith(".exe"):
            return a.get("browser_download_url", ""), int(a.get("size", 0))
    return "", 0


class UpdateDownloader(QObject):
    progress = pyqtSignal(int)        # porcentagem
    finished = pyqtSignal(str)        # caminho do instalador baixado ("" = falhou)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.nam = QNetworkAccessManager(self)
        self.reply = None
        self.file = None

    @property
    def running(self):
        return self.reply is not None

    def start(self, url, size):
        UPDATE_DIR.mkdir(parents=True, exist_ok=True)
        self.size = size
        self.path = UPDATE_DIR / url.rsplit("/", 1)[-1]
        self.file = QFile(str(self.path))
        if not self.file.open(QIODevice.OpenModeFlag.WriteOnly | QIODevice.OpenModeFlag.Truncate):
            log.error("Atualização: não deu para gravar %s", self.path)
            self.finished.emit("")
            return
        log.info("Atualização: baixando %s", url)
        self.reply = self.nam.get(make_request(url, timeout=60000, user_agent=b"IPTV-Player"))
        self.reply.readyRead.connect(self._write)   # grava aos poucos: o instalador tem mais de 100 MB
        self.reply.downloadProgress.connect(self._progress)
        self.reply.finished.connect(self._done)

    def cancel(self):
        if self.reply:
            self.reply.abort()

    def _write(self):
        self.file.write(self.reply.readAll())

    def _progress(self, got, total):
        total = total if total > 0 else self.size
        if total > 0:
            self.progress.emit(int(got * 100 / total))

    def _done(self):
        reply, self.reply = self.reply, None
        self._write_rest(reply)
        self.file.close()
        ok = reply.error() == QNetworkReply.NetworkError.NoError and http_ok(reply)
        size = self.path.stat().st_size if self.path.exists() else 0
        if ok and self.size and size != self.size:
            log.error("Atualização: arquivo incompleto (%d de %d bytes)", size, self.size)
            ok = False
        if not ok:
            log.error("Atualização: download falhou (%s)", reply.errorString())
            self.path.unlink(missing_ok=True)
        reply.deleteLater()
        self.finished.emit(str(self.path) if ok else "")

    def _write_rest(self, reply):
        data = reply.readAll()
        if data:
            self.file.write(data)


def run_installer(path):
    """Instala em silêncio (só a barra de progresso) e reabre o programa ao terminar.

    /CLOSEAPPLICATIONS fecha este programa se ele ainda estiver aberto quando a cópia começar."""
    log.info("Atualização: executando %s", path)
    subprocess.Popen([path, "/SILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CLOSEAPPLICATIONS", "/RELAUNCH=1"],
                     creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
                     close_fds=True)
