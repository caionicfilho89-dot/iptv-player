"""Controle por voz: aperte o microfone (Ctrl+Espaço), fale e o Pudim faz.

Grava do microfone padrão até a pessoa parar de falar (ou 7 s), reconhece com o Whisper "small" no processador
(o mesmo das legendas, já baixado) e transforma a frase num comando: "coloca no SporTV", "jogo do Corinthians",
"filme de terror dos anos 80", "volta 30 segundos", "pausa", "aumenta o volume", "liga a legenda"…"""
import re
import threading

from PyQt6.QtCore import QObject, pyqtSignal

from .captions import MODEL_DIR
from .log import log
from .sports import fold

RATE = 16000
MAX_SECONDS = 7
SILENCE_END = 1.1           # segundos de silêncio depois de falar = acabou
SILENCE_LEVEL = 0.012       # volume (RMS) abaixo disso é silêncio
NUMBERS = {"um": 1, "uma": 1, "dois": 2, "duas": 2, "tres": 3, "quatro": 4, "cinco": 5, "dez": 10, "quinze": 15,
           "vinte": 20, "trinta": 30, "quarenta": 40, "cinquenta": 50, "sessenta": 60, "noventa": 90}


def _num(text, default):
    m = re.search(r"\d+", text)
    if m:
        return int(m.group())
    return next((v for k, v in NUMBERS.items() if re.search(rf"\b{k}\b", text)), default)


def parse_command(text):
    """Frase falada -> (ação, argumento). Ações: seek, pause, stop, volume, mute, captions, dub, tv, home,
    game, channel, search."""
    t = fold(text).strip(" .!?,")
    t = re.sub(r"^(ei |oi |ok )?(pudim ?flix|pudim)[, ]*", "", t).strip()
    if not t:
        return None, None
    if re.search(r"\b(volta|voltar|retrocede|atras)\b", t) and re.search(r"segundo|minuto|\d", t):
        n = _num(t, 30) * (60 if "minuto" in t else 1)
        return "seek", -n
    if re.search(r"\b(avanca|avancar|adianta|pula|pular)\b", t) and re.search(r"segundo|minuto|\d", t):
        n = _num(t, 30) * (60 if "minuto" in t else 1)
        return "seek", n
    if re.search(r"o que (eu )?perdi|o que (que )?(aconteceu|rolou|passou)|\bresum", t):
        return "missed", None
    if re.fullmatch(r"(pausa|pausar|pause|para um pouco|espera)", t):
        return "pause", None
    if re.fullmatch(r"(continua|continuar|play|despausa|solta|volta a tocar)", t):
        return "pause", None
    if re.search(r"\b(desliga|para|parar|fecha) (o )?(video|canal|filme|tudo)\b", t):
        return "stop", None
    if re.search(r"\b(sem som|mudo|muta|silencio)\b", t):
        return "mute", None
    if "volume" in t or re.search(r"\b(mais alto|mais baixo|aumenta|abaixa|diminui)\b", t):
        up = bool(re.search(r"\b(aumenta|sobe|mais alto|mais)\b", t)) and not re.search(r"\b(abaixa|diminui|baixo)\b", t)
        return "volume", (10 if up else -10)
    if "legenda" in t:
        return "captions", not re.search(r"\b(desliga|tira|sem|remove)\b", t)
    if "dublag" in t or re.search(r"\bdubla\b", t):
        return "dub", not re.search(r"\b(desliga|tira|sem)\b", t)
    if re.search(r"\b(manda|mandar|passa|coloca|joga)\b.*\b(pra|para|na) (a )?tv\b", t) or t in ("na tv", "tv"):
        return "tv", True
    if re.search(r"\b(inicio|tela inicial|volta pro comeco|home)\b", t):
        return "home", None
    m = re.search(r"\bjogo (do|da|de) (.+)$", t)
    if m:
        return "game", m.group(2)
    m = re.match(r"(coloca|bota|poe|abre|muda|troca|vai)( pro| pra| para| no| na| o| a)* (canal )?(.+)$", t)
    if m and not re.match(r"(filme|serie|novela|desenho|documentario)", m.group(4)):
        return "channel", m.group(4)
    m = re.match(r"(quero ver |quero assistir |procura |busca |pesquisa |acha |me mostra |mostra )?(.+)$", t)
    return "search", m.group(2) if m else t


class VoiceControl(QObject):
    heard = pyqtSignal(str)       # frase reconhecida ("" = não entendeu)
    status = pyqtSignal(str)      # "Ouvindo…", "Entendendo…", mensagem de erro; "" = acabou

    def __init__(self, parent=None):
        super().__init__(parent)
        self.busy = False
        self._model = None

    def listen(self):
        if self.busy:
            return
        self.busy = True
        threading.Thread(target=self._run, name="voz", daemon=True).start()

    def _whisper(self):
        if self._model is None:
            import os
            from faster_whisper import WhisperModel
            from faster_whisper.utils import download_model
            target = MODEL_DIR / "small"
            if not (target / "model.bin").exists():
                self.status.emit("Baixando a IA de voz (~480 MB, só na primeira vez)…")
                download_model("small", output_dir=str(target))
            threads = max(2, min(8, (os.cpu_count() or 4) // 2))
            self._model = WhisperModel(str(target), device="cpu", compute_type="int8", cpu_threads=threads)
        return self._model

    def _record(self):
        import numpy as np
        import soundcard as sc
        from .captions import _com_init
        _com_init()
        mic = sc.default_microphone()
        chunks, spoke, quiet = [], False, 0.0
        step = 0.1
        with mic.recorder(samplerate=RATE, channels=1, blocksize=int(RATE * step)) as rec:
            for _ in range(int(MAX_SECONDS / step)):
                block = rec.record(numframes=int(RATE * step))[:, 0]
                chunks.append(block)
                level = float(np.sqrt(np.mean(block ** 2)))
                if level > SILENCE_LEVEL:
                    spoke, quiet = True, 0.0
                elif spoke:
                    quiet += step
                    if quiet >= SILENCE_END:
                        break
        return np.concatenate(chunks).astype("float32"), spoke

    def _run(self):
        try:
            self.status.emit("🎙️ Ouvindo… fale agora")
            audio, spoke = self._record()
            if not spoke:
                self.status.emit("Não ouvi nada. Confira se o microfone está ligado.")
                self.heard.emit("")
                return
            self.status.emit("🎙️ Entendendo…")
            segs, _info = self._whisper().transcribe(audio, language="pt", beam_size=1, vad_filter=True,
                                                     initial_prompt="Comandos para um player de TV: coloca no "
                                                     "SporTV, jogo do Corinthians, filme de terror, volta 30 segundos.")
            text = " ".join(s.text.strip() for s in segs).strip()
            log.info("Voz: %r", text)
            self.status.emit("")
            self.heard.emit(text)
        except Exception as e:  # noqa: BLE001
            log.exception("Controle por voz")
            self.status.emit(f"Não deu para usar o microfone: {e}")
            self.heard.emit("")
        finally:
            self.busy = False
