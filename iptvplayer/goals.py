"""Alerta de gol: acompanha o placar ao vivo dos campeonatos (placar público da ESPN) e avisa quando sai gol
num jogo que passa nos seus canais — clicar leva ao canal (e volta 30 s se o canal já está aberto com buffer).

Enquanto há jogo rolando consulta a cada 45 s; sem jogo, a cada 10 min."""
import json
import threading
import time
import urllib.request

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from .log import log
from .sports import fold

LEAGUES = ["bra.1", "bra.2", "bra.copa_do_brazil", "conmebol.libertadores", "conmebol.sudamericana",
           "uefa.champions", "uefa.europa", "eng.1", "esp.1", "ita.1", "ger.1", "fra.1", "por.1", "fifa.world",
           "conmebol.america", "fifa.friendly"]
URL = "https://site.api.espn.com/apis/site/v2/sports/soccer/{}/scoreboard"
LIVE_EVERY = 45 * 1000
IDLE_EVERY = 10 * 60 * 1000
# palavras que não identificam um time ("Atlético", "Clube"…): o resto precisa bater com o guia
COMMON = {"atletico", "clube", "club", "esporte", "sport", "futebol", "fc", "ec", "sc", "ac", "de", "da", "do", "the",
          "cf", "deportivo", "sporting"}
# abreviações do guia e da ESPN
ALIASES = {"mg": "mineiro", "pr": "paranaense", "go": "goianiense", "rj": "", "sp": "", "man": "manchester",
           "utd": "united", "athletico": "paranaense", "inter": "internacional", "psg": "paris", "saint": "", "germain": ""}


def _words(name):
    out = set()
    for w in fold(name).replace("-", " ").split():
        w = ALIASES.get(w, w)
        if len(w) >= 2 and w not in COMMON:
            out.add(w)
    return out


def same_team(a, b):
    """'Atlético-MG' x 'Atletico Mineiro', 'Man United' x 'Manchester United' (mas não 'Man City')…"""
    wa, wb = _words(a), _words(b)
    if not wa or not wb:
        return False
    small, big = (wa, wb) if len(wa) <= len(wb) else (wb, wa)
    return small <= big


class GoalWatcher(QObject):
    goal = pyqtSignal(dict)       # {"home", "away", "hs", "as", "scorer", "minute", "league", "scorer_team"}
    scores = pyqtSignal(dict)     # placar dos jogos rolando: {(casa, fora): (gols casa, gols fora, relógio)}
    _polled = pyqtSignal(list, bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.last = {}            # id do jogo -> (gols casa, gols fora)
        self.events = {}          # id do jogo -> último estado (com os lances), para o "O que eu perdi?"
        self.live = {}
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.poll)
        self._polled.connect(self._on_polled)
        self._busy = False

    def start(self, delay_ms=15000):
        self.timer.start(delay_ms)

    def stop(self):
        self.timer.stop()

    def poll(self):
        if self._busy:
            return
        self._busy = True
        threading.Thread(target=self._fetch, name="placar", daemon=True).start()

    def _fetch(self):
        events, any_live = [], False
        try:
            for league in LEAGUES:
                try:
                    req = urllib.request.Request(URL.format(league), headers={"User-Agent": "Mozilla/5.0"})
                    with urllib.request.urlopen(req, timeout=10) as r:
                        data = json.load(r)
                except Exception:  # noqa: BLE001
                    continue
                for ev in data.get("events", []):
                    comp = (ev.get("competitions") or [{}])[0]
                    state = ((ev.get("status") or {}).get("type") or {}).get("state", "")
                    teams = {c.get("homeAway"): c for c in comp.get("competitors", [])}
                    if "home" not in teams or "away" not in teams:
                        continue
                    any_live |= state == "in"
                    goals = [d for d in comp.get("details", []) if (d.get("type") or {}).get("text", "").lower()
                             .startswith("goal") or d.get("scoringPlay")]
                    last_goal = goals[-1] if goals else {}
                    home_id = teams["home"]["team"].get("id", "")
                    plays = [{"kind": "red" if d.get("redCard") else "goal" if d in goals else "",
                              "minute": (d.get("clock") or {}).get("displayValue", ""),
                              "player": ((d.get("athletesInvolved") or [{}])[0]).get("displayName", ""),
                              "home": (d.get("team") or {}).get("id", "") == home_id,
                              "own": bool(d.get("ownGoal")), "pen": bool(d.get("penaltyKick"))}
                             for d in comp.get("details", []) if d in goals or d.get("redCard")]
                    scorer = ((last_goal.get("athletesInvolved") or [{}])[0]).get("displayName", "")
                    scorer_team = (last_goal.get("team") or {}).get("id", "")
                    events.append({
                        "id": ev.get("id"), "state": state, "league": (data.get("leagues") or [{}])[0].get("name", ""),
                        "home": teams["home"]["team"].get("displayName", ""),
                        "away": teams["away"]["team"].get("displayName", ""),
                        "home_id": teams["home"]["team"].get("id", ""),
                        "hs": int(teams["home"].get("score") or 0), "as": int(teams["away"].get("score") or 0),
                        "clock": (ev.get("status") or {}).get("displayClock", ""),
                        "scorer": scorer, "minute": (last_goal.get("clock") or {}).get("displayValue", ""),
                        "scorer_home": scorer_team == teams["home"]["team"].get("id", ""),
                        "plays": plays, "detail": ((ev.get("status") or {}).get("type") or {}).get("detail", ""),
                    })
        finally:
            self._polled.emit(events, any_live)

    def _on_polled(self, events, any_live):
        self._busy = False
        live = {}
        for e in events:
            before = self.last.get(e["id"])
            now = (e["hs"], e["as"])
            if e["state"] == "in":
                live[(e["home"], e["away"])] = (e["hs"], e["as"], e["clock"])
            if before is not None and e["state"] in ("in", "post") and sum(now) > sum(before):
                log.info("Gol: %s %d x %d %s (%s)", e["home"], e["hs"], e["as"], e["away"], e["scorer"] or "?")
                self.goal.emit(e)
            self.last[e["id"]] = now
            self.events[e["id"]] = e
        self.live = live
        self.scores.emit(live)
        self.timer.start(LIVE_EVERY if any_live else IDLE_EVERY)

    def find(self, home, away):
        """Último estado do jogo entre esses times (pelos nomes do guia), ou None."""
        for e in self.events.values():
            if (same_team(home, e["home"]) and same_team(away, e["away"])) or \
                    (same_team(home, e["away"]) and same_team(away, e["home"])):
                return e
        return None


def describe(e):
    """Placar e lances em texto: "Corinthians 1 x 0 Mirassol (67')" + uma linha por gol/expulsão."""
    lines = [f"{e['home']} {e['hs']} x {e['as']} {e['away']}" + (f"  ·  {e['detail']}" if e.get("detail") else "")]
    for p in e.get("plays", []):
        team = e["home"] if p["home"] else e["away"]
        icon = "🟥" if p["kind"] == "red" else "⚽"
        extra = " (contra)" if p["own"] else " (pênalti)" if p["pen"] else ""
        lines.append(f"{icon} {p['minute']} {p['player'] or '?'}{extra} — {team}")
    if len(lines) == 1:
        lines.append("Nenhum gol ainda.")
    return "\n".join(lines)
