"""Catálogo do PudimFlix: organiza a lista em Ao vivo, Filmes, Séries, Programas… pelos grupos da própria lista.

Determinístico (sem IA nem rede): cada item cai num caminho como ("Filmes", "Ação") ou
("Séries", "Netflix", "Round 6", "Temporada 01"). A soma de cada ramo bate com o total da lista."""
import re
import unicodedata
from collections import Counter
from dataclasses import replace
from urllib.parse import urlsplit

VIDEO_EXTENSIONS = {".mp4", ".mkv", ".avi", ".mov", ".webm", ".m4v", ".wmv", ".flv"}
EPISODE_RE = re.compile(r"\bS(\d{1,3})\s*E(\d{1,4})\b", re.I)
YEAR_RE = re.compile(r"\((19\d{2}|20\d{2})\)")
TAG_RE = re.compile(r"\[[^\]]*\]")
LABEL_ALIASES = {
    "acao": "Ação", "comedia": "Comédia", "animacao": "Animação", "familia": "Família",
    "ficcao cientifica": "Ficção científica", "documentarios": "Documentários",
    "kids": "Infantil", "legendadas": "Legendadas", "legendados": "Legendados",
    "stand up commedy": "Stand-up", "reels shorts": "Vídeos curtos",
    "series": "Séries", "radios": "Rádios", "outros": "Outros",
    "gerais": "Gerais", "variedades": "Variedades", "lancamentos": "Lançamentos",
}

LIVE, MOVIES, SERIES, VIDEOS, RADIOS, ADULT, UNSTABLE = (
    "Canais ao vivo", "Filmes", "Séries", "Vídeos e programas gravados", "Rádios", "Adultos (18+)",
    "Marcados pela lista como instáveis")
ROOT_ORDER = [LIVE, MOVIES, SERIES, VIDEOS, RADIOS, ADULT, UNSTABLE]
TV_SHOWS = (VIDEOS, "Programas de TV")
SHARED_LOGO_LIMIT = 30   # imagem repetida em mais séries que isso é genérica (não é a capa de nenhuma)
KIDS_WORDS = ("infantil", "kids", "anima", "famil", "desenho", "crianca", "disney")


def catalog_fold(text):
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()


def clean_catalog_label(text):
    text = str(text or "").strip()
    while text and not text[0].isalnum() and text[0] not in "[(":
        text = text[1:].lstrip()   # tira emojis de bandeira e símbolos do começo
    text = re.sub(r"\s+", " ", text).strip()
    return LABEL_ALIASES.get(catalog_fold(text), text) or "Sem nome"


def media_is_video(channel):
    """Filme, episódio ou vídeo gravado (tem começo e fim), pelo link."""
    if channel.kind in ("movie", "episode"):
        return True
    path = urlsplit(channel.url).path.lower()
    tail = path.rsplit("/", 1)[-1]
    suffix = "." + tail.rsplit(".", 1)[-1] if "." in tail else ""
    return suffix in VIDEO_EXTENSIONS or "/movie/" in path or "/series/" in path


def as_vod(channel):
    """Cópia marcada como filme/episódio, para o player tratar como algo com começo e fim."""
    if channel.kind in ("movie", "episode"):
        return channel
    return replace(channel, kind="episode" if EPISODE_RE.search(channel.name) else "movie")


def series_parts(name):
    match = EPISODE_RE.search(name)
    if not match:
        return name.strip() or "Sem título", "Episódios"
    title = name[:match.start()].strip(" -|:")
    return title or "Sem título", f"Temporada {int(match.group(1)):02d}"


def episode_order(channel):
    m = EPISODE_RE.search(channel.name)
    return (int(m.group(1)), int(m.group(2)), channel.name) if m else (9999, 9999, channel.name)


def episode_label(channel):
    m = EPISODE_RE.search(channel.name)
    return f"Episódio {int(m.group(2))}" if m else display_title(channel.name)


def display_title(name):
    """"Carrie, A Estranha (1976) [L]" -> "Carrie, A Estranha"."""
    t = YEAR_RE.sub("", TAG_RE.sub("", name))
    return re.sub(r"\s+", " ", t).strip(" -|") or name


def year_of(name):
    m = YEAR_RE.search(name)
    return int(m.group(1)) if m else 0


def badges(channel):
    """Selos curtos do cartão: legendado, 4K."""
    out = []
    blob = f"{channel.name} {channel.group}".lower()
    if "[l]" in blob or "legendad" in blob:
        out.append("LEG")
    if "4k" in blob or "uhd" in blob:
        out.append("4K")
    return out


def poster_url(url):
    """Capas do TMDB em tamanho de cartão (a lista manda a de 600 px, 4× mais pesada)."""
    return url.replace("/w600_and_h900_bestv2/", "/w342/") if url else url


def catalog_path(channel):
    group = (channel.group or "").strip()
    parts = [part.strip() for part in group.split("|") if part.strip()]
    first = catalog_fold(parts[0] if parts else "")
    video = media_is_video(channel)
    adult = bool(re.search(r"\[xxx\]|\badultos?\b|18\+", group, re.I))
    if "instaveis" in first or "fora do ar" in first:
        return (UNSTABLE,)
    if adult:
        subtype = "Vídeos" if video or first == "filmes" else "Canais ao vivo"
        return (ADULT, subtype, clean_catalog_label(parts[-1] if parts else group))
    if first == "series" and (video or len(parts) > 1):
        provider = clean_catalog_label(parts[1] if len(parts) > 1 else "Outras")
        title, season = series_parts(channel.name)
        return (SERIES, provider, title, season)
    if first == "filmes" and (video or len(parts) > 1):
        genre = clean_catalog_label(parts[1] if len(parts) > 1 else "Outros")
        return (MOVIES, genre)
    if first == "radios":
        return (RADIOS,)
    if video:
        label = clean_catalog_label(parts[0] if parts else group)
        if first == "esportes":
            label = "Esportes gravados"
        if first == "programas da tv":
            title, season = series_parts(channel.name)
            return (VIDEOS, "Programas de TV", title, season)
        return (VIDEOS, label)
    if first == "canais":
        provider = clean_catalog_label(parts[1] if len(parts) > 1 else "Outros")
        return (LIVE, "Brasil", provider)
    if len(parts) > 1:
        return (LIVE, "Outros países", clean_catalog_label(parts[0]), clean_catalog_label(parts[1]))
    return (LIVE, "Outros", clean_catalog_label(group))


def is_title_level(path):
    """Ramo cujos filhos são títulos (uma série, um programa) e não subcategorias."""
    return (len(path) == 2 and path[0] == SERIES) or tuple(path) == TV_SHOWS


def is_kids(path):
    return any(w in catalog_fold(label) for label in path for w in KIDS_WORDS)


class CatalogNode:
    __slots__ = ("path", "label", "indices", "children")

    def __init__(self, path=()):
        self.path = tuple(path)
        self.label = self.path[-1] if self.path else "Tudo"
        self.indices = []
        self.children = {}

    @property
    def count(self):
        return len(self.indices)


class ContentCatalog:
    def __init__(self, channels):
        self.channels = channels
        self.root = CatalogNode()
        self.nodes = {(): self.root}
        self.group_counts = Counter()
        self.by_url = {}
        for index, channel in enumerate(channels):
            path = catalog_path(channel)
            self.group_counts[channel.group] += 1
            self.by_url.setdefault(channel.url, index)
            node = self.root
            node.indices.append(index)
            for depth, label in enumerate(path):
                child = node.children.get(label)
                if child is None:
                    child = CatalogNode(path[:depth + 1])
                    node.children[label] = child
                    self.nodes[child.path] = child
                child.indices.append(index)
                node = child
        self.search_names = [catalog_fold(c.name) for c in channels]
        self._title_logos = {}
        self.bad_logos = self._find_bad_logos()
        self.validate()

    def title_nodes(self, node):
        """Séries e programas dentro do ramo (para mostrar a série inteira em vez de cada episódio)."""
        if is_title_level(node.path):
            return self.ordered_children(node, "list")
        out = [n for p, n in self.nodes.items() if len(p) == 3 and p[:len(node.path)] == node.path
               and is_title_level(p[:2])]
        return sorted(out, key=lambda n: n.indices[0])

    def _find_bad_logos(self):
        """Algumas listas repetem a mesma imagem em séries diferentes: essa imagem não identifica nenhuma."""
        users = Counter()
        for path, node in self.nodes.items():
            if len(path) == 3 and is_title_level(path[:2]):
                logos = Counter(self.channels[i].logo for i in node.indices if self.channels[i].logo)
                self._title_logos[path] = [u for u, _ in logos.most_common(3)]
                for u in logos:
                    users[u] += 1
        return {u for u, n in users.items() if n > SHARED_LOGO_LIMIT}

    def logo_of(self, entry):
        """Imagem confiável do item ou da série ("" quando a lista não tem uma)."""
        if isinstance(entry, CatalogNode):
            for u in self._title_logos.get(entry.path, ()):
                if u not in self.bad_logos:
                    return u
            return ""
        return "" if entry.logo in self.bad_logos else entry.logo

    def validate(self):
        if self.root.count != len(self.channels) or sum(n.count for n in self.root.children.values()) != self.root.count:
            raise ValueError("O catálogo não preservou todos os itens.")
        for node in self.nodes.values():
            if node.children and sum(child.count for child in node.children.values()) != node.count:
                raise ValueError("Contagem inconsistente em um ramo do catálogo.")
        return True

    def ordered_children(self, node, by="name"):
        """Filhos em ordem alfabética ("name"), por tamanho ("size") ou pela ordem da lista ("list")."""
        kids = list(node.children.values())
        if not node.path:
            order = {name: i for i, name in enumerate(ROOT_ORDER)}
            return sorted(kids, key=lambda n: (order.get(n.label, 100), catalog_fold(n.label)))
        if by == "size":
            return sorted(kids, key=lambda n: -n.count)
        if by == "list":
            return sorted(kids, key=lambda n: n.indices[0])  # a lista costuma trazer o mais novo primeiro
        return sorted(kids, key=lambda n: catalog_fold(n.label))

    def node(self, path):
        return self.nodes.get(tuple(path))

    def items(self, path):
        node = self.nodes.get(tuple(path))
        return [self.channels[i] for i in node.indices] if node else []

    def first(self, node):
        return self.channels[node.indices[0]]

    def path_of(self, channel):
        """Caminho completo do item (a pasta onde ele está)."""
        return catalog_path(channel)

    def search(self, query, limit=60):
        """Títulos de séries/programas, filmes, canais e vídeos cujo nome contém a busca."""
        q = catalog_fold(query.strip())
        out = {"titles": [], "movies": [], "live": [], "videos": []}
        if len(q) < 2:
            return out
        for path, node in self.nodes.items():
            if len(path) == 3 and is_title_level(path[:2]) and q in catalog_fold(node.label):
                out["titles"].append(node)
        out["titles"].sort(key=lambda n: n.indices[0])
        del out["titles"][limit:]
        for i, name in enumerate(self.search_names):
            if q not in name:
                continue
            ch = self.channels[i]
            root = catalog_path(ch)[0]
            bucket = {MOVIES: "movies", LIVE: "live", RADIOS: "live"}.get(root)
            if bucket is None:
                if root in (SERIES, UNSTABLE) or (root == VIDEOS and catalog_path(ch)[:2] == TV_SHOWS):
                    continue   # episódios aparecem pelo título da série
                bucket = "videos"
            if len(out[bucket]) < limit:
                out[bucket].append(ch)
        return out

    def summary(self):
        return {"total": self.root.count, "original_group_count": len(self.group_counts),
                "tree_node_count": len(self.nodes),
                "top_level": {n.label: n.count for n in self.ordered_children(self.root)}}
