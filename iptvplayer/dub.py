"""Dublagem por IA: fala a tradução das legendas em português com uma voz do Windows.

As frases chegam da IA de legendas; a voz é gerada pelo SAPI (vozes do Windows) e tocada pelo
AudioEngine, que abaixa o som original enquanto ela fala.
"""
import queue
import threading

from PyQt6.QtCore import QObject, QTimer

from .captions import TARGET
from .log import log

ONECORE = r"HKEY_LOCAL_MACHINE\SOFTWARE\Microsoft\Speech_OneCore\Voices"
SAFT_48K_MONO = 38           # SpeechAudioFormatType: 48 kHz, 16 bits, mono
SETTLE_MS = 900              # espera a frase "assentar" antes de falar (pode chegar a continuação)
MAX_BACKLOG = 10.0           # segundos de fala atrasada: acima disso, frases antigas são puladas


def _tokens():
    """Vozes instaladas: as novas (OneCore) primeiro, depois as do SAPI clássico."""
    import comtypes.client as cc
    out = []
    for category in (ONECORE, None):
        try:
            if category:
                cat = cc.CreateObject("SAPI.SpObjectTokenCategory", dynamic=True)
                cat.SetId(category, False)
                toks = cat.EnumerateTokens()
            else:
                toks = cc.CreateObject("SAPI.SpVoice", dynamic=True).GetVoices()
            out += [toks.Item(i) for i in range(toks.Count)]
        except Exception:  # noqa: BLE001 - categoria inexistente neste Windows
            pass
    return out


def voices():
    """Nomes das vozes em português do Windows (ex.: "Microsoft Maria - Portuguese (Brazil)")."""
    try:
        names = [t.GetDescription() for t in _tokens()]  # thread principal: o Qt já iniciou o COM
    except Exception:  # noqa: BLE001 - SAPI indisponível
        return []
    seen, out = set(), []
    for n in names:
        if "portug" in n.lower() and n not in seen:
            seen.add(n)
            out.append(n)
    return out


class Dubber(QObject):
    """Decide quando falar cada frase e gera a voz numa tarefa separada."""

    def __init__(self, engine, voice="", parent=None):
        super().__init__(parent)
        self.engine = engine
        self.voice_name = voice
        self.full = ""                # versão mais recente da frase atual
        self.spoken = 0               # quantas palavras dela já foram faladas
        self.timer = QTimer(self, singleShot=True, interval=SETTLE_MS, timeout=self._speak_pending)
        self._q = queue.Queue()
        self._gen = 0
        threading.Thread(target=self._synth_loop, name="dublagem", daemon=True).start()

    # ---- interface (thread principal)
    def on_caption(self, text, lang, replace):
        if not text or lang == TARGET:
            return  # canal já em português: nada para dublar
        if not replace:
            self._speak_pending()     # a frase anterior terminou: fala o que faltava dela
            self.full, self.spoken = text, 0
        else:
            self.full = text          # mesma frase, agora mais completa
        self.timer.start()

    def reset(self):
        """Troca de canal: descarta o que ainda ia ser falado."""
        self.timer.stop()
        self.full, self.spoken = "", 0
        self._gen += 1
        self.engine.clear_speech()

    def stop(self):
        self.reset()
        self._q.put(None)

    def _speak_pending(self):
        words = self.full.split()
        rest = words[self.spoken:]
        if rest:
            self.spoken = len(words)
            self._q.put((self._gen, " ".join(rest)))

    # ---- tarefa da voz
    def _synth_loop(self):
        try:
            import comtypes
            import comtypes.client as cc
            import numpy as np
            comtypes.CoInitialize()  # o SAPI precisa do COM nesta tarefa (no modo que o comtypes usa)
            voice = cc.CreateObject("SAPI.SpVoice", dynamic=True)
            toks = _tokens()
            pick = next((t for t in toks if t.GetDescription() == self.voice_name), None) \
                or next((t for t in toks if "portug" in t.GetDescription().lower()), None)
            if pick is not None:
                voice.Voice = pick
                log.info("Dublagem: voz %s", pick.GetDescription())
            else:
                log.warning("Dublagem: nenhuma voz em português instalada; usando a voz padrão")
            fmt = cc.CreateObject("SAPI.SpAudioFormat", dynamic=True)
            fmt.Type = SAFT_48K_MONO
        except Exception:  # noqa: BLE001
            log.exception("Dublagem: não foi possível iniciar a voz do Windows")
            return
        while True:
            item = self._q.get()
            if item is None:
                return
            gen, text = item
            backlog = self.engine.speech_backlog()
            if gen != self._gen or (backlog > MAX_BACKLOG and not self._q.empty()):
                continue  # canal mudou, ou atrasou demais: pula para as frases mais novas
            # fala um pouco mais rápido que o normal e acelera se estiver ficando para trás
            voice.Rate = min(7, 1 + int(backlog))
            try:
                ms = cc.CreateObject("SAPI.SpMemoryStream", dynamic=True)
                ms.Format = fmt
                voice.AudioOutputStream = ms
                voice.Speak(text, 0)
                pcm = np.frombuffer(bytes(ms.GetData()), np.int16).astype(np.float32) / 32768.0
            except Exception:  # noqa: BLE001
                log.exception("Dublagem: erro ao gerar a voz")
                continue
            if gen == self._gen and len(pcm):
                self.engine.say(pcm)
