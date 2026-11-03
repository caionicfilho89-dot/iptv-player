"""Recap com IA: antes de continuar uma série, o Pudinho resume "o que rolou até aqui".

Junta as sinopses dos episódios anteriores (da API da conta, metadata.episodes_with_plot) e pede ao Gemma 3
(a mesma IA da legenda e do Pudinho, na placa de vídeo, sem internet) um resumo curto, sem spoiler do episódio
que a pessoa vai ver. Sem a IA, cai nas próprias sinopses. Nunca atrapalha a reprodução: qualquer erro ou falta
de dados simplesmente toca o episódio direto."""
import threading
import time

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (QCheckBox, QDialog, QHBoxLayout, QLabel, QPushButton, QScrollArea, QVBoxLayout,
                             QWidget)

from .catalog import EPISODE_RE, catalog_fold, series_parts
from .log import log

GAP = 18 * 3600          # só relembra se faz mais de ~18h desde o último episódio visto (não atrapalha maratona)
MAX_EPISODES = 8         # sinopses anteriores que vão para a IA (as mais recentes antes do atual)
MAX_PLOT = 320           # corta cada sinopse para o prompt não ficar gigante
_LLM_LOCK = threading.Lock()

SYSTEM = ("Você é o Pudinho, o assistente do Pudim. Faz recaps curtos de séries em português do Brasil, "
          "num tom animado e direto. Nunca inventa fatos e nunca dá spoiler do episódio que a pessoa ainda vai ver.")


def _order(name):
    m = EPISODE_RE.search(name or "")
    return (int(m.group(1)), int(m.group(2))) if m else None


def _last_watch_before(win, key, cur):
    """Quando (timestamp) a pessoa viu pela última vez um episódio ANTERIOR desta série, ou None."""
    cat = getattr(win, "catalog", None)
    resume = win.cfg["resume"]
    best = None
    for url, pos in resume.items():
        i = cat.by_url.get(url) if cat else None
        if i is None or not (pos and len(pos) >= 3):
            continue
        ch = cat.channels[i]
        o = _order(ch.name)
        if o and o < cur and catalog_fold(series_parts(ch.name)[0]).strip() == key:
            when = pos[2]
            if best is None or when > best:
                best = when
    return best


def should_offer(win, ch):
    """Decisão barata (sem rede): vale a pena oferecer um recap antes de tocar este episódio?"""
    try:
        if not win.cfg.get("recap", True) or getattr(win, "meta", None) is None:
            return False
        cur = _order(ch.name)
        if not cur or cur == (1, 1):   # primeiro episódio: não há o que relembrar
            return False
        key = catalog_fold(series_parts(ch.name)[0]).strip()
        last = _last_watch_before(win, key, cur)
        if last is None:
            return True                # entrou no meio da série: um resumo ajuda
        return time.time() - last > GAP   # voltou depois de um tempo (não no meio de uma maratona)
    except Exception:  # noqa: BLE001
        log.exception("Recap: decidindo se oferece")
        return False


def _get_llm(win):
    shared = getattr(getattr(win, "captions", None), "_llm", None) or getattr(win, "_summary_llm", None)
    if shared is not None:
        return shared
    try:
        from .captions import LLMTranslator, _enable_cuda_dirs, cuda_ready, gpu_available, llm_ready
        if llm_ready() and gpu_available() and cuda_ready():
            _enable_cuda_dirs()
            win._summary_llm = LLMTranslator()
            return win._summary_llm
    except Exception:  # noqa: BLE001
        log.exception("Recap: carregando a IA")
    return None


def _build_text(win, ch):
    """(texto do recap, nº de episódios usados) ou (None, 0) se não houver sinopse anterior."""
    title = series_parts(ch.name)[0]
    cur = _order(ch.name)
    eps = win.meta.episodes_with_plot(title)
    if not eps:
        return None, 0
    before = [e for e in eps if (e[0], e[1]) < cur and (e[3] or "").strip()]
    if not before:
        return None, 0
    before = before[-MAX_EPISODES:]
    cur_label = f"S{cur[0]:02d}E{cur[1]:02d}"
    lines = [f"- S{s:02d}E{n:02d}{(' ' + t) if t and not str(t).upper().startswith('S') else ''}: "
             f"{' '.join((plot or '').split())[:MAX_PLOT]}" for s, n, t, plot in before]
    fonte = "\n".join(lines)
    clean_title = " ".join(title.split())
    llm = _get_llm(win)
    if llm is not None:
        prompt = (f"Monte um recap de \"o que rolou até aqui\" na série {clean_title}, para quem vai assistir o "
                  f"{cur_label} e pode ter esquecido. Escreva de 2 a 4 frases, direto, sem listas e sem markdown, "
                  f"baseado SÓ nas sinopses dos episódios anteriores abaixo. Não conte nada do {cur_label} nem "
                  f"invente fatos.\n\nEpisódios anteriores:\n{fonte}")
        try:
            from .assistant import clean_reply
            with _LLM_LOCK:
                text = clean_reply(llm.chat(SYSTEM, [("user", prompt)], max_new=220))
            if text:
                return text, len(before)
        except Exception:  # noqa: BLE001
            log.exception("Recap: gerando o texto com a IA")
    # sem IA (ou falhou): usa as próprias sinopses dos últimos episódios
    tail = [" ".join((p or "").split()) for _s, _n, _t, p in before[-2:] if (p or "").strip()]
    if tail:
        return ("Nos episódios anteriores: " + " ".join(tail))[:600], len(before)
    return None, len(before)


class RecapDialog(QDialog):
    """Mostra o recap (gerado em segundo plano) e, ao fechar, toca o episódio."""
    _ready = pyqtSignal(str)

    def __init__(self, win, ch, proceed, parent=None):
        super().__init__(parent or win)
        self.win, self.ch, self.proceed = win, ch, proceed
        self._went = False
        self.setWindowTitle("Recap")
        self.setMinimumWidth(520)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 16, 20, 16)
        lay.setSpacing(10)
        lay.addWidget(QLabel("📝 O que rolou até aqui", objectName="h2"))
        o = _order(ch.name)
        sub = series_parts(ch.name)[0] + (f" — vai começar o S{o[0]:02d}E{o[1]:02d}" if o else "")
        lay.addWidget(QLabel(sub, objectName="muted", wordWrap=True))

        self.body = QLabel("Montando o resumo com a IA… 🍮", objectName="recapBody", wordWrap=True)
        self.body.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.body.setStyleSheet("font-size:15px; line-height:150%; padding:4px 2px;")
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        holder = QWidget()
        hv = QVBoxLayout(holder)
        hv.setContentsMargins(0, 0, 0, 0)
        hv.addWidget(self.body)
        hv.addStretch(1)
        scroll.setWidget(holder)
        scroll.setMinimumHeight(140)
        lay.addWidget(scroll, 1)

        self.never = QCheckBox("Não mostrar recaps")
        self.never.toggled.connect(self._toggle_never)
        lay.addWidget(self.never)

        row = QHBoxLayout()
        skip = QPushButton("Pular recap")
        skip.clicked.connect(self._go)
        self.play = QPushButton("  ▶ Assistir", objectName="primary")
        self.play.clicked.connect(self._go)
        row.addWidget(skip)
        row.addStretch(1)
        row.addWidget(self.play)
        lay.addLayout(row)

        self._ready.connect(self._show_text)
        self.finished.connect(lambda _r: self._go())   # fechar no X também toca
        threading.Thread(target=self._work, name="recap", daemon=True).start()

    def _work(self):
        text = None
        try:
            text, _n = _build_text(self.win, self.ch)
        except Exception:  # noqa: BLE001
            log.exception("Recap: montando")
        self._ready.emit(text or "")

    def _toggle_never(self, on):
        self.win.cfg["recap"] = not on
        self.win.cfg.save()

    def _show_text(self, text):
        self.body.setText(text or "Não achei um resumo dos episódios anteriores dessa série. É só dar o play! 🍿")

    def _go(self, *_):
        if self._went:
            return
        self._went = True
        if self.isVisible():
            self.accept()
        try:
            self.proceed()
        except Exception:  # noqa: BLE001
            log.exception("Recap: tocando depois do recap")


def maybe_recap(win, ch, proceed):
    """Se valer a pena, mostra o recap e toca o episódio quando a pessoa fechar; senão, toca direto."""
    try:
        if should_offer(win, ch):
            RecapDialog(win, ch, proceed).show()
            return
    except Exception:  # noqa: BLE001
        log.exception("Recap: abrindo")
    proceed()
