"""Pudinho: o assistente do Pudim, um chat como o dos sites.

Indica filmes e séries do catálogo da pessoa (pela busca por sentido das sinopses), acha títulos pela descrição,
explica como usar o app, responde sobre os jogos do dia e executa comandos ("abre o SporTV", "liga a legenda").
O texto das respostas sai do Gemma 3 (a mesma IA da legenda), na placa de vídeo, sem internet. Sem a IA,
responde com modelos prontos (as indicações e os jogos continuam funcionando)."""
import html
import re
import threading
import time
import unicodedata

from PyQt6.QtCore import QObject, Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton, QScrollArea, QSizePolicy,
                             QVBoxLayout, QWidget)

from .catalog import CatalogNode, display_title
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
RE_SIMILAR = re.compile(r"\b(?:parecid[oa]s?|tipo|igual|estilo|semelhantes?|no estilo|na linha)"
                        r"(?: (?:com|a|ao|de|do|da|que))? (.+)$")
FILLER = re.compile(r"\b(me|pra mim|por favor|pfv|pf|voce|vc|ai|indica|indique|recomenda|recomende|sugere|sugira|"
                    r"alguma sugestao|sugestao|quero|queria|ver|assistir|um|uma|uns|umas|algum|alguma|algo|bom|boa|"
                    r"legal|pra|para|hoje|agora)\b")
COMMANDS = {"seek", "pause", "stop", "mute", "volume", "captions", "dub", "tv", "home", "missed", "channel"}
DONE = {"seek": "Pronto, mexi no tempo do vídeo.", "pause": "Feito: pausei (ou continuei) pra você.",
        "stop": "Desliguei o vídeo.", "mute": "Mexi no som.", "volume": "Ajustei o volume.",
        "captions": "Prontinho, mexi na legenda.", "dub": "Prontinho, mexi na dublagem.",
        "tv": "Mandando pra TV!", "home": "Voltei pro Início.", "missed": "Vou ver o que você perdeu…",
        "channel": "Abrindo aí!"}


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
        self._reply.connect(self.answered)

    # ---- pedido (chega na tarefa principal; a IA roda em outra)
    def ask(self, text):
        self._req += 1
        req = self._req
        t = fold(text)
        action, arg = parse_command(text)
        try:
            sport = RE_SPORT.search(t) or action == "game"
            if RE_HELP.search(t) and not (sport and not re.match(r"(como|onde|tem como|da pra|da para)\b", t)):
                return self._think(req, text, "ajuda", self._help_info(), [])
            if sport:
                info, actions = self._sport_info(t, arg if action == "game" else "")
                return self._think(req, text, "futebol", info, actions)
            if action in COMMANDS and len(t.split()) <= 8:
                self.win._on_voice(text, from_chat=True)
                return self._done(req, text, DONE.get(action, "Feito!"), [])
            if RE_PICK.search(t) or (action == "search" and len(t.split()) >= 4 and not re.match(
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

    def _pick_info(self, text, t):
        home = getattr(self.win, "home", None)
        if home is None or home.catalog is None:
            return "A pessoa ainda não tem uma lista com filmes e séries no Pudim.", []
        entries, base = [], None
        m = RE_SIMILAR.search(t)
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
        entries = [e for e in entries if isinstance(e, CatalogNode) or e.url not in watched][:MAX_OPTIONS]
        if not entries:
            return "Não achei nada parecido com esse pedido no seu catálogo.", []
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
            actions.append((f"{icon} {title}", lambda e=e: self._open(e)))
        head = (f"Parecidos com {display_title(base.label if isinstance(base, CatalogNode) else base.name)}"
                if base is not None else "Opções do seu catálogo para esse pedido")
        return head + " (indique só destes):\n" + "\n".join(lines), actions

    def _open(self, e):
        self.win.go_home()
        self.win.home.activate(e, None)

    # ---- resposta da IA
    def _think(self, req, question, kind, info, actions):
        threading.Thread(target=self._work, args=(req, question, kind, info, actions), name="pudinho",
                         daemon=True).start()

    def _work(self, req, question, kind, info, actions):
        task = {
            "indicacao": "Indique 2 a 4 títulos da lista abaixo que combinem com o pedido, escrevendo o nome "
                         "exatamente como na lista e dizendo o porquê em poucas palavras. Não escreva botões nem "
                         "colchetes: o app mostra os botões sozinho.",
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
        elif kind == "indicacao":
            actions = _mentioned_first(answer, actions)
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
        if kind == "indicacao":
            return "Separei estes do seu catálogo, é só clicar:" if actions else info
        if kind == "futebol":
            return info.replace("Jogos nos seus canais:\n", "").replace("- ", "")
        if kind == "ajuda":
            return "Dá uma olhada no manual: " + MANUAL.split("\n", 1)[1][:600]
        return (f"Oi! Eu sou o {NAME}. Minha IA não está disponível neste PC (ela precisa da placa de vídeo), mas "
                "ainda indico filmes e séries do seu catálogo e conto dos jogos do dia.")


class ChatPanel(QFrame):
    """Painel lateral do chat."""
    SUGGESTIONS = ("Me indica um filme de suspense", "O Corinthians joga hoje?", "Como mando pra TV?",
                   "Algo parecido com Breaking Bad")

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
