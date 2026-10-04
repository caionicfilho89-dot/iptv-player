"""Histórico de estabilidade dos canais: quantas vezes abriu, falhou ou travou, e quanto demorou para abrir.

Serve para mostrar quais canais costumam funcionar e para ordenar a lista pelos mais estáveis."""
import json
import time

from .paths import CACHE

HEALTH_FILE = CACHE / "estabilidade.json"
MAX_CHANNELS = 20000
PROBE_WEIGHT = 0.5  # o teste de lista só vê se o link responde; vale metade de abrir de verdade

# campos de cada canal: abriu, falhou, travou, soma dos segundos para abrir, teste ok, teste falhou, quando
OK, FAIL, STALL, START, P_OK, P_FAIL, WHEN = range(7)


class Health:
    def __init__(self):
        self.data, self._dirty = {}, 0
        try:
            raw = json.loads(HEALTH_FILE.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                self.data = {u: v for u, v in raw.items() if isinstance(v, list) and len(v) == 7}
        except (OSError, ValueError):
            pass

    def _rec(self, url):
        r = self.data.get(url)
        if r is None:
            r = self.data[url] = [0, 0, 0, 0.0, 0, 0, 0]
        r[WHEN] = int(time.time())
        self._dirty += 1
        return r

    def opened(self, url, seconds):
        r = self._rec(url)
        r[OK] += 1
        r[START] += round(seconds, 1)

    def failed(self, url):
        self._rec(url)[FAIL] += 1

    def stalled(self, url):
        self._rec(url)[STALL] += 1

    def probed(self, url, ok):
        self._rec(url)[P_OK if ok else P_FAIL] += 1

    def score(self, url):
        """0 a 1 (None = nunca testado). Começa em 0,5 e se aproxima da taxa real conforme há dados."""
        r = self.data.get(url)
        if not r:
            return None
        good = r[OK] + PROBE_WEIGHT * r[P_OK]
        total = r[OK] + r[FAIL] + PROBE_WEIGHT * (r[P_OK] + r[P_FAIL])
        s = (good + 1) / (total + 2)
        s -= min(0.3, 0.15 * r[STALL] / (r[OK] + 1))  # trava depois de abrir
        if r[OK]:
            s -= min(0.1, max(0.0, r[START] / r[OK] - 5) / 100)  # demora para abrir
        return max(0.0, min(1.0, s))

    def level(self, url):
        """3 = estável, 2 = razoável, 1 = instável, 0 = sem dados suficientes."""
        r = self.data.get(url)
        if not r or r[OK] + r[FAIL] + r[P_OK] + r[P_FAIL] < 2:
            return 0
        s = self.score(url)
        return 3 if s >= 0.75 else 2 if s >= 0.45 else 1

    def describe(self, url):
        r = self.data.get(url)
        if not r:
            return ""
        parts = []
        if r[OK] or r[FAIL]:
            parts.append(f"abriu {r[OK]} de {r[OK] + r[FAIL]} vez{'es' if r[OK] + r[FAIL] != 1 else ''}")
        if r[STALL]:
            parts.append(f"travou {r[STALL]} vez{'es' if r[STALL] != 1 else ''}")
        if r[OK]:
            parts.append(f"leva {r[START] / r[OK]:.0f} s para abrir")
        if r[P_OK] or r[P_FAIL]:
            parts.append(f"no ar em {r[P_OK]} de {r[P_OK] + r[P_FAIL]} testes")
        return "Estabilidade: " + ", ".join(parts)

    def save(self, force=False):
        if not self._dirty or (not force and self._dirty < 50):
            return
        if len(self.data) > MAX_CHANNELS:  # esquece os canais vistos há mais tempo
            for u in sorted(self.data, key=lambda u: self.data[u][WHEN])[:len(self.data) - MAX_CHANNELS]:
                del self.data[u]
        try:
            tmp = HEALTH_FILE.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.data, separators=(",", ":")), encoding="utf-8")
            tmp.replace(HEALTH_FILE)
            self._dirty = 0
        except OSError:
            pass
