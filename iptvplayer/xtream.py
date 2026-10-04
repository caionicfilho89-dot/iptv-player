"""Contas Xtream Codes (servidor + usuário + senha), o formato da maioria das operadoras de IPTV.

A conta vira uma lista personalizada cujo link é o player_api.php do servidor; na hora de baixar, a
lista M3U é montada pela API (categorias e canais ao vivo) com o guia (xmltv.php) no cabeçalho."""
import json
import re
import time
import urllib.parse
import urllib.request

API = "/player_api.php"
TIMEOUT = 30


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


def login(url):
    """Confere a conta; devolve o user_info do servidor ou levanta XtreamError com a explicação."""
    info = _get(url)
    user = info.get("user_info") if isinstance(info, dict) else None
    if not isinstance(user, dict) or str(user.get("auth", "0")) != "1":
        raise XtreamError("Usuário ou senha incorretos.")
    status = str(user.get("status") or "Active")
    if status.lower() != "active":
        names = {"expired": "vencida", "banned": "bloqueada", "disabled": "desativada"}
        raise XtreamError(f"A conta está {names.get(status.lower(), status)}.")
    return user


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


def _attr(v):
    return str(v or "").replace('"', "'").replace("\n", " ").strip()


def build_m3u(url):
    """Monta a lista M3U dos canais ao vivo da conta (bytes, UTF-8)."""
    server, user, password = parse_api_url(url)
    info = login(url)
    formats = info.get("allowed_output_formats") or ["m3u8"]
    ext = "m3u8" if "m3u8" in formats else formats[0]  # HLS permite pausar a TV ao vivo
    cats = _get(url, action="get_live_categories")
    groups = {str(c.get("category_id")): c.get("category_name", "") for c in cats if isinstance(c, dict)} \
        if isinstance(cats, list) else {}
    streams = _get(url, action="get_live_streams")
    if not isinstance(streams, list):
        raise XtreamError("O servidor não devolveu a lista de canais.")
    u, p = urllib.parse.quote(user, safe=""), urllib.parse.quote(password, safe="")
    epg = f"{server}/xmltv.php?" + urllib.parse.urlencode({"username": user, "password": password})
    lines = [f'#EXTM3U url-tvg="{epg}"']
    streams.sort(key=lambda s: (int(s.get("num") or 0) if str(s.get("num") or "").isdigit() else 0))
    for s in streams:
        if not isinstance(s, dict) or s.get("stream_id") is None:
            continue
        name = _attr(s.get("name")) or f"Canal {s['stream_id']}"
        lines.append(f'#EXTINF:-1 tvg-id="{_attr(s.get("epg_channel_id"))}" tvg-logo="{_attr(s.get("stream_icon"))}" '
                     f'group-title="{_attr(groups.get(str(s.get("category_id")), ""))}",{name}')
        lines.append(f"{server}/live/{u}/{p}/{s['stream_id']}.{ext}")
    if len(lines) == 1:
        raise XtreamError("A conta não tem canais ao vivo.")
    return ("\n".join(lines) + "\n").encode("utf-8")
