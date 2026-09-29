"""Guia de programação (XMLTV), baixado e lido em segundo plano."""
import calendar
import gzip
import hashlib
import re
import threading
import time
import unicodedata
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime

from PyQt6.QtCore import QObject, pyqtSignal

from .paths import EPG_CACHE

MAX_AGE = 12 * 3600          # baixa o guia de novo a cada 12h
KEEP_BEFORE, KEEP_AFTER = 3 * 3600, 36 * 3600


def norm(name):
    """Normaliza nome de canal para comparar lista x guia ("São Paulo/SP  Globo HD" -> "globo")."""
    if "  " in name and "/" in name.split("  ")[0]:
        name = name.split("  ", 1)[1]
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"\[.*?\]|\(.*?\)", " ", s)
    s = re.sub(r"\b(hd|fhd|uhd|sd|4k|h265|tv|canal|channel|brasil|brazil)\b", " ", s)
    return re.sub(r"[^a-z0-9]", "", s)


def parse_time(s):
    s = (s or "").strip()
    try:
        dt = datetime.strptime(s[:14], "%Y%m%d%H%M%S")
    except ValueError:
        return None
    tz = s[14:].strip()
    if re.fullmatch(r"[+-]\d{4}", tz):
        off = (int(tz[1:3]) * 3600 + int(tz[3:5]) * 60) * (1 if tz[0] == "+" else -1)
        return calendar.timegm(dt.timetuple()) - off
    return time.mktime(dt.timetuple())


class Guide:
    def __init__(self):
        self.progs = {}   # id do canal no guia -> [(início, fim, título, descrição)]
        self.ids = {}     # id normalizado -> id
        self.names = {}   # nome normalizado -> id
        self.name_ids = {}  # nome normalizado -> todos os ids com esse nome (durante a leitura)


class EpgManager(QObject):
    updated = pyqtSignal()
    status = pyqtSignal(str)
    _loaded = pyqtSignal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.guide = Guide()
        self._match = {}
        self._busy = False
        self._pending = None
        self._loaded.connect(self._on_loaded)

    # ------------------------------------------------------------ consulta
    def _channel_key(self, ch):
        if ch.url in self._match:
            return self._match[ch.url]
        g, key = self.guide, None
        if ch.tvg_id:
            key = g.ids.get(ch.tvg_id.split("@")[0].lower())
        if not key:
            n = norm(ch.name)
            key = g.names.get(n) if n else None
        self._match[ch.url] = key
        return key

    def schedule(self, ch):
        key = self._channel_key(ch)
        return self.guide.progs.get(key, []) if key else []

    def now_next(self, ch, now=None):
        now = now or time.time()
        cur = nxt = None
        for p in self.schedule(ch):
            if p[0] <= now < p[1]:
                cur = p
            elif p[0] >= now:
                nxt = p
                break
        return cur, nxt

    def has_data(self):
        return bool(self.guide.progs)

    # ------------------------------------------------------------ carga
    def load(self, urls, force=False):
        urls = [u for u in dict.fromkeys(urls) if u]
        if self._busy:
            self._pending = (urls, force)
            return
        if not urls:
            self._on_loaded(Guide())
            return
        self._busy = True
        self.status.emit("Carregando guia de programação…")
        threading.Thread(target=self._worker, args=(urls, force), daemon=True).start()

    def _worker(self, urls, force):
        g = Guide()
        now = time.time()
        for url in urls:
            try:
                path = EPG_CACHE / (hashlib.md5(url.encode()).hexdigest() + ".xml")
                if force or not path.exists() or now - path.stat().st_mtime > MAX_AGE:
                    req = urllib.request.Request(url, headers={"User-Agent": "IPTV-Player"})
                    with urllib.request.urlopen(req, timeout=60) as r:
                        data = r.read()
                    tmp = path.with_suffix(".part")
                    tmp.write_bytes(data)
                    tmp.replace(path)
                self._parse(path, g, now)
            except Exception:  # guia é opcional: qualquer falha só deixa este guia de fora
                continue
        for lst in g.progs.values():
            lst.sort()
        # o mesmo nome pode aparecer em vários canais do guia: fica com o que tem programação
        for n, ids in g.name_ids.items():
            g.names[n] = next((i for i in ids if g.progs.get(i)), ids[0])
        g.name_ids = {}
        self._loaded.emit(g)

    @staticmethod
    def _parse(path, g, now):
        with open(path, "rb") as f:
            gz = f.read(2) == b"\x1f\x8b"
        stream = gzip.open(path) if gz else open(path, "rb")
        with stream:
            for _, el in ET.iterparse(stream):
                if el.tag == "channel":
                    cid = el.get("id") or ""
                    g.ids.setdefault(cid.split("@")[0].lower(), cid)
                    for dn in el.findall("display-name"):
                        n = norm(dn.text or "")
                        if n and cid not in g.name_ids.setdefault(n, []):
                            g.name_ids[n].append(cid)
                    el.clear()
                elif el.tag == "programme":
                    start, stop = parse_time(el.get("start")), parse_time(el.get("stop"))
                    if start and stop and stop > now - KEEP_BEFORE and start < now + KEEP_AFTER:
                        desc = (el.findtext("desc") or "").strip()
                        g.progs.setdefault(el.get("channel"), []).append(
                            (start, stop, (el.findtext("title") or "").strip(), desc[:400]))
                    el.clear()

    def _on_loaded(self, g):
        self.guide = g
        self._match = {}
        self._busy = False
        n = len(g.progs)
        self.status.emit(f"Guia: {n} canais com programação" if n else "")
        self.updated.emit()
        if self._pending:
            urls, force = self._pending
            self._pending = None
            self.load(urls, force)
