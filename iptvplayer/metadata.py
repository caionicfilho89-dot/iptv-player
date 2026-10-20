"""Sinopse, nota, gênero, elenco e capas de filmes e séries, pela API da conta Xtream da própria lista.

Só consulta informações (player_api.php), nunca abre vídeo: não ocupa a conexão da conta.
- séries: uma chamada traz todas (get_series), guardada em disco por alguns dias;
- filmes: get_vod_info na primeira vez que o filme aparece em destaque ou é aberto, guardado em disco;
  em segundo plano, devagar, o programa também vai buscando todos os outros (para a busca por ator/atriz)."""
import json
import re
import threading
import time
import urllib.parse
import urllib.request
from collections import Counter

from PyQt6.QtCore import QObject, pyqtSignal

from .catalog import catalog_fold, series_parts
from .log import log
from .paths import CACHE

SERIES_FILE = CACHE / "info_series.json"
MOVIES_FILE = CACHE / "info_filmes.json"
SERIES_MAX_AGE = 3 * 86400
TIMEOUT = 30
CAST_WORKERS = 3          # consultas ao mesmo tempo ao montar o elenco de todos os filmes
CAST_START_DELAY = 30     # s depois de abrir, para não disputar com o carregamento
SAVE_EVERY = 90           # s entre gravações do arquivo dos filmes (fica grande)


def find_account(channels):
    """(servidor, usuário, senha) da conta Xtream mais usada nos links da lista, ou None."""
    hosts = Counter()
    for ch in channels:
        p = urllib.parse.urlsplit(ch.url)
        pieces = [x for x in p.path.split("/") if x]
        if pieces and pieces[0] in ("live", "movie", "series"):
            pieces = pieces[1:]
        if len(pieces) == 3 and pieces[2].split(".")[0].isdigit():
            key = (f"{p.scheme}://{p.netloc}", pieces[0], pieces[1])
            hosts[key] += 1
            if hosts[key] > 200:   # a lista inteira é da mesma conta: não precisa olhar tudo
                break
    return hosts.most_common(1)[0][0] if hosts else None


def stream_id(url):
    tail = urllib.parse.urlsplit(url).path.rsplit("/", 1)[-1]
    sid = tail.split(".")[0]
    return sid if sid.isdigit() else ""


def split_cast(text):
    """"Sissy Spacek, Piper Laurie" -> ["Sissy Spacek", "Piper Laurie"]."""
    return [n.strip() for n in re.split(r"[,;/]", text or "") if len(n.strip()) > 1]


def _first_url(v):
    """backdrop_path vem como lista, texto de lista ou texto simples."""
    if isinstance(v, list):
        return v[0] if v else ""
    v = str(v or "").strip()
    if v.startswith("["):
        try:
            v = json.loads(v.replace("'", '"'))
            return v[0] if v else ""
        except ValueError:
            return ""
    return v


def _clean(d):
    """Só o que a tela usa, com nomes iguais para filme e série."""
    def num(x):
        try:
            return round(float(x), 1)
        except (TypeError, ValueError):
            return 0
    return {
        "plot": (d.get("plot") or d.get("description") or "").strip(),
        "genre": (d.get("genre") or "").strip(),
        "rating": num(d.get("rating")),
        "cast": (d.get("cast") or d.get("actors") or "").strip(),
        "director": (d.get("director") or "").strip(),
        "year": str(d.get("year") or d.get("releasedate") or d.get("release_date") or "")[:4],
        "duration": (d.get("duration") or "").strip(),
        "cover": d.get("cover") or d.get("cover_big") or d.get("movie_image") or "",
        "backdrop": _first_url(d.get("backdrop_path")),
    }


class MetaService(QObject):
    series_ready = pyqtSignal()
    movie_ready = pyqtSignal(str)     # url do filme
    cast_progress = pyqtSignal(int, int)   # filmes com elenco conhecido, total de filmes

    def __init__(self, parent=None):
        super().__init__(parent)
        self.account = None
        self.series = {}              # nome sem acento -> info
        self.movies = {}              # id do filme -> info
        self._pending = set()
        self._lock = threading.Lock()
        self._saved_at = time.time()
        self._crawling = False
        self.cast_total = 0           # filmes da conta na lista (0 = não há conta)
        self._actors = None           # índice de atores (montado quando pedem)
        self._actors_key = None
        self._actors_at = 0
        try:
            self.movies = json.loads(MOVIES_FILE.read_text(encoding="utf8"))
        except (OSError, ValueError):
            pass
        try:
            self.series = json.loads(SERIES_FILE.read_text(encoding="utf8")).get("items", {})
        except (OSError, ValueError):
            pass

    def _api(self, **params):
        server, user, pw = self.account
        q = urllib.parse.urlencode({"username": user, "password": pw, **params})
        req = urllib.request.Request(f"{server}/player_api.php?{q}", headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return json.loads(r.read())

    def owns(self, url):
        """O link é da conta (o número no fim dele é um id que a API conhece)."""
        return bool(self.account) and url.startswith(self.account[0] + "/")

    def start(self, channels):
        """Descobre a conta pelos links, atualiza as séries se estiverem velhas e começa o elenco dos filmes."""
        self.account = find_account(channels)
        if not self.account:
            return
        sids = [stream_id(ch.url) for ch in channels if "/movie/" in ch.url and self.owns(ch.url)]
        self.index_cast([s for s in sids if s])
        try:
            age = time.time() - SERIES_FILE.stat().st_mtime
        except OSError:
            age = None
        if age is None or age > SERIES_MAX_AGE or not self.series:
            threading.Thread(target=self._load_series, daemon=True, name="info-series").start()
        elif self.series:
            self.series_ready.emit()

    def _load_series(self):
        try:
            data = self._api(action="get_series")
            items = {}
            for d in data if isinstance(data, list) else []:
                name = catalog_fold(d.get("name") or "").strip()
                if name:
                    items[name] = _clean(d)
            tmp = SERIES_FILE.with_suffix(".tmp")
            tmp.write_text(json.dumps({"items": items}, ensure_ascii=False), encoding="utf8")
            tmp.replace(SERIES_FILE)
            self.series = items
            log.info("Informações de %d séries carregadas", len(items))
            self.series_ready.emit()
        except Exception as exc:  # noqa: BLE001
            log.warning("Não foi possível carregar as informações das séries: %s", type(exc).__name__)

    def series_info(self, title):
        """title: nome da série como está na lista ("Terra da Mafia [L]")."""
        return self.series.get(catalog_fold(title).strip())

    def series_of_episode(self, ch):
        return self.series_info(series_parts(ch.name)[0])

    def movie_info(self, ch, fetch=True):
        """Informações do filme; se ainda não tem, busca em segundo plano (avisa por movie_ready)."""
        sid = stream_id(ch.url) if self.owns(ch.url) or not self.account else ""
        if not sid:
            return None
        info = self.movies.get(sid)
        if info is None and fetch and self.account:
            with self._lock:
                if sid in self._pending:
                    return None
                self._pending.add(sid)
            threading.Thread(target=self._load_movie, args=(sid, ch.url), daemon=True, name="info-filme").start()
        return info

    def _load_movie(self, sid, url):
        try:
            data = self._api(action="get_vod_info", vod_id=sid)
            info = _clean((data or {}).get("info") or {}) if isinstance(data, dict) else _clean({})
        except Exception as exc:  # noqa: BLE001
            log.warning("Sinopse do filme não veio: %s", type(exc).__name__)
            info = None
        with self._lock:
            self._pending.discard(sid)
            if info is not None:
                self.movies[sid] = info
                if time.time() - self._saved_at > SAVE_EVERY:
                    self._save_movies()
        if info is not None:
            self.movie_ready.emit(url)

    # --- elenco de todos os filmes (para a busca por ator/atriz)
    def index_cast(self, sids):
        """Busca aos poucos as informações dos filmes que ainda não têm; continua de onde parou na próxima vez."""
        sids = list(dict.fromkeys(sids))
        self.cast_total = len(sids)
        todo = [s for s in sids if s not in self.movies]
        if not todo or self._crawling:
            return
        self._crawling = True
        threading.Thread(target=self._crawl, args=(todo,), daemon=True, name="info-elenco").start()

    def cast_ready(self):
        """(filmes com elenco já conhecido, total), para o aviso "ainda conhecendo o elenco"."""
        return min(len(self.movies), self.cast_total), self.cast_total

    def _crawl(self, todo):
        time.sleep(CAST_START_DELAY)
        log.info("Elenco: buscando informações de %d filmes em segundo plano", len(todo))
        it = iter(todo)
        state = {"fails": 0, "done": 0}

        def worker():
            while state["fails"] < 25:
                with self._lock:
                    sid = next(it, None)
                if sid is None:
                    return
                if sid in self.movies:
                    continue
                try:
                    data = self._api(action="get_vod_info", vod_id=sid)
                    info = _clean((data or {}).get("info") or {}) if isinstance(data, dict) else _clean({})
                except Exception:  # noqa: BLE001
                    state["fails"] += 1
                    time.sleep(3 * state["fails"])   # servidor ocupado: espera mais a cada erro seguido
                    continue
                state["fails"] = 0
                with self._lock:
                    self.movies[sid] = info
                    state["done"] += 1
                    if time.time() - self._saved_at > SAVE_EVERY:
                        self._save_movies()
                if state["done"] % 250 == 0:
                    self.cast_progress.emit(*self.cast_ready())
                time.sleep(0.05)

        threads = [threading.Thread(target=worker, daemon=True, name=f"info-elenco-{k}") for k in range(CAST_WORKERS)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        with self._lock:
            self._save_movies()
        self._crawling = False
        log.info("Elenco: %d filmes novos (%d erros seguidos no fim)", state["done"], state["fails"])
        self.cast_progress.emit(*self.cast_ready())

    def actors(self):
        """nome sem acento -> [nome, {ids de filmes}, {séries (nome sem acento)}]; remontado no máximo a cada 20 s."""
        key = (len(self.movies), len(self.series))
        if self._actors is not None and (key == self._actors_key or time.time() - self._actors_at < 20):
            return self._actors
        out = {}
        with self._lock:
            movies = list(self.movies.items())
        for sid, info in movies:
            for name in split_cast(info.get("cast")):
                out.setdefault(catalog_fold(name), [name, set(), set()])[1].add(sid)
        for title, info in list(self.series.items()):
            for name in split_cast(info.get("cast")):
                out.setdefault(catalog_fold(name), [name, set(), set()])[2].add(title)
        self._actors, self._actors_key, self._actors_at = out, key, time.time()
        return out

    def find_actors(self, query, limit=24):
        """Atores/atrizes com a busca no começo de alguma palavra do nome; os com mais títulos antes."""
        q = catalog_fold(query).strip()
        if len(q) < 3:
            return []
        found = [(v[0], len(v[1]) + len(v[2])) for k, v in self.actors().items()
                 if k.startswith(q) or f" {q}" in k]
        found.sort(key=lambda x: (-x[1], x[0]))
        return found[:limit]

    def actor_titles(self, name):
        """(ids de filmes, séries) com a pessoa no elenco."""
        v = self.actors().get(catalog_fold(name).strip())
        return (v[1], v[2]) if v else (set(), set())

    def _save_movies(self):
        self._saved_at = time.time()
        try:
            tmp = MOVIES_FILE.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.movies, ensure_ascii=False), encoding="utf8")
            tmp.replace(MOVIES_FILE)
        except OSError:
            pass

    def save(self):
        with self._lock:
            self._save_movies()
