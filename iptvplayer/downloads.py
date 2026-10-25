"""Fila de downloads: baixa um título por vez (link direto, HLS ou sites do yt-dlp) e envia para o TeraBox.

Uma tarefa baixa e outra envia, então o envio de um filme acontece enquanto o próximo já está baixando.
A fila fica em downloads.json; ao abrir o programa, o que estava pela metade continua de onde parou."""
import glob
import importlib
import json
import re
import shutil
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

from PyQt6.QtCore import QObject, pyqtSignal

from . import m3u
from .log import log
from .paths import DATA_DIR
from .recorder import _user_dir

QUEUE_FILE = DATA_DIR / "downloads.json"
VIDEO_EXT = (".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv", ".webm", ".ts", ".m4v", ".mpg", ".mpeg", ".3gp")
USER_AGENT = "VLC/3.0.21 LibVLC/3.0.21"   # servidores IPTV costumam liberar só players
PARALLEL_MIN = 16 * 1024 * 1024            # arquivos menores baixam por uma conexão só
# estados: fila -> baixando -> baixado -> enviando -> concluido   (ou pausado / erro)
LABELS = {"fila": "Na fila", "baixando": "Baixando", "baixado": "Aguardando envio", "enviando": "Enviando",
          "concluido": "Concluído", "pausado": "Pausado", "erro": "Erro"}


class Stopped(Exception):
    pass


class _Fallback(Exception):
    """O servidor não aceita baixar em várias partes ao mesmo tempo."""


def _terabox():
    """Envio ao TeraBox é opcional: o módulo pode não estar nesta versão."""
    try:
        return importlib.import_module(".terabox", __package__)
    except ImportError:
        return None


def _part_files(local):
    """Pedaços de um download pela metade: .part (uma conexão) e .part0, .part1… (várias)."""
    p = Path(str(local) + ".part")
    return [p] + sorted(p.parent.glob(glob.escape(p.name) + "[0-9]*"))


def safe_name(name, limit=120):
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", name or "").strip(" .")[:limit] or "video"


def name_from_url(url):
    stem = Path(urllib.parse.unquote(urllib.parse.urlsplit(url).path)).stem
    return re.sub(r"[._]+", " ", stem).strip() or "video"


def is_list_url(url):
    low = url.lower().split("?")[0]
    return low.endswith((".m3u", ".txt")) or "get.php" in low or "type=m3u" in url.lower()


def parse_entries(text):
    """Texto colado -> [(nome, link, opções)]. Aceita lista M3U, "Nome | link" ou um link por linha."""
    text = (text or "").strip()
    if "#EXTINF" in text:
        chans, _ = m3u.parse_m3u_text(text)
        return [(c.name, c.url, list(c.opts)) for c in chans if c.url.startswith("http")]
    out = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        name, _, url = line.rpartition("|") if "|" in line else ("", "", line)
        url = url.strip()
        if url.startswith("http"):
            out.append((name.strip() or name_from_url(url), url, []))
    return out


def fetch_list(url):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read().decode("utf-8", "replace")


class _Mute:
    def debug(self, msg):
        pass

    info = warning = error = debug


class DownloadManager(QObject):
    changed = pyqtSignal(str)   # id do item que mudou ("" = a lista toda)

    def __init__(self, cfg):
        super().__init__()
        self.cfg = cfg
        self.lock = threading.RLock()
        self.items = self._load()
        self._stop = set()          # ids que devem parar o que estão fazendo
        self._wake = threading.Event()
        self._quit = False
        self._threads = []
        self._last_save = 0.0

    # ------------------------------------------------------------ fila
    def _load(self):
        try:
            items = json.loads(QUEUE_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        for it in items:   # o programa fechou no meio: volta um passo
            if it.get("state") == "baixando":
                it["state"] = "fila"
            elif it.get("state") == "enviando":
                it["state"] = "baixado"
        return [it for it in items if isinstance(it, dict) and it.get("url")]

    def save(self, force=True):
        if not force and time.monotonic() - self._last_save < 5:
            return
        self._last_save = time.monotonic()
        # o lock vale até o arquivo trocar: a tela e as tarefas de download salvam ao mesmo tempo
        with self.lock:
            data = json.dumps(self.items, ensure_ascii=False, indent=1)
            tmp = QUEUE_FILE.with_suffix(".tmp")
            try:
                tmp.write_text(data, encoding="utf-8")
                tmp.replace(QUEUE_FILE)
            except OSError as e:
                log.warning("Downloads: não consegui salvar a fila: %s", e)

    def _opt(self, key):
        """Opção das configurações (as do envio ao TeraBox podem não existir)."""
        try:
            return self.cfg[key]
        except KeyError:
            return None

    def add(self, entries, folder=""):
        """entries: [(nome, link, opções)]. Devolve quantos entraram (links repetidos ficam de fora)."""
        with self.lock:
            known = {it["url"] for it in self.items}
            new = []
            for name, url, opts in entries:
                if url in known:
                    continue
                known.add(url)
                new.append({"id": uuid.uuid4().hex[:12], "name": name, "url": url, "opts": list(opts or []),
                            "folder": folder, "state": "fila", "done": 0, "total": 0, "error": "",
                            "local": "", "remote": "", "added": int(time.time())})
            self.items.extend(new)
        if new:
            self.save()
            self.changed.emit("")
            self._wake.set()
        return len(new)

    def get(self, item_id):
        with self.lock:
            return next((it for it in self.items if it["id"] == item_id), None)

    def _set(self, it, save=True, **kw):
        with self.lock:
            it.update(kw)
        if save:
            self.save()
        self.changed.emit(it["id"])

    def pause(self, item_id):
        it = self.get(item_id)
        if it and it["state"] in ("fila", "baixando", "baixado", "enviando"):
            self._stop.add(item_id)
            self._set(it, state="pausado", resume_to="baixado" if it["state"] in ("baixado", "enviando") else "fila")

    def resume(self, item_id):
        it = self.get(item_id)
        if it and it["state"] in ("pausado", "erro"):
            self._stop.discard(item_id)
            back = it.get("resume_to") or "fila"
            if back == "baixado" and not Path(it.get("local") or "").exists():
                back = "fila"
            self._set(it, state=back, error="")
            self._wake.set()

    def pause_all(self):
        for it in list(self.items):
            self.pause(it["id"])

    def resume_all(self):
        for it in list(self.items):
            if it["state"] == "pausado":
                self.resume(it["id"])

    def remove(self, item_id, delete_file=True):
        it = self.get(item_id)
        if not it:
            return
        self._stop.add(item_id)
        if delete_file and (it["state"] != "concluido" or delete_file == "sempre"):
            for p in ([Path(it["local"])] if it.get("local") else []) + _part_files(it.get("local") or "x"):
                try:
                    p.unlink(missing_ok=True)
                except OSError:
                    pass
        with self.lock:
            self.items = [x for x in self.items if x["id"] != item_id]
        self.save()
        self.changed.emit("")

    def clear_done(self):
        with self.lock:
            self.items = [x for x in self.items if x["state"] != "concluido"]
        self.save()
        self.changed.emit("")

    def counts(self):
        with self.lock:
            active = sum(1 for it in self.items if it["state"] in ("fila", "baixando", "baixado", "enviando"))
            return active, len(self.items)

    # ------------------------------------------------------------ pastas
    def local_dir(self):
        d = Path(self._opt("dl_dir")) if self._opt("dl_dir") else _user_dir("videos", "PudimFlix Downloads")
        d.mkdir(parents=True, exist_ok=True)
        return d

    def upload_enabled(self):
        return bool(self._opt("dl_upload") and self._opt("terabox_cookie") and _terabox())

    # ------------------------------------------------------------ tarefas
    def start(self):
        for target, name in ((self._download_loop, "downloads"), (self._upload_loop, "envio-terabox")):
            t = threading.Thread(target=target, name=name, daemon=True)
            t.start()
            self._threads.append(t)

    def shutdown(self):
        self._quit = True
        with self.lock:
            self._stop.update(it["id"] for it in self.items)
        self._wake.set()
        self.save()

    def _next(self, state):
        with self.lock:
            for it in self.items:
                if it["state"] == state:
                    self._stop.discard(it["id"])
                    return it
        return None

    def _check(self, it):
        if self._quit or it["id"] in self._stop:
            raise Stopped()

    def _progress(self, it, done, total):
        with self.lock:
            it["done"], it["total"] = done, total
        self.changed.emit(it["id"])
        self.save(force=False)

    def _loop(self, state, work):
        while not self._quit:
            it = self._next(state)
            if it is None:
                self._wake.wait(5)
                self._wake.clear()
                continue
            try:
                work(it)
            except Stopped:
                if self.get(it["id"]) is None:   # removido no meio: apaga o pedaço baixado
                    for p in _part_files(it.get("local") or "x"):
                        p.unlink(missing_ok=True)
            except Exception as e:  # noqa: BLE001 — erro num título não pode parar a fila
                log.warning("Downloads: %s falhou: %s", it["name"], e)
                if not self._quit and self.get(it["id"]) is not None:
                    self._set(it, state="erro", error=str(e)[:200],
                              resume_to="baixado" if state == "baixado" else "fila")

    def _download_loop(self):
        self._loop("fila", self._download)

    def _upload_loop(self):
        def work(it):
            if not self.upload_enabled():   # envio desligado depois de baixar: fica só no PC
                self._set(it, state="concluido")
                return
            self._upload(it)
        self._loop("baixado", work)

    # ------------------------------------------------------------ baixar
    def _target(self, it, ext):
        parts = [safe_name(p) for p in it["folder"].split("/") if p]
        d = self.local_dir().joinpath(*parts)
        d.mkdir(parents=True, exist_ok=True)
        return d / (safe_name(it["name"]) + ext)

    def _headers(self, it):
        h = {"User-Agent": USER_AGENT}
        for o in it.get("opts", []):
            if o.startswith(":http-user-agent="):
                h["User-Agent"] = o.split("=", 1)[1]
            elif o.startswith(":http-referrer="):
                h["Referer"] = o.split("=", 1)[1]
        return h

    def _download(self, it):
        url = it["url"]
        if "/live/" in urllib.parse.urlsplit(url).path:   # padrão dos servidores IPTV para canais ao vivo
            raise RuntimeError("isso é um canal ao vivo, não um filme ou episódio para baixar")
        self._set(it, state="baixando", error="")
        path_ext = Path(urllib.parse.urlsplit(url).path).suffix.lower()
        if path_ext == ".m3u8" or (path_ext not in VIDEO_EXT and not self._looks_direct(it)):
            self._ytdlp(it)
        else:
            self._http(it, path_ext if path_ext in VIDEO_EXT else ".mp4")
        self._check(it)
        self._set(it, state="baixado" if self.upload_enabled() else "concluido")

    def _looks_direct(self, it):
        """Link sem extensão: arquivo de vídeo direto (servidor IPTV) ou página de site (yt-dlp)?"""
        if re.search(r"/(movie|series)/", it["url"]):
            return True
        try:
            req = urllib.request.Request(it["url"], headers=dict(self._headers(it), Range="bytes=0-0"))
            with urllib.request.urlopen(req, timeout=20) as r:
                ctype = r.headers.get("Content-Type", "").lower()
        except Exception:  # noqa: BLE001
            return False
        return ctype.startswith(("video/", "application/octet-stream")) and "mpegurl" not in ctype

    def _conns(self):
        try:
            return max(1, int(self.cfg["net_conns"]))
        except (KeyError, TypeError, ValueError):
            return 1

    def _http(self, it, ext, tries=6):
        target = Path(it["local"]) if it.get("local") else self._target(it, ext)
        part = Path(str(target) + ".part")
        self._set(it, local=str(target))
        if self._conns() > 1 and not part.exists():
            try:
                if self._http_parallel(it, target, part, self._conns(), tries):
                    return
            except _Fallback as e:
                self._set(it, parts=None)
                log.info("Downloads: %s — servidor não aceitou várias conexões (%s), usando uma só", it["name"], e)
                for p in _part_files(target)[1:]:
                    p.unlink(missing_ok=True)
        for attempt in range(tries):
            have = part.stat().st_size if part.exists() else 0
            headers = self._headers(it)
            if have:
                headers["Range"] = f"bytes={have}-"
            try:
                req = urllib.request.Request(it["url"], headers=headers)
                with urllib.request.urlopen(req, timeout=30) as r:
                    if have and r.status != 206:   # servidor não retoma: começa de novo
                        have = 0
                    length = int(r.headers.get("Content-Length") or 0)
                    if not length and "/live/" in it["url"]:
                        raise RuntimeError("isso é um canal ao vivo, não um arquivo para baixar")
                    total = have + length if length else 0
                    with open(part, "ab" if have else "wb") as f:
                        done = have
                        while chunk := r.read(1024 * 1024):
                            self._check(it)
                            f.write(chunk)
                            done += len(chunk)
                            self._progress(it, done, total)
                if total and part.stat().st_size < total:
                    raise ConnectionError("conexão caiu antes do fim")
                part.replace(target)
                return
            except (Stopped, RuntimeError):
                raise
            except Exception as e:  # noqa: BLE001 — rede caiu: retoma do ponto onde parou
                if attempt == tries - 1:
                    raise RuntimeError(f"falha ao baixar: {e}") from e
                log.info("Downloads: %s — tentando de novo (%s)", it["name"], e)
                for _ in range(5 * (attempt + 1)):
                    self._check(it)
                    time.sleep(1)

    def _http_parallel(self, it, target, part, conns, tries):
        """Baixa o arquivo em `conns` partes ao mesmo tempo (pedidos com Range), cada uma no seu .partN,
        e junta no fim. Cada parte retoma de onde parou. Devolve False se o arquivo é pequeno ou o
        tamanho é desconhecido (aí baixa por uma conexão só); levanta _Fallback se o servidor recusa."""
        req = urllib.request.Request(it["url"], headers=dict(self._headers(it), Range="bytes=0-0"))
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                m = re.search(r"/(\d+)$", r.headers.get("Content-Range", ""))
                if r.status != 206 or not m:
                    return False   # servidor não divide o arquivo
                total = int(m.group(1))
        except urllib.error.HTTPError as e:
            raise _Fallback(f"HTTP {e.code}") from None
        except OSError:
            return False
        if total < PARALLEL_MIN:
            return False
        conns = it.get("parts") or conns   # retomando: as partes já começadas têm esse tamanho
        self._set(it, parts=conns)
        size = -(-total // conns)
        ranges = [(i, i * size, min(total, (i + 1) * size) - 1) for i in range(conns)]
        files = [Path(f"{target}.part{i}") for i, _a, _b in ranges]
        abort, errors = threading.Event(), []

        def worker(i, start, end):
            f = files[i]
            for attempt in range(tries):
                have = f.stat().st_size if f.exists() else 0
                if start + have > end:
                    return
                headers = dict(self._headers(it), Range=f"bytes={start + have}-{end}")
                try:
                    with urllib.request.urlopen(urllib.request.Request(it["url"], headers=headers),
                                                timeout=30) as r:
                        if r.status != 206:
                            raise _Fallback("ignorou o pedido de parte do arquivo")
                        with open(f, "ab") as out:
                            while not abort.is_set() and (chunk := r.read(256 * 1024)):
                                out.write(chunk[:end - start - have + 1])
                                have += len(chunk)
                    if abort.is_set() or start + have > end:
                        return
                except _Fallback as e:
                    errors.append(e)
                    abort.set()
                    return
                except urllib.error.HTTPError as e:
                    # limite de conexões da conta/servidor: não insiste, volta para uma conexão só
                    if e.code in (401, 403, 406, 429, 456, 458, 503, 509) and not f.exists():
                        errors.append(_Fallback(f"HTTP {e.code}"))
                        abort.set()
                        return
                    if attempt == tries - 1:
                        errors.append(e)
                        abort.set()
                        return
                except Exception as e:  # noqa: BLE001 — rede caiu: a parte retoma de onde parou
                    if attempt == tries - 1:
                        errors.append(e)
                        abort.set()
                        return
                if abort.wait(3 * (attempt + 1)):
                    return

        threads = [threading.Thread(target=worker, args=r, name=f"download-parte-{r[0]}", daemon=True)
                   for r in ranges]
        for t in threads:
            t.start()
        log.info("Downloads: %s — %d conexões ao mesmo tempo (%.0f MB)", it["name"], conns, total / 1e6)
        try:
            while any(t.is_alive() for t in threads):
                self._check(it)
                self._progress(it, sum(f.stat().st_size for f in files if f.exists()), total)
                time.sleep(0.5)
        except Stopped:
            abort.set()
            for t in threads:
                t.join(5)
            raise
        if errors:
            e = errors[0]
            if isinstance(e, _Fallback):
                raise e
            raise RuntimeError(f"falha ao baixar: {e}")
        if sum(f.stat().st_size for f in files if f.exists()) != total:
            raise RuntimeError("falha ao baixar: partes incompletas")
        with open(part, "wb") as out:   # junta as partes
            for f in files:
                with open(f, "rb") as src:
                    shutil.copyfileobj(src, out, 4 * 1024 * 1024)
        for f in files:
            f.unlink(missing_ok=True)
        part.replace(target)
        self._progress(it, total, total)
        return True

    def _ytdlp(self, it):
        try:
            import yt_dlp
        except ImportError:
            raise RuntimeError("esta versão não tem o yt-dlp para baixar desse site") from None
        base = self._target(it, "")
        done_file = {}

        def hook(d):
            self._check(it)
            if d["status"] == "downloading":
                self._progress(it, d.get("downloaded_bytes") or 0,
                               d.get("total_bytes") or d.get("total_bytes_estimate") or 0)
            elif d["status"] == "finished":
                done_file["path"] = d.get("filename")

        opts = {"quiet": True, "no_warnings": True, "noplaylist": True, "logger": _Mute(),
                "outtmpl": str(base) + ".%(ext)s", "progress_hooks": [hook], "continuedl": True,
                "format": "best[vcodec!=none][acodec!=none]/best", "http_headers": self._headers(it),
                "match_filter": yt_dlp.utils.match_filter_func("!is_live"), "retries": 10,
                "fragment_retries": 10,
                # HLS: vários pedaços ao mesmo tempo (mesma opção de conexões do player)
                "concurrent_fragment_downloads": self._conns()}
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.download([it["url"]])
        except Stopped:
            raise
        except Exception as e:  # noqa: BLE001
            if self._quit or it["id"] in self._stop:
                raise Stopped() from None
            raise RuntimeError(re.sub(r"^ERROR:\s*(\[[^\]]+\]\s*)*", "", str(e))[:160]) from None
        if not done_file.get("path"):
            raise RuntimeError("nada foi baixado (ao vivo ou link sem vídeo)")
        self._set(it, local=done_file["path"])

    # ------------------------------------------------------------ enviar
    def _upload(self, it):
        local = Path(it.get("local") or "")
        if not local.is_file():
            raise RuntimeError("o arquivo baixado sumiu da pasta")
        self._set(it, state="enviando", done=0, total=local.stat().st_size)
        terabox = _terabox()
        remote = terabox.norm_folder(f"{self.cfg['terabox_folder']}/{it['folder']}")
        box = terabox.TeraBox(self.cfg["terabox_cookie"]).login()
        path = box.upload(str(local), remote, local.name, progress=lambda d, t: self._progress(it, d, t),
                          stop=lambda: self._quit or it["id"] in self._stop)
        self._check(it)
        if self._opt("dl_delete"):
            try:
                local.unlink()
            except OSError as e:
                log.warning("Downloads: enviado, mas não consegui apagar %s: %s", local, e)
        self._set(it, state="concluido", remote=path)
        log.info("Downloads: %s enviado para o TeraBox em %s", it["name"], path)
