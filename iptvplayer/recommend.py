"""Dicas do que assistir, pelo seu gosto — calculadas no próprio PC, nada sai dele.

O gosto vem do que você assistiu (quanto mais assistiu, mais pesa; largado no começo pesa pouco) e da
Minha lista. De cada título saem gêneros, elenco, diretor e a pasta do catálogo (gênero do filme ou
plataforma da série). Cada filme, série e programa que você ainda não viu ganha pontos pelo que tem em
comum com esse gosto, mais um pouco pela nota e por ser recente. Daí saem as fileiras do Início:
"Recomendados para você", "Porque você assistiu …" e "Novidades para você", e a dica do dia.
"""
import math
import re
import time
from collections import Counter

from . import catalog as cat
from .catalog import CatalogNode, catalog_fold, display_title, series_parts
from .metadata import split_cast, stream_id

PER_GENRE = 5          # no máximo isso do mesmo gênero principal numa fileira (variedade)
ROW = 24


GENRE_NAMES = {}       # chave do gênero -> nome para mostrar ("Documentário")


def _genre_key(name):
    """"Documentário" e "Document?rio" (acento perdido no servidor) viram a mesma chave: só as consoantes."""
    letters = re.sub(r"[^a-z]", "", catalog_fold(name))
    return re.sub(r"[aeiou]", "", letters) or letters


def _genres(text):
    out = []
    for g in re.split(r"[,/|]", text or ""):
        g = g.strip()
        if len(g) > 2:
            k = _genre_key(g)
            if "?" not in g and "�" not in g:
                GENRE_NAMES.setdefault(k, g.lower())
            out.append(k)
    return out


def features(info, path):
    """Traços de um título: {traço: peso}."""
    f = {}
    for g in _genres(info.get("genre")):
        f["g:" + g] = 1.0
    for i, name in enumerate(split_cast(info.get("cast"))[:6]):
        f["c:" + name.lower()] = 0.7 if i < 3 else 0.4
    for d in split_cast(info.get("director"))[:2]:
        f["d:" + d.lower()] = 0.8
    if len(path) >= 2:
        f["p:" + "/".join(path[:2]).lower()] = 0.6
    return f


class Recommender:
    def __init__(self, win):
        self.win = win
        self._key = None
        self._rows = []
        self._reasons = {}
        self._cands, self._cands_key, self._idf = [], None, {}

    # ---- o que você já viu
    def _watched(self):
        """[(item do catálogo ou nó da série, peso, quando)] e o conjunto de títulos já vistos."""
        win, catalog = self.win, self.win.catalog
        out, seen = [], set()
        for url, pos in win.cfg["resume"].items():
            i = catalog.by_url.get(url)
            if i is None:
                continue
            ch = catalog.channels[i]
            secs, length, when = pos[0], pos[1], pos[2] if len(pos) > 2 else 0
            frac = 1.0 if secs < 0 else (secs / length if length else 0.3)
            weight = 0.25 if frac < 0.1 else 0.5 + frac      # largou logo no começo: pesa pouco
            e = self._title_of(ch)
            out.append((e, weight, when))
            seen.add(self._id(e))
        for f in win.cfg["favorites"]:
            i = catalog.by_url.get(f.get("url"))
            if i is None:
                continue
            ch = catalog.channels[i]
            if not cat.media_is_video(ch) and not self._is_title(ch):
                continue
            e = self._title_of(ch)
            out.append((e, 1.2, 0))
            seen.add(self._id(e))
        return out, seen

    def _is_title(self, ch):
        path = self.win.catalog.path_of(ch)
        return len(path) >= 3 and (path[0] == cat.SERIES or path[:2] == cat.TV_SHOWS)

    def _title_of(self, ch):
        """Episódio -> nó da série/programa; filme -> o próprio item."""
        catalog = self.win.catalog
        path = catalog.path_of(ch)
        if self._is_title(ch):
            node = catalog.node(path[:3])
            if node is not None:
                return node
        return ch

    @staticmethod
    def _id(e):
        return ("s", e.path) if isinstance(e, CatalogNode) else ("m", catalog_fold(display_title(e.name)))

    def _info(self, e):
        meta = self.win.meta
        if isinstance(e, CatalogNode):
            return meta.series_info(e.label) or {}
        return meta.movies.get(stream_id(e.url)) or {}

    def _path(self, e):
        return e.path if isinstance(e, CatalogNode) else self.win.catalog.path_of(e)

    # ---- pontuação
    def _candidates(self):
        catalog = self.win.catalog
        movies = catalog.node((cat.MOVIES,))
        if movies:
            seen = set()
            for i in movies.indices:
                ch = catalog.channels[i]
                k = catalog_fold(display_title(ch.name))
                if k not in seen:           # o mesmo filme dublado/legendado: um só
                    seen.add(k)
                    yield ch
        for root in ((cat.SERIES,), cat.TV_SHOWS):
            node = catalog.node(root)
            if node:
                yield from catalog.title_nodes(node)

    def _prepare(self):
        """Traços, ano e nota de todos os títulos (uma vez por catálogo) e o quanto cada traço é comum."""
        key = (id(self.win.catalog), len(self.win.meta.movies), len(self.win.meta.series))
        if self._cands_key == key:
            return
        cands, df = [], Counter()
        for e in self._candidates():
            path = self._path(e)
            if path and path[0] in (cat.ADULT, cat.UNSTABLE):
                continue
            info = self._info(e)
            try:
                year = int(str(info.get("year") or cat.year_of(e.label if isinstance(e, CatalogNode) else e.name)
                               or 0)[:4])
            except ValueError:
                year = 0
            feats = features(info, path)
            if not feats:
                continue
            df.update(feats.keys())
            try:
                rating = min(10.0, float(info.get("rating") or 0))
            except (TypeError, ValueError):
                rating = 0.0
            cands.append((e, self._id(e), feats, year, rating))
        n = max(1, len(cands))
        # gênero que quase todo título tem ("drama") diz pouco do seu gosto; um raro diz muito
        self._idf = {k: math.log((n + 1) / (c + 1)) + 0.3 for k, c in df.items()}
        self._cands, self._cands_key = cands, key

    def _score_all(self, profile, seen, newer_than=None):
        self._prepare()
        year_now = time.localtime().tm_year
        idf = self._idf
        out = []
        for e, eid, feats, year, rating in self._cands:
            if eid in seen or (newer_than and year < newer_than):
                continue
            hit = sum(profile.get(k, 0) * w * idf.get(k, 1.0) for k, w in feats.items())
            if hit <= 0:
                continue
            score = hit / math.sqrt(len(feats))
            if rating:
                score *= 0.75 + rating / 20      # nota 9: x1.2 · nota 4: x0.95
            if year >= year_now - 1:
                score *= 1.15
            out.append((score, e, feats))
        out.sort(key=lambda r: -r[0])
        return out

    def _pick(self, scored, reason_of, limit=ROW, skip=()):
        rows, per = [], Counter()
        skip = {self._id(x) for x in skip}
        names = set()   # o mesmo título em duas plataformas/pastas: aparece uma vez
        for _s, e, feats in scored:
            name = catalog_fold(display_title(e.label if isinstance(e, CatalogNode) else e.name))
            if name in names:
                continue
            genres = [k for k in feats if k.startswith("g:")]
            main = genres[0] if genres else "?"
            if per[main] >= PER_GENRE or self._id(e) in skip:
                continue
            per[main] += 1
            names.add(name)
            rows.append(e)
            self._reasons[self._id(e)] = reason_of(feats)
            if len(rows) >= limit:
                break
        return rows

    def _reason(self, profile):
        def why(feats):
            people = [k[2:] for k in feats if k[:2] in ("c:", "d:") and profile.get(k, 0) >= 1.0]
            if people:
                return "Com " + people[0].title()
            gs = sorted((k for k in feats if k.startswith("g:")),
                        key=lambda k: -profile.get(k, 0) * self._idf.get(k, 1.0))[:2]
            names = [GENRE_NAMES.get(g[2:], g[2:]) for g in gs if profile.get(g, 0) > 0]
            return ("Porque você gosta de " + " e ".join(names)) if names else ""
        return why

    # ---- fileiras do Início
    def rows(self):
        """[(título, itens)] — recalculado só quando o histórico ou a Minha lista mudam."""
        win = self.win
        if win.catalog is None:
            return []
        key = (id(win.catalog), len(win.cfg["resume"]), len(win.cfg["favorites"]), len(win.meta.movies),
               max((p[2] for p in win.cfg["resume"].values() if len(p) > 2), default=0) // 3600)
        if key == self._key:
            return self._rows
        watched, seen = self._watched()
        if not watched:
            self._key, self._rows = key, []
            return []
        profile = Counter()
        for e, weight, _when in watched:
            for k, w in features(self._info(e), self._path(e)).items():
                profile[k] += w * weight
        why = self._reason(profile)
        rows = []
        # novidades primeiro (lançamentos deste ano e do anterior); os recomendados mostram outros títulos
        news = self._pick(self._score_all(profile, seen, newer_than=time.localtime().tm_year - 1), why)
        best = self._pick(self._score_all(profile, seen), why, skip=news[:12])
        if best:
            rows.append(("Recomendados para você", best))
        # porque você assistiu: o título mais recente com informação
        recent = sorted((r for r in watched if r[2]), key=lambda r: -r[2])
        for e, _w, _when in recent[:3]:
            feats = features(self._info(e), self._path(e))
            if len(feats) < 3:
                continue
            similar = self._pick(self._score_all(Counter(feats), seen), lambda _f: "Parecido",
                                 skip=best[:8] + news[:8])
            if len(similar) >= 6:
                name = display_title(e.label if isinstance(e, CatalogNode) else series_parts(e.name)[0])
                rows.append((f"Porque você assistiu {name}", similar))
                break
        if news:
            rows.insert(1 if best else 0, ("Novidades para você", news))
        self._key, self._rows = key, rows
        return rows

    def reason(self, e):
        return self._reasons.get(self._id(e), "")

    def tip(self, shown):
        """Dica do dia: a melhor novidade (ou recomendação) que ainda não foi dica."""
        for title, items in self.rows():
            if title.startswith("Porque"):
                continue
            for e in items if title.startswith("Novidades") else []:
                if repr(self._id(e)) not in shown:
                    return e
        for title, items in self.rows():
            for e in items:
                if repr(self._id(e)) not in shown:
                    return e
        return None

    def tip_key(self, e):
        return repr(self._id(e))
