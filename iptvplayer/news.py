"""Notícias do banner do Início: manchetes do Brasil e estreias de filmes (cinema e streaming), do Google Notícias.

Baixa em segundo plano a cada 30 min e guarda em .cache/noticias.json, para o banner já abrir com as
últimas manchetes (mesmo sem internet). Sem chave de API: são os feeds RSS públicos do Google Notícias.
"""
import email.utils
import json
import threading
import time
import urllib.request
import xml.etree.ElementTree as ET

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from .log import log
from .paths import CACHE

FEEDS = {
    "news": "https://news.google.com/rss?hl=pt-BR&gl=BR&ceid=BR:pt-419",
    "movies": "https://news.google.com/rss/search?q=(estreia+OR+estreias+OR+lan%C3%A7amento)"
              "+(filme+OR+cinema+OR+streaming)+when:7d&hl=pt-BR&gl=BR&ceid=BR:pt-419",
}
CACHE_FILE = CACHE / "noticias.json"
EVERY_MS = 30 * 60 * 1000
KEEP = 20


def parse_rss(raw):
    """[{title, source, url, ts}] de um feed RSS do Google Notícias, mais recentes primeiro."""
    out, seen = [], set()
    for it in ET.fromstring(raw).iter("item"):
        title = (it.findtext("title") or "").strip()
        source = (it.findtext("source") or "").strip()
        if source and title.endswith(" - " + source):
            title = title[:-len(source) - 3].rstrip()   # "Manchete - G1" -> "Manchete"
        if not title or title.lower() in seen:
            continue
        seen.add(title.lower())
        try:
            ts = email.utils.parsedate_to_datetime(it.findtext("pubDate") or "").timestamp()
        except (TypeError, ValueError):
            ts = 0
        out.append({"title": title, "source": source, "url": (it.findtext("link") or "").strip(), "ts": ts})
    out.sort(key=lambda n: -n["ts"])
    return out[:KEEP]


def ago(ts, now=None):
    """"agora", "há 25 min", "há 3 h", "há 2 dias"."""
    if not ts:
        return ""
    s = max(0, (now or time.time()) - ts)
    if s < 120:
        return "agora"
    if s < 3600:
        return f"há {int(s // 60)} min"
    if s < 86400:
        return f"há {int(s // 3600)} h"
    d = int(s // 86400)
    return f"há {d} dia{'s' if d > 1 else ''}"


class NewsFeed(QObject):
    changed = pyqtSignal()
    _done = pyqtSignal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.items = {k: [] for k in FEEDS}
        self.updated = 0.0
        self._busy = False
        try:
            data = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
            self.items.update({k: v for k, v in data.get("items", {}).items() if k in FEEDS})
            self.updated = data.get("updated", 0.0)
        except (OSError, ValueError):
            pass
        self._done.connect(self._on_done)
        self.timer = QTimer(self, interval=EVERY_MS, timeout=self.fetch)
        self.timer.start()
        QTimer.singleShot(2500, self.fetch)

    def fetch(self):
        if self._busy:
            return
        self._busy = True
        threading.Thread(target=self._work, name="noticias", daemon=True).start()

    def _work(self):
        got = {}
        for key, url in FEEDS.items():
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 PudimFlix"})
                with urllib.request.urlopen(req, timeout=15) as r:
                    got[key] = parse_rss(r.read())
            except Exception as e:  # noqa: BLE001 — sem internet ou feed fora: fica o que já tinha
                log.info("Notícias (%s) não baixaram: %s", key, e)
        self._done.emit(got)

    def _on_done(self, got):
        self._busy = False
        got = {k: v for k, v in got.items() if v}
        if not got:
            return
        self.items.update(got)
        self.updated = time.time()
        try:
            CACHE_FILE.write_text(json.dumps({"updated": self.updated, "items": self.items}, ensure_ascii=False),
                                  encoding="utf-8")
        except OSError:
            pass
        self.changed.emit()
