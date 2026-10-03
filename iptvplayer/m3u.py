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

    @classmethod
    def from_dict(cls, d):
        names = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in names})


EXTINF_RE = re.compile(r'^#EXTINF:\s*-?\d+((?:\s+[\w-]+="[^"]*")*)\s*,(.*)$')
ATTR_RE = re.compile(r'([\w-]+)="([^"]*)"')
QUALITY_RE = re.compile(r"\((\d{3,4}[pi])\)")
TAG_RE = re.compile(r"\[[^\]]*\]|\(\d{3,4}[pi]\)")
FREETV_MARK_RE = re.compile("[ⓈⒼⓎ]")  # Ⓢ Ⓖ Ⓨ: marcas da lista Free-TV (SD, bloqueio por país, YouTube)


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
            ))
            info, opts = None, []
    return channels, header


def parse_m3u(path):
    with open(path, encoding="utf-8", errors="replace") as f:
        return parse_m3u_text(f.read())


def header_epg_urls(header):
    raw = header.get("x-tvg-url") or header.get("url-tvg") or ""
    return [u.strip() for u in raw.split(",") if u.strip().startswith("http")]
