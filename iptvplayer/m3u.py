"""Leitura de playlists M3U."""
import re
from dataclasses import dataclass, field, fields


@dataclass
class Channel:
    name: str
    url: str
    logo: str = ""
    group: str = ""
    quality: str = ""
    not247: bool = False
    geo: bool = False
    tvg_id: str = ""
    opts: list = field(default_factory=list)
    kind: str = ""            # "" canal ao vivo, "movie" filme, "series" série (abre a lista de episódios)
    catchup: str = ""         # assistir o que já passou: "xc", "default", "append", "shift", "flussonic"
    catchup_days: int = 0
    catchup_source: str = ""  # modelo do link do que já passou ({utc}, {duration}, {Y}-{m}-{d}…)
    catchup_offset: int = 0   # fuso do servidor em segundos (para {Y}…{S})
    country: str = ""         # país (tvg-country), quando a lista informa

    @classmethod
    def from_dict(cls, d):
        names = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in names})


# marcas que indicam um canal brasileiro (nome do grupo, nome do canal ou tvg_id .br)
_BR_WORDS = ("brasil", "brazil", "brazilian")
_BR_TOKEN_RE = re.compile(r"(?:^|[\s\(\[|:_-])br(?:$|[\s\)\]|:_-])", re.IGNORECASE)


def is_brazilian(ch):
    """True se o canal parece ser do Brasil (para priorizá-lo na lista)."""
    if (ch.tvg_id or "").lower().endswith(".br"):
        return True
    blob = f"{ch.group} {ch.name}".lower()
    if any(w in blob for w in _BR_WORDS):
        return True
    return bool(_BR_TOKEN_RE.search(ch.group or ""))


EXTINF_RE = re.compile(r'^#EXTINF:\s*-?\d+((?:\s+[\w-]+="[^"]*")*)\s*,(.*)$')
ATTR_RE = re.compile(r'([\w-]+)="([^"]*)"')
QUALITY_RE = re.compile(r"\((\d{3,4}[pi])\)")
TAG_RE = re.compile(r"\[[^\]]*\]|\(\d{3,4}[pi]\)")
FREETV_MARK_RE = re.compile("[ⓈⒼⓎ]")  # Ⓢ Ⓖ Ⓨ: marcas da lista Free-TV (SD, bloqueio por país, YouTube)


def _int(v):
    try:
        return int(float(v or 0))
    except ValueError:
        return 0


def parse_m3u_text(text):
    """Devolve (canais, atributos do cabeçalho #EXTM3U)."""
    channels, seen, header = [], set(), {}
    info, opts = None, []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#EXTM3U"):
            header.update(ATTR_RE.findall(line))
            continue
        if line.startswith("#EXTINF"):
            m = EXTINF_RE.match(line)
            if m:
                attrs, title = dict(ATTR_RE.findall(m.group(1))), m.group(2)
            else:
                attrs, title = dict(ATTR_RE.findall(line)), line.rsplit(",", 1)[-1]
            info, opts = (attrs, title.strip()), []
        elif line.startswith("#EXTVLCOPT:"):
            opts.append(":" + line[len("#EXTVLCOPT:"):])
        elif line.startswith("#"):
            continue
        else:
            if line in seen:
                info = None
                continue
            seen.add(line)
            attrs, title = info or ({}, line.rsplit("/", 1)[-1])
            q = QUALITY_RE.search(title)
            channels.append(Channel(
                name=re.sub(r"\s{2,}", " ", TAG_RE.sub("", FREETV_MARK_RE.sub("", title))).strip() or title,
                url=line,
                logo=attrs.get("tvg-logo", ""),
                group=attrs.get("group-title", "").split(";")[0],
                quality=q.group(1) if q else "",
                not247="Not 24/7" in title,
                geo="Geo-blocked" in title or "Ⓖ" in title,  # Ⓖ: marca de bloqueio por país do Free-TV
                tvg_id=attrs.get("tvg-id", ""),
                opts=opts,
                kind=attrs.get("x-kind", ""),
                catchup=(attrs.get("catchup") or attrs.get("catchup-type") or "").lower(),
                catchup_days=_int(attrs.get("catchup-days") or attrs.get("timeshift")),
                catchup_source=attrs.get("catchup-source", ""),
                catchup_offset=_int(attrs.get("x-catchup-offset")),
                country=attrs.get("tvg-country", "").split(";")[0][:2],
            ))
            info, opts = None, []
    return channels, header


_LEAD_NUM_RE = re.compile(r"^(#EXTINF[^,]*,)\s*\d{1,5}\s+(?=\S)")


def name_key(title):
    """Nome do canal sem qualidade/marcas, para achar o mesmo canal em listas diferentes."""
    n = TAG_RE.sub("", FREETV_MARK_RE.sub("", title)).lower().replace("+", " plus")
    n = re.sub(r"\b(hd|fhd|uhd|sd|4k|1080p|720p|480p)\b", "", n)
    return re.sub(r"[^a-z0-9]", "", n)


def keep_groups(text, groups):
    """Só os canais destes grupos (group-title) de uma lista."""
    out, keep = [], False
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("#EXTINF"):
            m = ATTR_RE.findall(line)
            keep = dict(m).get("group-title", "").split(";")[0] in groups
        if line.startswith("#EXTM3U") or keep or not line:
            out.append(line)
        if line and not line.startswith("#"):
            keep = False
    return "\n".join(out)


def merge_m3u(texts, skip_urls=(), skip_names=()):
    """Junta listas numa só sem repetir canal (mesmo link ou mesmo nome).
    texts: [(texto, tira_numero, país)] — tira_numero remove o número do canal antes do nome ("126 Veja+");
    país (ex. "BR") é marcado como tvg-country nos canais que não informam."""
    out, urls, names = ["#EXTM3U"], set(skip_urls), set(skip_names)
    for text, strip_num, country in texts:
        block = []
        for raw in text.splitlines():
            line = raw.strip().lstrip("﻿")
            if not line or line.startswith("#EXTM3U"):
                continue
            if line.startswith("#EXTINF"):
                if strip_num:
                    line = _LEAD_NUM_RE.sub(r"\1", line)
                if country and "tvg-country=" not in line:
                    line = line.replace("#EXTINF:-1", f'#EXTINF:-1 tvg-country="{country}"', 1)
                block = [line]
            elif line.startswith("#"):
                if block:
                    block.append(line)
            else:
                title = block[0].rsplit(",", 1)[-1] if block else ""
                key = name_key(title)
                if line not in urls and (not key or key not in names):
                    urls.add(line)
                    names.add(key)
                    out += block + [line]
                block = []
    return "\n".join(out) + "\n"


def parse_m3u(path):
    with open(path, encoding="utf-8", errors="replace") as f:
        return parse_m3u_text(f.read())


def header_epg_urls(header):
    raw = header.get("x-tvg-url") or header.get("url-tvg") or ""
    return [u.strip() for u in raw.split(",") if u.strip().startswith("http")]
