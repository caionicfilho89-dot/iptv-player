"""Categorias: listas embutidas, atualizadas do iptv-org e listas adicionadas pelo usuário."""
import uuid
from pathlib import Path

from . import xtream
from .paths import BASE, LIST_CACHE

IPTV_ORG = "https://iptv-org.github.io/iptv/"
FAV_KEY, RECENT_KEY, NEW_KEY = "__fav", "__recent", "__new"
SPECIAL_KEYS = (FAV_KEY, RECENT_KEY, NEW_KEY)
CUSTOM_PREFIX = "custom:"

FREE_TV = "https://raw.githubusercontent.com/Free-TV/IPTV/master/playlist.m3u8"

# chave (= nome do .m3u), rótulo, ícone, caminho no iptv-org ou URL completa (None = só local)
BUILTIN = [
    ("melhor_iptv", "Todos (melhores)", "tv", None),
    ("todos", "Todos os canais", "globe", "index.m3u"),
    ("brasil", "Brasil", "flag", "countries/br.m3u"),
    ("portugal", "Portugal", "flag", "countries/pt.m3u"),
    ("português", "Português", "chat", "languages/por.m3u"),
    ("filmes", "Filmes", "film", "categories/movies.m3u"),
    ("series", "Séries", "layers", "categories/series.m3u"),
    ("esportes", "Esportes", "trophy", "categories/sports.m3u"),
    ("notícias", "Notícias", "news", "categories/news.m3u"),
    ("documentários", "Documentários", "globe", "categories/documentary.m3u"),
    ("música", "Música", "music", "categories/music.m3u"),
    ("infantil", "Infantil", "smile", "categories/kids.m3u"),
    ("religiosos", "Religiosos", "heart", "categories/religious.m3u"),
    ("gerais", "Gerais", "antenna", "categories/general.m3u"),
    ("english", "English", "globe", "languages/eng.m3u"),
    ("español", "Español", "globe", "languages/spa.m3u"),
    ("mundo", "Mundo (Free-TV)", "antenna", FREE_TV),  # só canais abertos; complementa o iptv-org
]
# grátis com propaganda (FAST): os canais oficiais de cada serviço, de todos os países, das listas do iptv-org
# por serviço (trazem canais que ainda não entraram na lista geral)
IPTV_ORG_STREAMS = "https://raw.githubusercontent.com/iptv-org/iptv/master/streams/"
MJH = "https://i.mjh.nz/"


def _streams(*names):
    return tuple(IPTV_ORG_STREAMS + n + ".m3u" for n in names)


_PLUTO = ("mx", "us", "ca", "uk", "es", "fr", "it", "de", "at", "ch", "dk", "no", "se")
_SAMSUNG = ("br", "pt", "us", "mx", "ca", "uk", "ie", "es", "fr", "it", "de", "at", "ch", "be", "nl", "lu", "dk",
            "no", "se", "fi", "in", "au", "nz")
_RAKUTEN = ("es", "uk", "fr", "it", "de", "fi", "pl")
# chave, rótulo, ícone, listas, guias de programação
FAST = [
    ("pluto_br", "Pluto TV Brasil", "tv", _streams("br_pluto"), (MJH + "PlutoTV/br.xml.gz",)),
    ("pluto", "Pluto TV (mundo)", "tv", _streams(*(c + "_pluto" for c in _PLUTO)), (MJH + "PlutoTV/us.xml.gz",)),
    ("samsung", "Samsung TV Plus", "tv", _streams(*(c + "_samsung" for c in _SAMSUNG)),
     (MJH + "SamsungTVPlus/us.xml.gz",)),
    ("rakuten", "Rakuten TV", "tv", _streams(*(c + "_rakuten" for c in _RAKUTEN)), ()),
    ("plex", "Plex", "tv", _streams("us_plex"), (MJH + "Plex/us.xml.gz",)),
    ("roku", "The Roku Channel", "tv", _streams("us_roku"), (MJH + "Roku/all.xml.gz",)),
    ("tubi", "Tubi", "tv", _streams("us_tubi"), ()),
    ("xumo", "Xumo Play", "tv", _streams("us_xumo"), ()),
    ("stirr", "Stirr", "tv", _streams("us_stirr"), ()),
    ("vizio", "Vizio WatchFree+", "tv", _streams("us_vizio"), ()),
    ("tcl", "TCL tv+", "tv", _streams("us_tcl"), ()),
    ("distro", "Distro TV", "tv", _streams("us_distro", "uk_distro", "in_distro"), ()),
]
BUILTIN += [(k, label, ic, remote) for k, label, ic, remote, _epg in FAST]
FAST_KEYS = {f[0] for f in FAST}
FAST_EPG = {f[0]: list(f[4]) for f in FAST}
BUILTIN_KEYS = {b[0] for b in BUILTIN}


def builtin_entries():
    """Categorias embutidas + outros .m3u soltos na pasta do programa."""
    out = [(k, label, ic) for k, label, ic, remote in BUILTIN
           if remote or (BASE / f"{k}.m3u").exists() or (LIST_CACHE / f"{k}.m3u").exists()]
    for f in sorted(BASE.glob("*.m3u")):
        if f.stem not in BUILTIN_KEYS:
            out.append((f.stem, f.stem.capitalize(), "folder"))
    return out


def _full_url(remote):
    """Link da lista; uma categoria feita de várias listas (tupla) é baixada e juntada."""
    if isinstance(remote, tuple):
        return remote
    return remote if is_remote(remote) else IPTV_ORG + remote


def remote_url(key):
    for k, _, _, remote in BUILTIN:
        if k == key and remote:
            return _full_url(remote)
    return None


def is_remote(url):
    return url.lower().startswith(("http://", "https://"))


def new_custom(name, url):
    return {"id": uuid.uuid4().hex[:10], "name": name.strip() or "Minha lista", "url": url.strip()}


# partes extras de uma conta Xtream na barra lateral: sufixo, rótulo, ícone
VOD_PARTS = [("filmes", "Filmes", "film"), ("series", "Séries", "layers")]


def vod_parts(src):
    return VOD_PARTS if xtream.is_api_url(src["url"]) else []


def custom_key(src, part=""):
    return CUSTOM_PREFIX + src["id"] + (f"/{part}" if part else "")


def is_vod_key(key):
    return key.startswith(CUSTOM_PREFIX) and "/" in key


def _find(key, custom_sources):
    """Chave de lista do usuário -> (lista, parte), ou (None, "")."""
    sid, _, part = key[len(CUSTOM_PREFIX):].partition("/")
    return next((s for s in custom_sources if s["id"] == sid), None), part


def custom_source(key, custom_sources):
    """Lista do usuário a que a categoria pertence (também para filmes e séries de uma conta), ou None."""
    return _find(key, custom_sources)[0] if key.startswith(CUSTOM_PREFIX) else None


def custom_cache(src, part=""):
    return LIST_CACHE / (f"custom_{src['id']}" + (f"_{part}" if part else "") + ".m3u")


def custom_caches(src):
    """Todos os arquivos baixados de uma lista do usuário (com filmes e séries de uma conta Xtream)."""
    return [custom_cache(src)] + [custom_cache(src, part) for part, *_ in vod_parts(src)]


def file_for(key, custom_sources):
    """Arquivo local da categoria (versão baixada tem prioridade), ou None."""
    if key.startswith(CUSTOM_PREFIX):
        src, part = _find(key, custom_sources)
        if not src:
            return None
        p = custom_cache(src, part) if is_remote(src["url"]) else Path(src["url"])
        return p if p.exists() else None
    for p in (LIST_CACHE / f"{key}.m3u", BASE / f"{key}.m3u"):
        if p.exists():
            return p
    return None


def download_url(key, custom_sources):
    if key.startswith(CUSTOM_PREFIX):
        src, part = _find(key, custom_sources)
        if not src or not is_remote(src["url"]):
            return None
        return xtream.part_url(src["url"], part) if part else src["url"]
    return remote_url(key)


def cache_path(key, custom_sources):
    if key.startswith(CUSTOM_PREFIX):
        src, part = _find(key, custom_sources)
        return custom_cache(src, part)
    return LIST_CACHE / f"{key}.m3u"


def all_update_jobs(custom_sources):
    jobs = [(k, _full_url(r), LIST_CACHE / f"{k}.m3u") for k, _, _, r in BUILTIN if r]
    # filmes e séries de uma conta Xtream ficam de fora: baixados quando a categoria é aberta
    jobs += [(custom_key(s), s["url"], custom_cache(s)) for s in custom_sources if is_remote(s["url"])]
    return jobs
