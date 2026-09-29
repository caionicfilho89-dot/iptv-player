"""Descoberta de canais: links alternativos, canais novos nas listas e memória do modo Explorar."""
import json
import re
import time
from dataclasses import asdict

from .m3u import Channel, parse_m3u
from .paths import CACHE

NEW_FILE = CACHE / "novos.json"
SEEN_FILE = CACHE / "explorados.txt"
NEW_TTL = 3 * 86400      # canal fica marcado como NOVO por 3 dias
MAX_ALT_TRIES = 4        # links alternativos testados antes de desistir do canal


def list_urls(path):
    """URLs de um arquivo .m3u, sem montar os canais (rápido)."""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return {ln.strip() for ln in f if ln.strip() and not ln.startswith("#")}
    except OSError:
        return None


def _norm_name(name):
    n = re.sub(r"\b(hd|fhd|uhd|sd|4k|1080p|720p|480p)\b", "", name.lower().replace("+", " plus"))
    return re.sub(r"[^a-z0-9]", "", n)


def _norm_id(tvg_id):
    return tvg_id.strip().lower()  # inclui o "@feed": Bloomberg@US e Bloomberg@Asia são canais diferentes


class LinkIndex:
    """Todos os links conhecidos de cada canal (mesmo ID do guia ou mesmo nome) em todas as listas."""

    def __init__(self):
        self.by_key = None

    def invalidate(self):
        self.by_key = None

    def build(self, paths):
        self.by_key = {}
        for path in paths:
            try:
                chans, _ = parse_m3u(path)
            except OSError:
                continue
            for c in chans:
                for key in self._keys(c):
                    bucket = self.by_key.setdefault(key, [])
                    if all(o.url != c.url for o in bucket):
                        bucket.append(c)

    @staticmethod
    def _keys(ch):
        keys = []
        if ch.tvg_id:
            keys.append("id:" + _norm_id(ch.tvg_id))
        n = _norm_name(ch.name)
        if len(n) >= 3:
            keys.append("nome:" + n)
        return keys

    def alternatives(self, ch, exclude, is_dead):
        """Outros links do mesmo canal, primeiro os com o mesmo ID do guia."""
        out, seen = [], set(exclude)  # o link original também vale, se um substituto memorizado cair
        for key in self._keys(ch):
            for c in (self.by_key or {}).get(key, []):
                if c.url not in seen and not is_dead(c.url):
                    seen.add(c.url)
                    out.append(c)
        return out


class NewChannels:
    """Canais que apareceram nas últimas atualizações das listas."""

    def __init__(self):
        try:
            self.data = json.loads(NEW_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.data = {}
        now = time.time()
        self.data = {u: v for u, v in self.data.items() if isinstance(v, dict) and now - v.get("t", 0) < NEW_TTL}

    def record(self, channels):
        now = time.time()
        added = 0
        for c in channels:
            if c.url not in self.data:
                self.data[c.url] = {"t": now, "ch": asdict(c)}
                added += 1
        return added

    def is_new(self, url):
        return url in self.data

    def channels(self):
        items = sorted(self.data.values(), key=lambda v: -v["t"])
        return [Channel.from_dict(v["ch"]) for v in items]

    def __len__(self):
        return len(self.data)

    def save(self):
        try:
            NEW_FILE.write_text(json.dumps(self.data, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass


class ExploredSet:
    """Canais já vistos no modo Explorar (para sempre mostrar coisa nova)."""

    def __init__(self):
        try:
            self.urls = set(SEEN_FILE.read_text(encoding="utf-8").split("\n")) - {""}
        except OSError:
            self.urls = set()
        self._dirty = 0

    def add(self, url):
        if url not in self.urls:
            self.urls.add(url)
            self._dirty += 1
            if self._dirty >= 10:
                self.save()

    def __contains__(self, url):
        return url in self.urls

    def __len__(self):
        return len(self.urls)

    def clear(self):
        self.urls.clear()
        self._dirty = 1
        self.save()

    def save(self):
        if not self._dirty:
            return
        try:
            SEEN_FILE.write_text("\n".join(self.urls), encoding="utf-8")
            self._dirty = 0
        except OSError:
            pass
