"""Agenda de jogos: acha no guia de programação os jogos (futebol e outros esportes) e anuncia antes de começar.

O guia já traz títulos como "Liverpool x Brighton - Ao Vivo", "NFL: Rams x Raiders - Ao Vivo" ou
"VT - Napoli x Roma" (VT = reprise). Daqui saem os dois times, o campeonato, o esporte e se é ao vivo.
Para não confundir com séries ("Spy X Family"), só conta como jogo o que passa num canal de esportes
ou tem palavra de esporte no título/campeonato/descrição. O mesmo jogo em vários canais vira um item só.
O aviso sai no canto da tela (clique = assistir) e, se ligado, falado pela voz do Windows.
"""
import queue
import re
import threading
import time
import unicodedata
from dataclasses import dataclass, field

from PyQt6.QtCore import QObject, pyqtSignal

from .log import log

# esporte: (nome, emoji, palavras no campeonato/título/descrição)
SPORTS = [
    ("americano", "Futebol americano", "🏈", ("nfl", "futebol americano", "super bowl", "ncaa football")),
    ("basquete", "Basquete", "🏀", ("nba", "nbb", "basquete", "basquetebol", "basketball", "wnba", "euroliga de basquete",
                                    "fiba")),
    ("volei", "Vôlei", "🏐", ("volei", "voleibol", "superliga feminina", "superliga masculina", "vnl")),
    ("futsal", "Futsal", "⚽", ("futsal",)),
    ("beisebol", "Beisebol", "⚾", ("mlb", "beisebol", "baseball", "world series")),
    ("hoquei", "Hóquei", "🏒", ("nhl", "hoquei", "hockey")),
    ("tenis", "Tênis", "🎾", ("tenis", "atp", "wta", "roland garros", "wimbledon", "us open", "australian open")),
    ("luta", "Lutas", "🥊", ("ufc", "boxe", "mma", "luta", "pfl", "bellator", "wwe", "jiu-jitsu")),
    ("handebol", "Handebol", "🤾", ("handebol", "handball")),
    ("rugby", "Rúgbi", "🏉", ("rugby", "rugbi")),
    ("futebol", "Futebol", "⚽", ("futebol", "brasileirao", "serie a", "serie b", "libertadores", "sul-americana",
                                  "copa do brasil", "premier league", "la liga", "laliga", "champions", "bundesliga",
                                  "ligue 1", "campeonato", "copa", "liga", "eliminatorias", "mundial de clubes",
                                  "estadual", "paulistao", "carioca", "mineiro", "gaucho", "eredivisie")),
]
SPORT_INFO = {key: (name, emoji) for key, name, emoji, _w in SPORTS}
# canais de esportes (nome ou id do guia, sem acento e em minúsculas)
SPORT_CHANNELS = ("sportv", "espn", "premiere", "band sports", "bandsports", "combate", "tnt sports", "caze",
                  "fox sports", "dazn", "eleven", "sport tv", "sporttv", "xsports", "x sports", "nsports",
                  "goat", "canal do inter", "fla tv", "furacao", "golf", "nba tv", "nfl network", "sky sports",
                  "bein", "tudn", "tyc sports", "sporting", "benfica tv", "eurosport", "ge tv", "cbf tv",
                  "globo", "record", "sbt", "band", "tv cultura", "redetv")
# canais abertos só valem com palavra de esporte (passam novelas, filmes…)
OPEN_TV = ("globo", "record", "sbt", "band", "tv cultura", "redetv")
REPLAY_RE = re.compile(r"^\s*(vt|reprise|compacto|melhores momentos|replay)\b\s*[-–:]?\s*", re.I)
LIVE_RE = re.compile(r"\s*[-–(]?\s*\b(ao vivo|live|en vivo)\b\s*\)?\s*$", re.I)
LIVE_PREFIX_RE = re.compile(r"^\s*\(?\s*(ao vivo|live|en vivo)\s*\)?\s*[-–:]\s*", re.I)
VS_RE = re.compile(r"^(?:(?P<comp>[^:]{2,70}?):\s*)?(?P<a>[^:]{2,50}?)\s+(?:x|X|vs\.?|VS\.?|versus)\s+(?P<b>[^:]{2,50}?)$")
NOT_TEAM_RE = re.compile(r"^(x|ii|iii|xx|xxi|\d+)$", re.I)
PAREN_RE = re.compile(r"\s*\(([^()]{1,30})\)\s*$")
NOT_GAME_RE = re.compile(r"\b(antevisao|pre-jogo|pre jogo|pos-jogo|pos jogo|resumo|melhores momentos|compacto|"
                         r"bastidores|analise|debate|magazine|highlights)\b")


def fold(text):
    t = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"\s+", " ", re.sub(r"[._/|]+", " ", t)).strip()


@dataclass
class Game:
    start: float
    stop: float
    title: str                       # título original do guia
    home: str
    away: str
    competition: str
    sport: str                       # chave de SPORTS
    live: bool                       # o guia diz "Ao vivo"/"Direto"
    replay: bool
    channels: list = field(default_factory=list)   # ids do guia onde passa

    @property
    def key(self):
        return f"{fold(self.home)}|{fold(self.away)}|{int(self.start // 600)}"

    @property
    def teams(self):
        return f"{self.home} x {self.away}"

    @property
    def emoji(self):
        return SPORT_INFO[self.sport][1]

    @property
    def sport_name(self):
        return SPORT_INFO[self.sport][0]


def _sport_of(text, channel):
    t = fold(text)
    for key, _name, _emoji, words in SPORTS:
        if any(re.search(r"\b" + re.escape(w) + r"\b", t) for w in words):
            return key
    c = fold(channel)
    if any(w in c for w in ("combate", "ufc", "ringside", "boxing", "boxe", "fight")):
        return "luta"
    if "nba" in c:
        return "basquete"
    if "nfl" in c:
        return "americano"
    return None


def parse_title(title, desc="", channel=""):
    """Game sem horário/canal (start=stop=0) se o título é um jogo, senão None."""
    t = (title or "").strip()
    if NOT_GAME_RE.search(fold(t)):
        return None              # antevisão, resumo, pré-jogo… não são o jogo
    replay = bool(REPLAY_RE.match(t))
    t = REPLAY_RE.sub("", t)
    live = bool(LIVE_RE.search(t) or LIVE_PREFIX_RE.match(t))
    t = LIVE_PREFIX_RE.sub("", LIVE_RE.sub("", t)).strip(" -–")
    # observações entre parênteses no fim: (Direto), (Rep), (Sporttv+ Replay), (2024), (4.ªj)
    while (m := PAREN_RE.search(t)):
        note = fold(m.group(1))
        if re.search(r"\b(rep|replay|reprise|diferido|vt)\b", note):
            replay = True
        elif re.search(r"\b(direto|ao vivo|live|en vivo)\b", note):
            live = True
        elif re.fullmatch(r"(19|20)\d\d", note) and int(note) < time.localtime().tm_year:
            replay = True        # luta/jogo antigo do arquivo do canal
        t = t[:m.start()].rstrip(" -–")
    # "Campeonato - Casa x Fora - Cidade": fica a parte com o "x", o que vem antes é o campeonato
    comp = ""
    parts = [p.strip() for p in re.split(r"\s+[-–]\s+", t)]
    if len(parts) > 1:
        i = next((k for k, p in enumerate(parts) if re.search(r"\s(x|X|vs\.?|VS\.?|versus)\s", p)), None)
        if i is None:
            return None
        comp, t = " - ".join(parts[:i]), parts[i]
    m = VS_RE.match(t)
    if not m:
        return None
    home, away = m.group("a").strip(" -–"), m.group("b").strip(" -–")
    comp = (m.group("comp") or "").strip() or comp
    # "Serie A 2026-27" / "Premier League 26/27" -> só o nome
    comp = re.sub(r"\s*\b((19|20)\d\d(\s*[-/]\s*\d{2,4})?|\d\d/\d\d)\b", "", comp).strip(" -–")
    if NOT_TEAM_RE.match(home) or NOT_TEAM_RE.match(away) or len(home) < 2 or len(away) < 2:
        return None
    ch = fold(channel)
    open_tv = any(re.search(r"\b" + w + r"\b", ch) for w in OPEN_TV)
    sport_channel = not open_tv and any(w in ch for w in SPORT_CHANNELS)
    in_title = _sport_of(f"{comp} {t}", "")
    if sport_channel:            # "Liverpool x Brighton" num canal de esportes: futebol, se nada disser outra coisa
        sport = in_title or _sport_of("", channel) or _sport_of(desc, channel) or "futebol"
    elif in_title:               # "NBA: Spurs x Mavericks" em qualquer canal
        sport = in_title
    elif open_tv and _sport_of(desc, ""):   # TV aberta: a descrição precisa falar do esporte
        sport = _sport_of(desc, "")
    else:
        return None              # "Spy X Family" num canal de desenhos não é jogo
    return Game(0, 0, title, home, away, comp, sport, live and not replay, replay)


def find_games(guide, start, end, channel_names=None):
    """Jogos do guia que começam entre start e end, um por partida, ordenados pelo horário.
    channel_names: id do guia -> nome bonito do canal (para reconhecer canal de esportes)."""
    games = {}
    for cid, progs in guide.progs.items():
        cname = (channel_names or {}).get(cid) or cid
        for s, e, title, desc in progs:
            if e <= start or s >= end or " x " not in f" {title.lower()} " and " vs" not in title.lower():
                continue
            g = parse_title(title, desc, cname)
            if g is None:
                continue
            g.start, g.stop = s, e
            prev = games.get(g.key)
            if prev is None:
                g.channels = [cid]
                games[g.key] = g
            else:
                if cid not in prev.channels:
                    prev.channels.append(cid)
                if g.live and not prev.live:   # ao vivo num canal, reprise em outro: vale o ao vivo
                    prev.live, prev.replay = True, False
                if not prev.competition and g.competition:
                    prev.competition = g.competition
    out = sorted(games.values(), key=lambda g: (g.start, g.teams))
    first = {}   # o mesmo jogo passando de novo horas depois (outro canal) é reprise
    for g in out:
        k = f"{fold(g.home)}|{fold(g.away)}"
        if k in first and g.start - first[k] > 1800 and not g.live:
            g.replay = True
        first.setdefault(k, g.start)
    return out


def matches_filters(game, cfg):
    if game.replay and not cfg["sports_replays"]:
        return False
    if cfg["sports_kinds"] and game.sport not in cfg["sports_kinds"]:
        return False
    teams = [fold(t) for t in cfg["sports_teams"] if t.strip()]
    if teams:
        both = f"{fold(game.home)} | {fold(game.away)} | {fold(game.competition)}"
        return any(t in both for t in teams)
    return True


def spoken(game, channel_name, minutes):
    """Frase do anúncio falado."""
    when = "agora" if minutes <= 1 else f"em {minutes} minutos"
    comp = f", {game.competition}" if game.competition else ""
    what = "Vai começar" if minutes > 1 else "Começou"
    where = f", no {channel_name}" if channel_name else ""
    if minutes <= 1:
        return f"{what}{comp}: {game.home} contra {game.away}{where}."
    return f"{game.sport_name}{comp}: {game.home} contra {game.away} começa {when}{where}."


class Announcer(QObject):
    """Fala os anúncios com a voz do Windows (SAPI), um de cada vez, sem travar a tela."""
    speaking = pyqtSignal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.q = queue.Queue()
        self._thread = None

    def say(self, text):
        self.q.put(text)
        if self._thread is None:
            self._thread = threading.Thread(target=self._loop, name="anuncios", daemon=True)
            self._thread.start()

    def _loop(self):
        voice = None
        while True:
            text = self.q.get()
            try:
                if voice is None:
                    import comtypes
                    import comtypes.client as cc
                    from .dub import _tokens
                    comtypes.CoInitialize()
                    voice = cc.CreateObject("SAPI.SpVoice", dynamic=True)
                    pick = next((t for t in _tokens() if "portug" in t.GetDescription().lower()), None)
                    if pick is not None:
                        voice.Voice = pick
                self.speaking.emit(True)
                voice.Speak(text, 0)
            except Exception as e:  # noqa: BLE001 — sem voz no Windows: fica só o aviso escrito
                log.warning("Anúncio falado falhou: %s", e)
            finally:
                if self.q.empty():
                    time.sleep(0.2)
                    self.speaking.emit(False)
