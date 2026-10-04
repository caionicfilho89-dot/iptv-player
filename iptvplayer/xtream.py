"""Contas Xtream Codes (servidor + usuário + senha), o formato da maioria das operadoras de IPTV.

A conta vira uma lista personalizada cujo link é o player_api.php do servidor; na hora de baixar, a
lista M3U é montada pela API: canais ao vivo (com o guia xmltv.php no cabeçalho e o que já passou),
filmes ou séries, conforme a parte da conta."""
import calendar
import json
import re
import time
import urllib.parse
import urllib.request

API = "/player_api.php"
TIMEOUT = 30

# partes de uma conta: sufixo da categoria -> ação da API que devolve os itens
PARTS = {"": "get_live_streams", "filmes": "get_vod_streams", "series": "get_series"}


class XtreamError(Exception):
    pass


def normalize_server(server):
    """'meuservidor.com:8080/' -> 'http://meuservidor.com:8080' (aceita também um link colado inteiro)."""
    s = server.strip()
    if not s:
        return ""
    if "://" not in s:
        s = "http://" + s
    p = urllib.parse.urlsplit(s)
    path = p.path
    for tail in (API, "/get.php", "/xmltv.php"):
        if path.endswith(tail):
            path = path[: -len(tail)]
    return urllib.parse.urlunsplit((p.scheme, p.netloc, path.rstrip("/"), "", ""))


def api_url(server, user, password):
    q = urllib.parse.urlencode({"username": user, "password": password})
    return f"{normalize_server(server)}{API}?{q}"


def is_api_url(url):
    return urllib.parse.urlsplit(url).path.endswith(API)


def parse_api_url(url):
    """Link do player_api.php -> (servidor, usuário, senha), ou None."""
    if not is_api_url(url):
        return None
    p = urllib.parse.urlsplit(url)
    q = urllib.parse.parse_qs(p.query)
    if "username" not in q or "password" not in q:
        return None
    return normalize_server(url), q["username"][0], q["password"][0]


def part_url(url, part):
    """Link usado para baixar uma parte da conta (canais, filmes ou séries)."""
    return url if not part else f"{url}&action={PARTS[part]}"


def _base(url):
    """Link da conta, sem a ação e os parâmetros dela."""
    return re.sub(r"&(action|series_id)=[^&]*", "", url)


_LIVE_RE = re.compile(r"(/(?:live|movie|series|timeshift)/)[^/]+/[^/]+/")
_QUERY_RE = re.compile(r"(username|password)=[^&]*")


def redact(url):
    """Tira usuário e senha do link antes de gravar no registro."""
    url = _LIVE_RE.sub(lambda m: m.group(1) + "***/***/", url)
    return _QUERY_RE.sub(lambda m: m.group(1) + "=***", url)


def _get(url, **params):
    if params:
        url += "&" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": "IPTV-Player"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            data = r.read()
    except OSError as e:
        raise XtreamError(f"Não foi possível falar com o servidor ({e}).") from e
    try:
        return json.loads(data.decode("utf-8", "replace"))
    except ValueError as e:
        raise XtreamError("O servidor não respondeu como um servidor Xtream Codes.") from e


def _account(url):
    info = _get(url)
    user = info.get("user_info") if isinstance(info, dict) else None
    if not isinstance(user, dict) or str(user.get("auth", "0")) != "1":
        raise XtreamError("Usuário ou senha incorretos.")
    status = str(user.get("status") or "Active")
    if status.lower() != "active":
        names = {"expired": "vencida", "banned": "bloqueada", "disabled": "desativada"}
        raise XtreamError(f"A conta está {names.get(status.lower(), status)}.")
    server = info.get("server_info")
    return user, server if isinstance(server, dict) else {}


def login(url):
    """Confere a conta; devolve o user_info do servidor ou levanta XtreamError com a explicação."""
    return _account(url)[0]


def describe(user):
    """Resumo da conta para mostrar ao usuário: validade e telas simultâneas."""
    parts = []
    exp = user.get("exp_date")
    if exp and str(exp).isdigit():
        parts.append("válida até " + time.strftime("%d/%m/%Y", time.localtime(int(exp))))
    elif "exp_date" in user:
        parts.append("sem data de validade")
    if user.get("max_connections"):
        n = int(user["max_connections"])
        parts.append(f"{n} tela{'s' if n != 1 else ''} ao mesmo tempo")
    return "Conta ativa" + (": " + ", ".join(parts) if parts else "")


def server_offset(server_info):
    """Fuso do servidor em segundos: o link do que já passou usa a hora local do servidor."""
    try:
        local = calendar.timegm(time.strptime(server_info["time_now"], "%Y-%m-%d %H:%M:%S"))
        return round((local - int(server_info["timestamp_now"])) / 900) * 900
    except (KeyError, ValueError, TypeError):
        return 0


def _attr(v):
    return str(v or "").replace('"', "'").replace("\n", " ").strip()


def _num(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return 0


def _groups(url, action):
    cats = _get(url, action=action)
    if not isinstance(cats, list):
        return {}
    return {str(c.get("category_id")): c.get("category_name", "") for c in cats if isinstance(c, dict)}


def _items(url, action, what):
    items = _get(url, action=action)
    if not isinstance(items, list):
        raise XtreamError(f"O servidor não devolveu a lista de {what}.")
    return [i for i in items if isinstance(i, dict)]


def build_m3u(url):
    """Monta a lista M3U de uma parte da conta (bytes, UTF-8), conforme a ação no link."""
    action = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query).get("action", [""])[0]
    base = _base(url)
    server, user, password = parse_api_url(base)
    info, server_info = _account(base)
    u, p = urllib.parse.quote(user, safe=""), urllib.parse.quote(password, safe="")
    lines = ["#EXTM3U"]
    if action == "get_vod_streams":
        groups = _groups(base, "get_vod_categories")
        for s in _items(base, "get_vod_streams", "filmes"):
            if s.get("stream_id") is None:
                continue
            ext = _attr(s.get("container_extension")) or "mp4"
            lines.append(f'#EXTINF:-1 x-kind="movie" tvg-logo="{_attr(s.get("stream_icon"))}" '
                         f'group-title="{_attr(groups.get(str(s.get("category_id")), ""))}",'
                         f'{_attr(s.get("name")) or "Filme"}')
            lines.append(f"{server}/movie/{u}/{p}/{s['stream_id']}.{ext}")
    elif action == "get_series":
        groups = _groups(base, "get_series_categories")
        for s in _items(base, "get_series", "séries"):
            if s.get("series_id") is None:
                continue
            lines.append(f'#EXTINF:-1 x-kind="series" tvg-logo="{_attr(s.get("cover"))}" '
                         f'group-title="{_attr(groups.get(str(s.get("category_id")), ""))}",'
                         f'{_attr(s.get("name")) or "Série"}')
            lines.append(f"{base}&action=get_series_info&series_id={s['series_id']}")
    else:
        formats = info.get("allowed_output_formats") or ["m3u8"]
        ext = "m3u8" if "m3u8" in formats else formats[0]  # HLS permite pausar a TV ao vivo
        offset = server_offset(server_info)
        groups = _groups(base, "get_live_categories")
        epg = f"{server}/xmltv.php?" + urllib.parse.urlencode({"username": user, "password": password})
        lines = [f'#EXTM3U url-tvg="{epg}"']
        streams = _items(base, "get_live_streams", "canais")
        streams.sort(key=lambda s: _num(s.get("num")))
        for s in streams:
            if s.get("stream_id") is None:
                continue
            archive = ""
            if _num(s.get("tv_archive")):  # o servidor guarda os últimos dias deste canal
                archive = (f' catchup="xc" catchup-days="{_num(s.get("tv_archive_duration")) or 1}"'
                           f' x-catchup-offset="{offset}"')
            name = _attr(s.get("name")) or f"Canal {s['stream_id']}"
            lines.append(f'#EXTINF:-1 tvg-id="{_attr(s.get("epg_channel_id"))}" '
                         f'tvg-logo="{_attr(s.get("stream_icon"))}" '
                         f'group-title="{_attr(groups.get(str(s.get("category_id")), ""))}"{archive},{name}')
            lines.append(f"{server}/live/{u}/{p}/{s['stream_id']}.{ext}")
    if len(lines) == 1:
        what = {"get_vod_streams": "filmes", "get_series": "séries"}.get(action, "canais ao vivo")
        raise XtreamError(f"A conta não tem {what}.")
    return ("\n".join(lines) + "\n").encode("utf-8")


def series_episodes(url):
    """Temporadas de uma série: (sinopse, {temporada: [(título, link, sinopse, duração em s)]})."""
    data = _get(url)
    if not isinstance(data, dict):
        raise XtreamError("O servidor não devolveu os episódios.")
    server, user, password = parse_api_url(_base(url))
    u, p = urllib.parse.quote(user, safe=""), urllib.parse.quote(password, safe="")
    eps = data.get("episodes") or {}
    if isinstance(eps, list):  # alguns servidores mandam uma lista por temporada em vez de um dicionário
        eps = {str(i + 1): e for i, e in enumerate(eps)}
    seasons = {}
    for season, items in eps.items():
        out = []
        for e in items if isinstance(items, list) else []:
            if not isinstance(e, dict) or e.get("id") is None:
                continue
            info = e.get("info") if isinstance(e.get("info"), dict) else {}
            num = _num(e.get("episode_num"))
            title = _attr(e.get("title")) or f"Episódio {num}"
            ext = _attr(e.get("container_extension")) or "mp4"
            out.append((title, f"{server}/series/{u}/{p}/{e['id']}.{ext}", _attr(info.get("plot")),
                        _num(info.get("duration_secs"))))
        if out:
            seasons[_num(season)] = out
    info = data.get("info") if isinstance(data.get("info"), dict) else {}
    return _attr(info.get("plot")), dict(sorted(seasons.items()))
