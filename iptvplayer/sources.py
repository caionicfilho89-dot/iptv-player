"""Categorias: listas embutidas, atualizadas do iptv-org e listas adicionadas pelo usuário."""
import uuid
from pathlib import Path

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


def custom_key(src):
    return CUSTOM_PREFIX + src["id"]


def custom_cache(src):
    return LIST_CACHE / f"custom_{src['id']}.m3u"


def file_for(key, custom_sources):
    """Arquivo local da categoria (versão baixada tem prioridade), ou None."""
    if key.startswith(CUSTOM_PREFIX):
        src = next((s for s in custom_sources if custom_key(s) == key), None)
        if not src:
            return None
        p = custom_cache(src) if is_remote(src["url"]) else Path(src["url"])
        return p if p.exists() else None
    for p in (LIST_CACHE / f"{key}.m3u", BASE / f"{key}.m3u"):
        if p.exists():
            return p
    return None


def download_url(key, custom_sources):
    if key.startswith(CUSTOM_PREFIX):
        src = next((s for s in custom_sources if custom_key(s) == key), None)
        return src["url"] if src and is_remote(src["url"]) else None
    return remote_url(key)


def cache_path(key, custom_sources):
    if key.startswith(CUSTOM_PREFIX):
        src = next(s for s in custom_sources if custom_key(s) == key)
        return custom_cache(src)
    return LIST_CACHE / f"{key}.m3u"


def all_update_jobs(custom_sources):
    jobs = [(k, _full_url(r), LIST_CACHE / f"{k}.m3u") for k, _, _, r in BUILTIN if r]
    jobs += [(custom_key(s), s["url"], custom_cache(s)) for s in custom_sources if is_remote(s["url"])]
    return jobs
