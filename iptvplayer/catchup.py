"""Assistir o que já passou (catch-up): monta o link de um programa do guia a partir do canal.

Segue os atributos de M3U usados por outros players (catchup, catchup-days, catchup-source):
- xc: servidores Xtream Codes (/live/usuário/senha/ID → /timeshift/usuário/senha/minutos/início/ID.ts)
- default: catchup-source é o link inteiro
- append: catchup-source é acrescentado ao link do canal
- shift: o link do canal com ?utc=início&lutc=agora
- flussonic: …/index.m3u8 → …/index-início-duração.m3u8
"""
import re
import time
import urllib.parse

DEFAULT_DAYS = 3
_PLACEHOLDER_RE = re.compile(r"\$?\{(\w+)(?::(\d+))?\}")
_XC_LIVE_RE = re.compile(r"^(https?://[^/]+(?:/[^/]+)*?)/(?:live/)?([^/]+)/([^/]+)/(\d+)(?:\.\w+)?$")


def mode(ch):
    m = (ch.catchup or "").lower()
    if not m and ch.catchup_days:
        m = "shift"  # só "timeshift=N" na lista: o jeito mais comum
    return {"fs": "flussonic", "flussonic-hls": "flussonic", "flussonic-ts": "flussonic",
            "timeshift": "shift"}.get(m, m)


def days(ch):
    return ch.catchup_days or DEFAULT_DAYS


def supports(ch):
    return mode(ch) in ("xc", "default", "append", "shift", "flussonic")


def available(ch, start, now=None):
    """O programa que começou em start pode ser assistido de novo?"""
    now = now or time.time()
    return supports(ch) and now - days(ch) * 86400 <= start < now - 60


def fill(template, start, stop, now, offset=0):
    """Troca {utc}, {duration}, {Y}-{m}-{d}:{H}-{M}… pelos valores do programa."""
    start, stop, now = int(start), int(stop), int(now)
    local = time.gmtime(start + offset)
    values = {
        "utc": start, "start": start, "timestamp": start, "utcend": stop, "end": stop, "lutc": now, "now": now,
        "duration": stop - start, "offset": now - start,
        "Y": f"{local.tm_year:04d}", "m": f"{local.tm_mon:02d}", "d": f"{local.tm_mday:02d}",
        "H": f"{local.tm_hour:02d}", "M": f"{local.tm_min:02d}", "S": f"{local.tm_sec:02d}",
    }

    def sub(m):
        name, div = m.group(1), m.group(2)
        if name not in values:
            return m.group(0)
        v = values[name]
        if div and isinstance(v, int):
            v = -(-v // int(div))  # {duration:60} = minutos, arredondando para cima
        return str(v)
    return _PLACEHOLDER_RE.sub(sub, template)


def url_for(ch, start, stop, now=None):
    """Link para assistir o programa (start–stop, em segundos desde 1970), ou None se o canal não permite."""
    now = now or time.time()
    stop = min(stop, now)  # programa ainda no ar: até agora
    m = mode(ch)
    url = ch.url
    if m == "xc":
        if ch.catchup_source:
            return fill(ch.catchup_source, start, stop, now, ch.catchup_offset)
        hit = _XC_LIVE_RE.match(url)
        if not hit:
            return None
        server, user, password, sid = hit.groups()
        return fill(f"{server}/timeshift/{user}/{password}/{{duration:60}}/{{Y}}-{{m}}-{{d}}:{{H}}-{{M}}/{sid}.ts",
                    start, stop, now, ch.catchup_offset)
    if m == "default":
        return fill(ch.catchup_source, start, stop, now, ch.catchup_offset) if ch.catchup_source else None
    if m == "append":
        return url + fill(ch.catchup_source, start, stop, now, ch.catchup_offset) if ch.catchup_source else None
    if m == "shift":
        sep = "&" if urllib.parse.urlsplit(url).query else "?"
        return f"{url}{sep}utc={int(start)}&lutc={int(now)}"
    if m == "flussonic":
        dur = int(stop - start)
        new, n = re.subn(r"/(index|video|mono|playlist)\.m3u8(\?.*)?$",
                         lambda x: f"/{x.group(1)}-{int(start)}-{dur}.m3u8{x.group(2) or ''}", url)
        if n:
            return new
        new, n = re.subn(r"/mpegts(\?.*)?$", lambda x: f"/timeshift_abs-{int(start)}.ts{x.group(1) or ''}", url)
        return new if n else None
    return None
