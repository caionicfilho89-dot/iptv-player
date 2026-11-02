"""Pudinho: o assistente do Pudim, um chat como o dos sites.

Indica filmes e séries do catálogo da pessoa (pela busca por sentido das sinopses), acha títulos pela descrição,
explica como usar o app, responde sobre os jogos do dia, confere quais lançamentos em alta na internet já estão no
catálogo e executa comandos ("abre o SporTV", "liga a legenda").
O texto das respostas sai do Gemma 3 (a mesma IA da legenda), na placa de vídeo, sem internet. Sem a IA,
responde com modelos prontos (as indicações e os jogos continuam funcionando)."""
import html
import random
import re
import threading
import time
import unicodedata
import urllib.parse
import urllib.request

from PyQt6.QtCore import QObject, Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton, QScrollArea, QSizePolicy,
                             QVBoxLayout, QWidget)

from .catalog import MOVIES, CatalogNode, display_title
from .goals import same_team
from .log import log
from .theme import T, app_icon_pixmap
from .voice import parse_command
from .widgets import make_btn

NAME = "Pudinho"
MAX_OPTIONS = 6          # títulos que a IA recebe (e que aparecem como botões) numa indicação
HISTORY_TURNS = 6        # falas anteriores que vão junto para a IA entender "e esse outro?"

PERSONA = (f"Você é o {NAME}, o assistente do Pudim, um app de TV, filmes e séries para Windows. Seu mascote é um "
           "pudim vivo, um monstrinho simpático. Fale em português do Brasil, informal, simpático e com uma pitada "
           "de humor, em respostas curtas (no máximo 4 frases), sem listas, sem markdown e sem emojis em excesso. "
           "Vá direto ao ponto: não comece com saudação nem se apresente de novo, a não ser que a pessoa "
           "cumprimente. Fale de você na primeira pessoa (\"eu\", ou pelo nome Pudinho) e fale com a pessoa como \"você\". "
           "Nunca invente filmes, séries, canais, placares ou funções que não estejam nas informações que você "
           "recebeu. Se não souber, diga que não sabe.")

MANUAL = """MANUAL DO PUDIM
- Início: fileiras de filmes, séries e canais ao vivo. Clique num cartão para abrir a página com sinopse, elenco e o botão Assistir (ou Continuar).
- Busca (Ctrl+F): por nome, por assunto ("drogas", "viagem no tempo") ou pela descrição ("o cara que acorda sempre no mesmo dia"). Também busca atores.
- Comando de voz (Ctrl+Espaço ou o microfone ao lado da busca): "coloca no SporTV", "jogo do Corinthians", "volta 30 segundos", "liga a legenda".
- Legenda traduzida por IA (Ctrl+T ou botão CC): canais em outra língua ganham legenda em português, ao vivo, feita no PC.
- Dublagem por IA (Ctrl+U ou menu Mais): uma voz em português lê a legenda por cima do canal.
- Mandar pra TV (Ctrl+Shift+T ou botão da TV): o canal, filme ou episódio passa na Smart TV pelo player dela; o Pudim precisa ficar aberto no PC. Pausar, voltar, avançar, a barra de tempo e o volume do Pudim controlam a TV.
- Voltar e avançar 30 segundos: Ctrl+← e Ctrl+→. Ir para o ao vivo: Ctrl+End. Mudo: Ctrl+M.
- Voltar ao canal anterior: Ctrl+B. Favoritar: Ctrl+D. Guia de programação: Ctrl+G. Grade de todos os canais: Ctrl+Shift+G.
- Gravar canal: Ctrl+R. Foto da tela: Ctrl+S. Janela flutuante: Ctrl+P. Áudio e legenda do canal: Ctrl+Shift+A.
- Zapping (troca de canal sozinho): Ctrl+Z. Surpreenda-me (canal aleatório): Ctrl+Shift+S. Explorar canais novos: Ctrl+E.
- "O que eu perdi?" (Ctrl+Shift+R): a IA resume o que passou no canal enquanto você estava fora.
- Pular intervalo: no menu Mais; percebe quando o logo do canal some (comerciais) e avisa quando o programa volta.
- Alerta de gol: avisa quando sai gol no jogo que você acompanha, com o placar ao vivo.
- Mais como este: na página de um filme ou série, uma fileira com títulos parecidos.
- Baixar: na página do filme ou série, botão Baixar, para assistir sem internet (aba Baixados).
- Controle pelo celular: no Menu do Início, mostra um QR code para usar o celular como controle.
- Retrospectiva (Ctrl+Shift+H): seus canais mais vistos e conquistas.
- Listas: no Menu do Início dá para adicionar sua lista M3U ou entrar com a conta (Xtream).
- Apagar a tela do PC (Ctrl+Shift+B ou botão da lua): os monitores ficam pretos e desligam a luz enquanto o filme passa na TV; qualquer tecla ou clique volta.
- Comparar títulos: clicar num título indicado pelo Pudinho abre a janela de comparação, até 4 lado a lado (capa, nota, duração, elenco, sinopse). O botão "Não consigo decidir" sorteia um deles.
- Modo casal: diga "eu gosto de suspense e ela de comédia" e o Pudinho procura títulos que agradam os dois.
- Dormir quando o filme na TV acabar: no menu Mais; no fim do filme a tela apaga e o PC dorme (1 minuto para cancelar com qualquer tecla).
- Continuar de onde parou: filmes vistos na TV também guardam onde você parou (fileira Continuar assistindo e botão Continuar).
- Pudinho (Ctrl+J): este chat."""

RE_HELP = re.compile(r"^(como|onde|cade|qual|quais|o que e|o que significa|pra que|para que|por que|porque|tem como|"
                     r"da pra|da para|consigo|nao consigo|nao (ta|esta) (funcionando|indo|abrindo)|ajuda|me ajuda)\b|"
                     r"\b(atalho|configur|botao|tecla|funciona|serve)\b")
RE_SPORT = re.compile(r"\b(jogo|jogos|joga|jogam|placar|gol|gols|partida|campeonato|brasileirao|libertadores|"
                      r"copa|futebol|rodada|quanto (ta|esta|ficou))\b")
RE_PICK = re.compile(r"\b(indica|indique|recomenda|recomende|sugere|sugira|sugest|quero ver|quero assistir|"
                     r"pra assistir|para assistir|pra ver|algum filme|alguma serie|algo|filme|filmes|serie|series|"
                     r"parecid|tipo|igual|estilo|semelhante|documentario|desenho|anime|novela|"
                     r"terror|comedia|suspense|romance|acao|drama|ficcao)\b")
PARTNER = (r"ela|ele|minha namorada|meu namorado|minha esposa|meu marido|minha mulher|minha mina|meu boy|"
           r"a namorada|o namorado|a patroa|o mozao|a mozao|minha noiva|meu noivo|minha crush")
RE_COUPLE = re.compile(r"(?:gosto|curto|prefiro|amo|adoro)(?: mais)?(?: de| do| da)? (.+?)(?:,| e) (?:" + PARTNER
                       + r")(?: ja| so)?(?: gosta| curte| prefere| ama| adora)?(?: mais)?(?: de| do| da)? (.+)$")
RE_COUPLE_TAIL = re.compile(r"\s*(?:,|\b(?:o que|me indica|indica|me recomenda|recomenda|qual|alguma|algum|pra gente|"
                            r"pra nos|juntos)\b).*$")
RE_SIMILAR = re.compile(r"\b(?:parecid[oa]s?|tipo|igual|estilo|semelhantes?|no estilo|na linha)"
                        r"(?: (?:com|a|ao|de|do|da|que))? (.+)$")
FILLER = re.compile(r"\b(me|pra mim|por favor|pfv|pf|voce|vc|ai|indica|indique|recomenda|recomende|sugere|sugira|"
                    r"alguma sugestao|sugestao|quero|queria|ver|assistir|um|uma|uns|umas|algum|alguma|algo|bom|boa|"
                    r"legal|pra|para|hoje|agora)\b")
RE_RELEASE = re.compile(r"\b(lancamento|lancamentos|lancou|estreia|estreias|estreou|estrearam|novidade|novidades|"
                        r"saiu agora|sairam|recem lancad|filmes? novos?|series? novas?|novos filmes|novas series|"
                        r"em alta|do momento|bombando|cinema)\b")
RELEASE_QUERIES = ("estreia filme cinema", "estreias da semana cinema", "filme estreia Netflix",
                   'filme estreia "Prime Video" OR Max OR "Disney+" OR "Apple TV"',
                   'série estreia Netflix OR "Prime Video" OR Max')
RE_MORE = re.compile(r"^(me )?(indica|indique|mostra|manda|quero|tem)? ?(outra coisa|outras? (opcoes|opcao|coisas?|"
                     r"sugestoes|indicacoes)|outros?( filmes?| titulos?| series?)?|mais (opcoes|sugestoes|coisas?)|"
                     r"nenhum desses|nao gostei( desses)?)( por favor| pra mim| ai| ae| pf| pfv)?$")
MORE_TEXT = "Me indica outras opções"
RE_QUOTED = re.compile(r"[“\"‘']([^”\"’']{2,60})[”\"’']")
# "me faz um resumo de X", "do que se trata X?", "vale a pena X?", "resume sem spoiler X"
RE_SUMMARY = re.compile(r"\b(resumo|resume|resumir|sinopse|do que (?:se trata|trata|e|eh)|sobre o que (?:e|eh)|"
                        r"qual (?:a|e) (?:a )?historia|conta (?:sobre|um pouco)|me fala (?:sobre|de)|vale a pena|"
                        r"sem spoiler)\b")
# "por que você me indicou isso?", "pq escolheu esses?", "por que esse?"
RE_WHY = re.compile(r"\b(por ?que|pq)\b.{0,30}\b(indic|recomend|sugeri|escolh|esse|esses|essa|essas|isso|desses)\b")
# "não sei o que ver", "me ajuda a escolher", "tanto faz, escolhe por mim"
RE_LOST = re.compile(r"\b(nao sei o que (?:ver|assistir|botar|por)|me ajuda a (?:escolher|decidir)|"
                     r"escolhe (?:por mim|voce)|voce escolhe|to indeciso|sem ideia|me surpreende|surpresa|"
                     r"o que (?:eu )?(?:vejo|assisto|poderia ver))\b")
QUIZ_GENRES = [("😱 Terror", "terror"), ("😂 Comédia", "comédia"), ("💥 Ação", "ação"),
               ("🔎 Suspense", "suspense"), ("❤️ Romance", "romance"), ("🎲 Surpresa", "__any__")]
QUIZ_ANY = ["terror", "comédia", "ação", "suspense", "romance", "ficção científica", "drama", "aventura"]
QUIZ_TIMES = [("⚡ Rapidinho (até ~1h40)", "rapido"), ("🍿 Filmão (2h+)", "longo"), ("🤷 Tanto faz", "qualquer")]
RELEASES_EVERY = 3 * 3600      # a lista de lançamentos da internet vale por 3 horas
_releases = {"at": 0.0, "titles": []}
COMMANDS = {"seek", "pause", "stop", "mute", "volume", "captions", "dub", "tv", "home", "missed", "channel"}
DONE = {"seek": "Pronto, mexi no tempo do vídeo.", "pause": "Feito: pausei (ou continuei) pra você.",
        "stop": "Desliguei o vídeo.", "mute": "Mexi no som.", "volume": "Ajustei o volume.",
        "captions": "Prontinho, mexi na legenda.", "dub": "Prontinho, mexi na dublagem.",
        "tv": "Mandando pra TV!", "home": "Voltei pro Início.", "missed": "Vou ver o que você perdeu…",
        "channel": "Abrindo aí!"}

# "tenho 2 horas", "só tenho uma hora e meia", "um filme de até 90 minutos", "cabe em 2h?"
RE_TIME = re.compile(r"\b(tenho|so tenho|tempo|cabe|caiba|caber|dura[rt]?|de ate|ate|no maximo|menos de|rapidinho|"
                     r"curtinho|curto|rapido)\b.{0,24}?"
                     r"(\d+|meia|uma|um|duas|dois|tres|quatro)\s*(h|hr|hrs|hora|horas|min|minuto|minutos)")
NUM_WORDS = {"uma": 1, "um": 1, "duas": 2, "dois": 2, "tres": 3, "quatro": 4}


def parse_minutes(t):
    """Minutos disponíveis a partir do pedido ("2 horas", "1h30", "uma hora e meia", "90 minutos")."""
    total = 0
    for m in re.finditer(r"(\d+)\s*h(?:oras?|rs?|r\b)?(?:\s*(?:e\s*)?(\d+))?", t):
        total += int(m.group(1)) * 60 + (int(m.group(2)) if m.group(2) else 0)
    if total == 0:
        for m in re.finditer(r"(\d+)\s*min\w*", t):
            total += int(m.group(1))
    if total == 0:
        for w, v in NUM_WORDS.items():
            if re.search(rf"\b{w}\s+horas?\b", t):
                total += v * 60
    if re.search(r"\bmeia\s+hora\b", t):
        total = 30
    elif total and re.search(r"\be\s+meia\b", t):
        total += 30
    return total


def dur_minutes(s):
    """Duração "HH:MM:SS" (ou "MM:SS", ou "120") em minutos."""
    parts = str(s or "").strip().split(":")
    try:
        if len(parts) == 3:
            h, m, sec = (int(x) for x in parts)
            return h * 60 + m + (1 if sec >= 30 else 0)
        if len(parts) == 2:
            return int(parts[0])
    except ValueError:
        pass
    m = re.search(r"\d+", str(s or ""))
    return int(m.group()) if m else 0


def fold(text):
    t = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", t)).strip()


SPORT_WORDS = {"jogo", "jogos", "joga", "jogam", "hoje", "amanha", "agora", "placar", "quanto", "esta", "ficou",
               "partida", "campeonato", "brasileirao", "libertadores", "copa", "futebol", "rodada", "quais", "qual",
               "quando", "onde", "passa", "canal", "time", "tem", "algum", "alguma", "contra", "gols", "assistir"}


def clean_reply(text):
    t = re.sub(r"\*\*?|__|^#+\s*", "", text or "", flags=re.M)
    t = re.sub(r"\[[^\]\n]{1,80}\]", "", t)   # às vezes ela desenha "[Mostrar X]": os botões de verdade vêm embaixo
    t = re.sub(rf"^{NAME}\s*:\s*", "", t.strip(), flags=re.I)
    return re.sub(r"\n{3,}", "\n\n", t).strip()


def _title_key(e):
    name = fold(display_title(e.label if isinstance(e, CatalogNode) else e.name))
    return re.sub(r"\s+", " ", re.sub(r"\b(4k|uhd|fhd|hd|sd|1080p|720p|h265|legendado|dublado|leg|dub)\b", "",
                                      name)).strip()


def release_titles():
    """Títulos de filmes e séries que estão estreando (cinema e streaming, últimas 2 semanas), tirados das aspas
    das manchetes do Google Notícias, os mais citados primeiro. Guardados por 3 horas."""
    if _releases["titles"] and time.time() - _releases["at"] < RELEASES_EVERY:
        return _releases["titles"]
    from . import news
    count = {}
    for q in RELEASE_QUERIES:
        url = ("https://news.google.com/rss/search?q=" + urllib.parse.quote(q + " when:14d")
               + "&hl=pt-BR&gl=BR&ceid=BR:pt-419")
        try:
            raw = urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"}),
                                         timeout=12).read()
        except OSError as e:
            log.info("Pudinho: lançamentos (%s): %s", q, e)
            continue
        for item in news.parse_rss(raw):
            for title in RE_QUOTED.findall(item["title"]):
                title = title.strip(" .,:;!?")
                if title and len(title.split()) <= 8:
                    key = fold(title)
                    name, n = count.get(key, (title, 0))
                    count[key] = (name, n + 1)
    titles = [name for name, _n in sorted(count.values(), key=lambda x: -x[1])]
    if titles:
        _releases.update(at=time.time(), titles=titles)
    return titles


def _mentioned_first(answer, actions):
    """Botões dos títulos que a resposta citou primeiro; se citou 2 ou mais, só eles."""
    low = fold(answer)
    hit = [a for a in actions if fold(a[0][2:]) and fold(a[0][2:]) in low]
    return hit if len(hit) >= 2 else hit + [a for a in actions if a not in hit]


class Brain(QObject):
    """Entende o pedido, junta as informações (catálogo, jogos, manual) e pede a resposta para a IA."""
    answered = pyqtSignal(int, str, object)   # nº do pedido, texto, [(rótulo, função)]
    _reply = pyqtSignal(int, str, object)

    def __init__(self, win):
        super().__init__(win)
        self.win = win
        self.history = []        # [(quem, texto)] para a IA
        self._req = 0
        self._llm = None
        self._llm_lock = threading.Lock()
        self._last = None        # última indicação: ("indicacao", texto) ou ("lancamentos", texto), para o "outras"
        self._shown = set()      # títulos já indicados nela (não repetem no "Indicar outras opções")
        self._last_entries = []  # os títulos da última indicação, para explicar "por que indiquei"
        self._quiz = None        # "não sei o que ver": {step, genero} enquanto o questionário está ativo
        self._reply.connect(self.answered)

    # ---- pedido (chega na tarefa principal; a IA roda em outra)
    def ask(self, text):
        self._req += 1
        req = self._req
        t = fold(text)
        action, arg = parse_command(text)
        try:
            if self._quiz is not None and self._quiz_answer(req, text, t):
                return
            if RE_LOST.search(t):
                return self._start_quiz(req, text)
            if self._last and RE_MORE.search(t):   # "indica outra coisa": mesmo pedido, sem repetir títulos
                kind, first = self._last
                question = f"{first} (quero outras opções, diferentes das que você já indicou)"
                if kind == "lancamentos":
                    return self._think(req, question, kind, "", [], prepare=lambda: self._release_info(more=True))
                if kind == "tempo":
                    info, actions = self._time_info(first, fold(first), parse_minutes(fold(first)), more=True)
                    return self._think(req, question, kind, info, actions)
                info, actions = self._pick_info(first, fold(first), more=True)
                return self._think(req, question, kind, info, actions)
            if self._last_entries and RE_WHY.search(t):
                return self._think(req, text, "porque", self._why_info(), [])
            if RE_RELEASE.search(t) and not re.match(r"(como|onde|tem como|da pra|da para)\b", t):
                return self._think(req, text, "lancamentos", "", [], prepare=self._release_info)
            if RE_SUMMARY.search(t):
                info, actions, found = self._summary_info(text, t)
                if found:
                    return self._think(req, text, "resumo", info, actions)
            sport = RE_SPORT.search(t) or action == "game"
            if RE_HELP.search(t) and not (sport and not re.match(r"(como|onde|tem como|da pra|da para)\b", t)):
                return self._think(req, text, "ajuda", self._help_info(), [])
            if sport:
                info, actions = self._sport_info(t, arg if action == "game" else "")
                return self._think(req, text, "futebol", info, actions)
            if action in COMMANDS and len(t.split()) <= 8:
                self.win._on_voice(text, from_chat=True)
                return self._done(req, text, DONE.get(action, "Feito!"), [])
            if RE_TIME.search(t) and parse_minutes(t):
                info, actions = self._time_info(text, t, parse_minutes(t))
                return self._think(req, text, "tempo", info, actions)
            if RE_PICK.search(t) or RE_COUPLE.search(t) or (action == "search" and len(t.split()) >= 4 and not re.match(
                    r"(oi|ola|e ai|eai|bom dia|boa tarde|boa noite|tudo bem|valeu|obrigad)", t)):
                info, actions = self._pick_info(text, t)
                return self._think(req, text, "indicacao", info, actions)
        except Exception:  # noqa: BLE001
            log.exception("Pudinho: preparando a resposta")
        return self._think(req, text, "conversa", "", [])

    def _done(self, req, question, answer, actions):
        self._remember(question, answer)
        self._reply.emit(req, answer, actions)

    def _remember(self, question, answer):
        self.history += [("user", question), ("model", answer)]
        del self.history[:-HISTORY_TURNS * 2]

    # ---- informações para a IA
    def _help_info(self):
        return MANUAL

    def _sport_info(self, t, team):
        win = self.win
        games = win.sport_games() if hasattr(win, "sport_games") else []
        lines, actions = [], []
        words = [w for w in t.split() if len(w) > 3]

        def mentions(g):
            names = [team] if team else words
            return any(same_team(g.home, n) or same_team(g.away, n) for n in names)
        picked = [(g, c) for g, c in games if mentions(g)]
        asked = team or " ".join(w for w in words if w not in SPORT_WORDS)
        if picked or asked:
            games = picked   # perguntou de um time: só os jogos dele (nenhum = não joga hoje)
        for g, chs in games:
            score = win.live_score(g)
            if score:
                when = f"AO VIVO agora, {score[0]} a {score[1]} ({score[2]})"
            elif g.start <= time.time():
                when = "começou, sem placar ao vivo"
            else:
                when = "às " + time.strftime("%H:%M", time.localtime(g.start))
            if time.localtime(g.start).tm_yday != time.localtime().tm_yday and not score:
                when = time.strftime("dia %d/%m às %H:%M", time.localtime(g.start))
            names = ", ".join(c.name for c in chs[:3]) or "num canal que não está nas suas listas"
            lines.append(f"- {g.home} x {g.away} ({g.competition or 'jogo'}): {when}. Passa em: {names}.")
            if chs:
                actions.append((f"▶ Assistir {g.home} x {g.away}", lambda g=g, c=chs: win.watch_game(g, c)))
            if len(lines) >= 8:
                break
        for (home, away), score in getattr(getattr(win, "goals", None), "live", {}).items():
            if any(same_team(home, w) or same_team(away, w) for w in words) and not any(
                    home in line for line in lines):
                lines.append(f"- {home} x {away}: AO VIVO, {score[0]} a {score[1]} ({score[2]}) (placar da ESPN).")
        if not lines:
            who = f" de {asked}" if asked else ""
            return (f"Não há nenhum jogo{who} nos seus canais nos próximos dias (a agenda vem do "
                    "guia de programação dos seus canais). Diga isso; não sugira outros jogos nem canais."), actions
        return "Jogos nos seus canais:\n" + "\n".join(lines), actions[:4]

    def _pick_info(self, text, t, more=False):
        home = getattr(self.win, "home", None)
        if home is None or home.catalog is None:
            return "Você ainda não tem uma lista com filmes e séries no Pudim.", []
        if not more:
            self._last, self._shown = ("indicacao", text), set()
        entries, base, couple = [], None, None
        mc = RE_COUPLE.search(t)
        if mc:   # modo casal: "eu gosto de suspense e ela de comédia"
            couple = tuple(RE_COUPLE_TAIL.sub("", x).strip() for x in mc.groups())
            if all(couple):
                entries = self._couple_entries(*couple)
        m = None if entries else RE_SIMILAR.search(t)
        if m:
            name = m.group(1).strip()
            found = home.catalog.search(name)
            base = (found["titles"] or found["movies"] or [None])[0]
            if base is not None:
                plot = (home.info_of(base, fetch=False) or {}).get("plot", "")
                entries = home.similar_entries(plot, exclude=base)
        if not entries:
            query = re.sub(r"\s+", " ", FILLER.sub(" ", t)).strip() or t
            entries = home.described_entries(query) if len(query.split()) >= 3 else []
            entries = entries or home.topic_entries(query) or home.described_entries(t)
            if not entries:
                found = home.catalog.search(query)
                entries = found["titles"] + found["movies"]
        watched = {u for u, v in self.win.cfg["resume"].items() if v and v[0] == -1}
        picked, keys = [], set()
        for e in entries:   # o mesmo filme em 4K/HD/legendado conta uma vez só
            key = _title_key(e)
            if key in keys or id(e) in self._shown or (not isinstance(e, CatalogNode) and e.url in watched):
                continue
            keys.add(key)
            picked.append(e)
            if len(picked) >= MAX_OPTIONS:
                break
        entries = picked
        if not entries:
            if more:
                return "Já mostrei tudo o que achei no seu catálogo para esse pedido. Sugira pedir de outro jeito.", []
            return "Não achei nada parecido com esse pedido no seu catálogo.", []
        self._shown.update(id(e) for e in entries)
        self._last_entries = list(entries)
        lines, actions = [], []
        for i, e in enumerate(entries, 1):
            info = home.info_of(e, fetch=False) or {}
            title = display_title(e.label if isinstance(e, CatalogNode) else e.name)
            kind = "série" if isinstance(e, CatalogNode) else "filme"
            extra = ", ".join(x for x in (info.get("year", ""), info.get("genre", ""),
                                          f"nota {info['rating']:.1f}" if info.get("rating") else "") if x)
            plot = " ".join((info.get("plot") or "").split())[:220]
            lines.append(f"{i}. {title} ({kind}{', ' + extra if extra else ''}): {plot or 'sem sinopse'}")
            icon = "📺" if kind == "série" else "🎬"
            actions.append((f"{icon} {title}", lambda e=e: self.win.compare.add(e)))
        head = (f"Parecidos com {display_title(base.label if isinstance(base, CatalogNode) else base.name)}"
                if base is not None else "Opções do seu catálogo para esse pedido")
        if couple and all(couple):
            head = (f"Modo casal: você gosta de {couple[0]} e a outra pessoa gosta de {couple[1]}. Opções do seu "
                    "catálogo que juntam os dois gostos (explique por que agrada os dois)")
        return head + " (indique só destes):\n" + "\n".join(lines), actions

    # ---- "não sei o que ver": questionário rápido com botões
    def _start_quiz(self, req, text):
        self._quiz = {"step": "genero"}
        chips = [(lbl, lambda l=lbl: self.win.chat.ask(l)) for lbl, _g in QUIZ_GENRES]
        self._done(req, text, "Bora achar algo! 🍿 Que pegada você quer hoje?", chips)

    def _quiz_answer(self, req, text, t):
        """Responde um passo do questionário. Retorna True se consumiu o que a pessoa disse."""
        step = self._quiz.get("step")
        if step == "genero":
            g = self._match_genre(t)
            if g is None:
                self._quiz = None
                return False
            self._quiz["genero"] = random.choice(QUIZ_ANY) if g == "__any__" else g
            self._quiz["step"] = "tempo"
            chips = [(lbl, lambda l=lbl: self.win.chat.ask(l)) for lbl, _v in QUIZ_TIMES]
            self._done(req, text, "Boa! E quanto tempo você tem?", chips)
            return True
        if step == "tempo":
            tempo = self._match_tempo(t)
            g = self._quiz.get("genero", "filme")
            self._quiz = None
            if tempo == "rapido":
                q = f"tenho 1h40 pra um {g}"
                info, actions = self._time_info(q, fold(q), 100)
                return self._finish_quiz(req, q, "tempo", info, actions)
            q = f"me indica um {g} bom"
            info, actions = self._pick_info(q, fold(q))
            return self._finish_quiz(req, q, "indicacao", info, actions)
        self._quiz = None
        return False

    def _finish_quiz(self, req, question, kind, info, actions):
        self._think(req, question, kind, info, actions)
        return True

    def _match_genre(self, t):
        for _lbl, g in QUIZ_GENRES:
            if g != "__any__" and fold(g) in t:
                return g
        if any(w in t for w in ("surpresa", "tanto faz", "qualquer", "voce escolhe", "aleatori")):
            return "__any__"
        return None

    def _match_tempo(self, t):
        if "rapid" in t or "curto" in t:
            return "rapido"
        if "film" in t or "longo" in t or "2h" in t or "2 h" in t:
            return "longo"
        return "qualquer"

    def _why_info(self):
        """Fonte para a IA explicar por que indicou os títulos da última indicação."""
        home = self.win.home
        rec = getattr(self.win, "recommender", None)
        gostos = ", ".join(rec.top_genres(4)) if rec is not None else ""
        lines = []
        for e in self._last_entries[:MAX_OPTIONS]:
            info = home.info_of(e, fetch=False) or {}
            title = display_title(e.label if isinstance(e, CatalogNode) else e.name)
            lines.append(f"- {title}: gênero {info.get('genre') or 'não informado'}")
        head = (f"Gostos da pessoa (do que ela costuma assistir): {gostos}\n" if gostos else "")
        return head + "Títulos que você indicou:\n" + "\n".join(lines)

    def _summary_info(self, text, t):
        """Resumo sem spoiler de um título do catálogo. Retorna (fonte p/ IA, botões, achou?)."""
        home = getattr(self.win, "home", None)
        if home is None or home.catalog is None:
            return "", [], False
        quoted = RE_QUOTED.search(text)
        if quoted:
            name = quoted.group(1).strip()
        else:   # tira as palavras do pedido pra sobrar o nome
            name = RE_SUMMARY.sub(" ", t)
            name = re.sub(r"\b(sem spoiler|spoilers?|do filme|da serie|da série|o filme|a serie|a série|pra mim|"
                          r"por favor|pfv|pf|ai|me|de|do|da)\b", " ", name)
            name = re.sub(r"\s+", " ", name).strip()
        e = self._in_catalog(name)
        if e is None and len(name) >= 3:
            found = home.catalog.search(name)
            e = (found["titles"] or found["movies"] or [None])[0]
        if e is None:
            return "", [], False
        info = home.info_of(e, fetch=False) or {}
        title = display_title(e.label if isinstance(e, CatalogNode) else e.name)
        kind = "série" if isinstance(e, CatalogNode) else "filme"
        action = [(f"{'📺' if kind == 'série' else '🎬'} {title}", lambda e=e: self.win.compare.add(e))]
        plot = " ".join((info.get("plot") or "").split())
        if not plot:
            return (f"Você quer um resumo sem spoiler de “{title}”, mas eu ainda não tenho a sinopse dele. "
                    "Diga isso de forma simpática e ofereça abrir a página do título.", action, True)
        extra = ", ".join(x for x in (info.get("year", ""), info.get("genre", "")) if x)
        src = f"TÍTULO: {title} ({kind}{', ' + extra if extra else ''}).\nSINOPSE-FONTE: {plot[:700]}"
        return src, action, True

    def _time_info(self, text, t, minutes, more=False):
        """"Tenho 2 horas": filmes do catálogo cuja duração cabe no tempo que a pessoa tem."""
        home = getattr(self.win, "home", None)
        if home is None or home.catalog is None:
            return "Você ainda não tem uma lista com filmes e séries no Pudim.", []
        if not more:
            self._last, self._shown = ("tempo", text), set()
        query = re.sub(r"\s+", " ", FILLER.sub(" ", RE_TIME.sub(" ", t))).strip()   # gênero/assunto junto?
        pool = home.topic_entries(query) or home.described_entries(query) if len(query.split()) >= 2 else []
        entries = self._fit_by_time(home, pool, minutes) or self._fit_by_time(home, None, minutes)
        picked, keys = [], set()
        for e in entries:
            key = _title_key(e)
            if key in keys or id(e) in self._shown:
                continue
            keys.add(key)
            picked.append(e)
            if len(picked) >= MAX_OPTIONS:
                break
        if not picked:
            if more:
                return "Já mostrei o que achei que cabe nesse tempo. Sugira pedir de outro jeito.", []
            return (f"Não achei filmes com duração conhecida que caibam em {minutes} minutos no seu catálogo. "
                    "Diga isso de um jeito simpático e ofereça indicar de outro jeito."), []
        self._shown.update(id(e) for e in picked)
        self._last_entries = list(picked)
        lines, actions = [], []
        for i, e in enumerate(picked, 1):
            info = home.info_of(e, fetch=False) or {}
            title = display_title(e.name)
            dmin = dur_minutes(info.get("duration", ""))
            extra = ", ".join(x for x in (info.get("year", ""), info.get("genre", ""),
                                          f"nota {info['rating']:.1f}" if info.get("rating") else "",
                                          f"{dmin} min" if dmin else "") if x)
            plot = " ".join((info.get("plot") or "").split())[:200]
            lines.append(f"{i}. {title} (filme{', ' + extra if extra else ''}): {plot or 'sem sinopse'}")
            actions.append((f"🎬 {title}", lambda e=e: self.win.compare.add(e)))
        head = (f"Você tem cerca de {minutes} minutos. Filmes do catálogo que cabem nesse tempo "
                "(indique só destes, dizendo a duração de cada um)")
        return head + ":\n" + "\n".join(lines), actions

    def _fit_by_time(self, home, pool, minutes):
        """Filmes com duração entre um piso e o tempo disponível; melhores notas primeiro."""
        upper, floor = minutes + 8, max(40, minutes - 90)

        def rating_of(info):
            try:
                return float(info.get("rating") or 0)
            except (TypeError, ValueError):
                return 0.0
        out = []
        if pool:
            for e in pool:
                if isinstance(e, CatalogNode):   # séries não entram no filtro por duração de filme
                    continue
                info = home.info_of(e, fetch=False) or {}
                d = dur_minutes(info.get("duration", ""))
                if d and floor <= d <= upper:
                    out.append((rating_of(info), d, e))
        else:
            home._title_entries((), ())          # garante o mapa id do filme -> item do catálogo
            by_sid = home._by_sid or {}
            for sid, info in list(self.win.meta.movies.items()):
                d = dur_minutes(info.get("duration", ""))
                if d and floor <= d <= upper and sid in by_sid:
                    out.append((rating_of(info), d, by_sid[sid]))
        out.sort(key=lambda x: (-x[0], abs(minutes - x[1])))
        return [e for _, _, e in out]

    def _couple_entries(self, a, b):
        """Títulos que agradam os dois gostos: primeiro os que aparecem nas duas buscas, depois os que a busca
        pelo sentido acha misturando os dois, depois um de cada, alternando."""
        home = self.win.home

        def find(q):
            found = home.described_entries(q) if len(q.split()) >= 3 else []
            return found or home.topic_entries(q) or home.described_entries(f"filme de {q} muito bom")

        first, second = find(a), find(b)
        in_second = {id(e) for e in second}
        pool = [e for e in first if id(e) in in_second]
        pool += home.described_entries(f"{a} misturado com {b}")
        for x, y in zip(first, second):
            pool += [x, y]
        out, seen = [], set()
        for e in pool:
            if id(e) not in seen:
                seen.add(id(e))
                out.append(e)
        return out

    def _entry_lines(self, entries):
        home = self.win.home
        lines, actions = [], []
        for e in entries:
            info = home.info_of(e, fetch=False) or {}
            title = display_title(e.label if isinstance(e, CatalogNode) else e.name)
            kind = "série" if isinstance(e, CatalogNode) else "filme"
            extra = ", ".join(x for x in (info.get("year", ""), info.get("genre", "")) if x)
            plot = " ".join((info.get("plot") or "").split())[:200]
            lines.append(f"- {title} ({kind}{', ' + extra if extra else ''}): {plot or 'sem sinopse'}")
            actions.append((f"{'📺' if kind == 'série' else '🎬'} {title}", lambda e=e: self.win.compare.add(e)))
        return lines, actions

    def _in_catalog(self, title):
        """O filme/série com exatamente esse nome no catálogo (sem ligar para ano e qualidade), ou None."""
        catalog = self.win.home.catalog
        want = fold(title)
        if len(want) < 3:
            return None
        found = catalog.search(title, limit=40)
        for e in found["titles"] + found["movies"]:
            name = fold(display_title(e.label if isinstance(e, CatalogNode) else e.name))
            name = re.sub(r"\s*\b(19|20)\d\d\b.*$", "", name).strip()
            if name == want:
                return e
        return None

    def _release_info(self, more=False):
        """(texto para a IA, botões): o que está estreando lá fora e já tem no catálogo. Roda fora da tela."""
        home = getattr(self.win, "home", None)
        if home is None or home.catalog is None:
            return "Você ainda não tem uma lista com filmes e séries no Pudim.", []
        if not more:
            self._last, self._shown = ("lancamentos", "Quais lançamentos já tem no meu catálogo?"), set()
        titles = release_titles()[:60]
        have, missing = [], []
        for rank, title in enumerate(titles):
            e = self._in_catalog(title)
            if e is None:
                if rank < 15 and title[:1].isupper():   # só os mais citados: frases entre aspas não entram
                    missing.append(title)
                continue
            if id(e) not in self._shown and e not in have:
                have.append(e)
            if len(have) >= MAX_OPTIONS:
                break
        parts, actions = [], []
        if have:
            lines, actions = self._entry_lines(have)
            parts.append("Lançamentos e estreias em alta na internet (últimas 2 semanas) que JÁ estão no seu "
                         "catálogo:\n" + "\n".join(lines))
        if not titles:
            parts.append("Não consegui ver os lançamentos na internet agora (sem conexão?).")
        elif not have:
            parts.append("Nenhum outro lançamento em alta na internet está no seu catálogo." if more else
                         "Nenhum dos lançamentos em alta na internet chegou ao seu catálogo ainda.")
        if missing and not more:
            parts.append("Em alta na internet mas ainda não no catálogo (só cite o nome, sem detalhes): "
                         + ", ".join(missing[:5]))
        if len(have) < 3:   # completa com os mais novos que a lista da conta já tem
            year = time.localtime().tm_year
            node = home.catalog.node((MOVIES, "Lançamentos"))
            fresh = []
            if node:
                for i in node.indices[:800]:
                    ch = home.catalog.channels[i]
                    m = re.search(r"\b(19|20)\d\d\b", ch.name)
                    if m and int(m.group(0)) >= year - 1 and id(ch) not in self._shown and ch not in have:
                        fresh.append(ch)
                    if len(fresh) >= 4:
                        break
            if fresh:
                lines, extra = self._entry_lines(fresh)
                parts.append("Também chegaram há pouco no seu catálogo:\n" + "\n".join(lines))
                actions += extra
                have += fresh
        self._shown.update(id(e) for e in have)
        return "\n\n".join(parts), actions

    def _open(self, e):
        self.win.go_home()
        self.win.home.activate(e, None)

    # ---- resposta da IA
    def _think(self, req, question, kind, info, actions, prepare=None):
        threading.Thread(target=self._work, args=(req, question, kind, info, actions, prepare), name="pudinho",
                         daemon=True).start()

    def _work(self, req, question, kind, info, actions, prepare=None):
        if prepare is not None:   # informações que dependem da internet: buscadas aqui, fora da tela
            try:
                info, actions = prepare()
            except Exception:  # noqa: BLE001
                log.exception("Pudinho: preparando %s", kind)
                info, actions = "Não consegui buscar essas informações agora.", []
        task = {
            "lancamentos": "Conte quais lançamentos em alta na internet já estão no catálogo e indique 2 a 4 "
                           "deles (ou dos que chegaram há pouco), escrevendo o nome exatamente como na lista e "
                           "dizendo o porquê em poucas palavras. Só se as informações abaixo tiverem a parte \"ainda "
                           "não no catálogo\", cite em uma frase até 3 desses nomes; senão não fale de títulos que "
                           "faltam. Não escreva botões nem colchetes: o app mostra os botões sozinho.",
            "indicacao": "Indique de 3 a 5 títulos da lista abaixo que combinem com o pedido, escrevendo o nome "
                         "exatamente como na lista. Para CADA um, escreva 1 ou 2 frases animadas dizendo por que "
                         "combina — o clima, o gênero e o que o torna uma boa pedida — de um jeito que dê vontade de "
                         "assistir, SEM spoilers. Comece com uma frase curta e simpática. NÃO termine se desculpando "
                         "nem diga que não tem mais opções: termine convidando a pessoa a pedir mais sugestões ou a "
                         "contar um pouco mais do que ela curte. Não escreva botões nem colchetes: o app mostra os "
                         "botões sozinho.",
            "tempo": "A pessoa disse quanto tempo tem. Indique 2 a 4 títulos da lista abaixo que caibam nesse "
                     "tempo, escrevendo o nome exatamente como na lista e dizendo a duração de cada um e o porquê "
                     "em poucas palavras. Não escreva botões nem colchetes: o app mostra os botões sozinho.",
            "porque": "A pessoa quer saber por que você indicou aqueles títulos. Explique em no máximo 1 frase "
                      "por título, usando só o gênero de cada um e os gostos informados abaixo. Não invente. Se não "
                      "houver gostos informados, diga que indicou por serem bem avaliados e do estilo que ela pediu.",
            "resumo": "A pessoa quer um resumo SEM SPOILERS. Com base só na sinopse-fonte abaixo, escreva um "
                      "resumo curto (2 a 3 frases) que dê vontade de assistir: apresente a premissa, o gênero e o "
                      "clima, SEM revelar reviravoltas, segredos, mortes nem o final. Se a sinopse-fonte já tiver "
                      "spoilers, não os repita. Não invente nada que não esteja na sinopse-fonte.",
            "futebol": "Responda sobre os jogos usando só as informações abaixo. Se tiver botão de assistir, "
                       "lembre que dá para clicar nele.",
            "ajuda": "Explique como fazer usando só o manual abaixo. Cite o atalho quando existir.",
            "conversa": "Converse, sem citar nenhum filme, série, canal ou jogo pelo nome (você não recebeu o "
                        "catálogo agora). Se fizer sentido, lembre que você indica filmes e séries do catálogo, "
                        "acha títulos pela descrição, explica o app, conta dos jogos do dia e obedece comandos.",
        }[kind]
        prompt = f"{task}\n\n{info}\n\nMensagem da pessoa: {question}" if info else f"{task}\n\nMensagem da pessoa: {question}"
        answer = ""
        llm = self._get_llm()
        if llm is not None:
            try:
                with self._llm_lock:
                    answer = clean_reply(llm.chat(PERSONA, self.history + [("user", prompt)]))
            except Exception:  # noqa: BLE001
                log.exception("Pudinho: IA")
        if not answer:
            answer = self._fallback(kind, info, actions)
        elif kind in ("indicacao", "lancamentos", "tempo"):
            actions = _mentioned_first(answer, actions)
        if kind in ("indicacao", "lancamentos", "tempo") and actions:
            actions = list(actions) + [("🔄 Indicar outras opções", lambda: self.win.chat.ask(MORE_TEXT))]
        self.history += [("user", question), ("model", answer)]
        del self.history[:-HISTORY_TURNS * 2]
        self._reply.emit(req, answer, actions)

    def _get_llm(self):
        if self._llm is not None:
            return self._llm
        try:
            from .captions import LLMTranslator, _enable_cuda_dirs, cuda_ready, gpu_available, llm_ready
            caps = getattr(self.win, "captions", None)
            shared = getattr(caps, "_llm", None) if caps else None
            shared = shared or getattr(self.win, "_summary_llm", None)
            if shared is not None:
                return shared
            if llm_ready() and gpu_available() and cuda_ready():
                _enable_cuda_dirs()   # as bibliotecas da placa de vídeo (a legenda também liga isso)
                self._llm = self.win._summary_llm = LLMTranslator()
        except Exception:  # noqa: BLE001
            log.exception("Pudinho: carregando a IA")
        return self._llm

    @staticmethod
    def _fallback(kind, info, actions):
        if kind in ("indicacao", "lancamentos", "tempo"):
            return "Separei estes do seu catálogo, é só clicar:" if actions else info
        if kind == "porque":
            return "Indiquei esses pensando no seu gosto e porque são bem avaliados. 🍮"
        if kind == "resumo":   # sem IA: as 2 primeiras frases da sinopse (costuma ser só a premissa)
            plot = info.split("SINOPSE-FONTE:", 1)[-1].strip()
            frases = re.split(r"(?<=[.!?])\s+", plot)
            return " ".join(frases[:2])[:300] or info
        if kind == "futebol":
            return info.replace("Jogos nos seus canais:\n", "").replace("- ", "")
        if kind == "ajuda":
            return "Dá uma olhada no manual: " + MANUAL.split("\n", 1)[1][:600]
        return (f"Oi! Eu sou o {NAME}. Minha IA não está disponível neste PC (ela precisa da placa de vídeo), mas "
                "ainda indico filmes e séries do seu catálogo e conto dos jogos do dia.")


class ChatPanel(QFrame):
    """Painel lateral do chat."""
    SUGGESTIONS = ("Me indica um filme de suspense", "O Corinthians joga hoje?", "Eu gosto de suspense e ela de comédia",
                   "Quais lançamentos já tem no meu catálogo?")

    def __init__(self, win):
        super().__init__(win, objectName="chatPanel")
        self.win = win
        self.brain = Brain(win)
        self.brain.answered.connect(self._on_answer)
        self._waiting = None
        self.setMinimumWidth(320)
        self.setMaximumWidth(460)
        self._style()

        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        head = QFrame(objectName="chatHead")
        h = QHBoxLayout(head)
        h.setContentsMargins(14, 10, 8, 10)
        avatar = QLabel()
        avatar.setPixmap(app_icon_pixmap(128))
        avatar.setFixedSize(42, 42)
        avatar.setScaledContents(True)
        h.addWidget(avatar)
        names = QVBoxLayout()
        names.setSpacing(0)
        title = QLabel(NAME, objectName="chatTitle")
        sub = QLabel("seu assistente no Pudim", objectName="chatSub")
        names.addWidget(title)
        names.addWidget(sub)
        h.addLayout(names, 1)
        h.addWidget(make_btn("close", "Fechar (Ctrl+J)", self.win.toggle_chat, kind="tool", icon_size=16))
        v.addWidget(head)

        self.scroll = QScrollArea(objectName="chatScroll")
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body = QWidget(objectName="chatBody")
        self.msgs = QVBoxLayout(body)
        self.msgs.setContentsMargins(12, 12, 12, 12)
        self.msgs.setSpacing(8)
        self.msgs.addStretch(1)
        self.scroll.setWidget(body)
        v.addWidget(self.scroll, 1)

        bottom = QFrame(objectName="chatInputRow")
        b = QHBoxLayout(bottom)
        b.setContentsMargins(10, 8, 10, 10)
        self.input = QLineEdit(placeholderText=f"Pergunte ao {NAME}…", objectName="chatInput")
        self.input.returnPressed.connect(self.send)
        b.addWidget(self.input, 1)
        b.addWidget(make_btn("mic", "Falar (Ctrl+Espaço)", self.win.voice_command, kind="tool", icon_size=17))
        send = QPushButton("Enviar", objectName="chatSend")
        send.setCursor(Qt.CursorShape.PointingHandCursor)
        send.clicked.connect(self.send)
        b.addWidget(send)
        v.addWidget(bottom)

        self._say(f"Oi! Eu sou o {NAME} 🍮 Posso te indicar um filme ou série do seu catálogo, achar aquele título "
                  "que você só lembra da história, contar dos jogos de hoje, explicar o app ou fazer coisas como "
                  "\"abre o SporTV\". Manda aí!", mine=False)
        self._actions([(s, lambda s=s: self.ask(s)) for s in self.SUGGESTIONS], chips=True)

    def _style(self):
        self.setStyleSheet(f"""
            #chatPanel {{ background: {T['panel']}; border-left: 1px solid {T['border']}; }}
            #chatHead {{ background: {T['surface']}; border-bottom: 1px solid {T['border']}; }}
            #chatTitle {{ font-size: 15px; font-weight: 700; color: {T['strong']}; }}
            #chatSub {{ color: {T['muted']}; font-size: 11px; }}
            #chatScroll, #chatBody {{ background: {T['panel']}; border: none; }}
            QLabel#bubbleMe {{ background: {T['accent']}; color: white; border-radius: 12px; padding: 8px 11px; }}
            QLabel#bubbleBot {{ background: {T['surface2']}; color: {T['text']}; border-radius: 12px;
                                padding: 8px 11px; }}
            QLabel#typing {{ color: {T['muted']}; font-style: italic; padding: 2px 4px; }}
            QPushButton#chatAction {{ text-align: left; background: {T['surface']}; color: {T['text']};
                                      border: 1px solid {T['border']}; border-radius: 9px; padding: 6px 10px; }}
            QPushButton#chatAction:hover {{ border-color: {T['accent']}; }}
            QPushButton#chatChip {{ background: transparent; color: {T['accent']}; border: 1px solid {T['accent']};
                                    border-radius: 13px; padding: 4px 10px; }}
            QPushButton#chatChip:hover {{ background: {T['surface']}; }}
            #chatInputRow {{ background: {T['surface']}; border-top: 1px solid {T['border']}; }}
            QLineEdit#chatInput {{ background: {T['panel']}; border: 1px solid {T['border']}; border-radius: 16px;
                                   padding: 7px 12px; color: {T['text']}; }}
            QLineEdit#chatInput:focus {{ border-color: {T['accent']}; }}
            QPushButton#chatSend {{ background: {T['accent']}; color: white; border: none; border-radius: 16px;
                                    padding: 7px 14px; font-weight: 600; }}
        """)

    # ---- mensagens
    def _add(self, widget, align):
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        if align == "right":
            row.addStretch(1)
        row.addWidget(widget)
        if align == "left":
            row.addStretch(1)
        holder = QWidget()
        holder.setLayout(row)
        self.msgs.insertWidget(self.msgs.count() - 1, holder)
        QTimer.singleShot(30, lambda: self.scroll.verticalScrollBar().setValue(
            self.scroll.verticalScrollBar().maximum()))
        return holder

    def _say(self, text, mine):
        lbl = QLabel(html.escape(text, quote=False).replace("\n", "<br>"), objectName="bubbleMe" if mine else "bubbleBot")
        lbl.setWordWrap(True)
        lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        lbl.setMaximumWidth(330)
        lbl.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        return self._add(lbl, "right" if mine else "left")

    def _actions(self, actions, chips=False):
        if not actions:
            return
        box = QWidget()
        lay = QVBoxLayout(box)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(5)
        for label, fn in actions:
            btn = QPushButton(label, objectName="chatChip" if chips else "chatAction")
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _checked=False, f=fn: f())   # o clicked manda "checked": não passa adiante
            lay.addWidget(btn)
        self._add(box, "left")

    def ask(self, text):
        text = (text or "").strip()
        if not text:
            return
        self._say(text, mine=True)
        if self._waiting is not None:
            self._waiting.deleteLater()
        typing = QLabel(f"{NAME} está pensando…", objectName="typing")
        self._waiting = self._add(typing, "left")
        self.brain.ask(text)

    def send(self):
        text = self.input.text()
        self.input.clear()
        self.ask(text)

    def _on_answer(self, req, text, actions):
        if req != self.brain._req:
            return   # chegou a resposta de uma pergunta antiga
        if self._waiting is not None:
            self._waiting.deleteLater()
            self._waiting = None
        self._say(text, mine=False)
        self._actions(actions)

    def focus_input(self):
        self.input.setFocus()
