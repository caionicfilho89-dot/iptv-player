"""Dublagem por IA: fala a tradução das legendas em português.

As frases chegam da IA de legendas; a voz é gerada no próprio PC e tocada pelo AudioEngine, que
abaixa o som original enquanto ela fala. Vozes:
- naturais (Kokoro): "Dora" e "Alex", soam quase humanas; ~350 MB baixados na primeira vez;
- leve (Piper): "Cadu", rápida e pequena (~60 MB);
- do Windows (SAPI): Maria/Daniel, já instaladas, mais robóticas.
"""
import queue
import threading
import urllib.request

import numpy as np
from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from .captions import TARGET
from .log import log
from .paths import CACHE

ONECORE = r"HKEY_LOCAL_MACHINE\SOFTWARE\Microsoft\Speech_OneCore\Voices"
SAFT_48K_MONO = 38           # SpeechAudioFormatType: 48 kHz, 16 bits, mono
SETTLE_MS = 900              # espera a frase "assentar" antes de falar (pode chegar a continuação)
MAX_BACKLOG = 10.0           # segundos de fala atrasada: acima disso, frases antigas são puladas
OUT_RATE = 48000             # taxa do AudioEngine
TTS_DIR = CACHE / "tts"
KOKORO = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/"
PIPER = "https://huggingface.co/rhasspy/piper-voices/resolve/main/pt/pt_BR/"

# chave -> (rótulo nas configurações, tamanho do download em MB)
NEURAL = {
    "kokoro:pf_dora": ("Dora — natural (feminina)", 350),
    "kokoro:pm_alex": ("Alex — natural (masculina)", 350),
    "piper:cadu": ("Cadu — leve (masculina)", 63),
}
DEFAULT_VOICE = "kokoro:pf_dora"


# ---------------------------------------------------------------- vozes do Windows (SAPI)
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


def windows_voices():
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


def voices():
    """(chave, rótulo) de todas as vozes para escolher nas configurações."""
    out = []
    for key, (label, mb) in NEURAL.items():
        ready = _files(key) and all(p.exists() for p, _ in _files(key))
        out.append((key, label if ready else f"{label} · baixa {mb} MB"))
    for name in windows_voices():
        out.append((f"sapi:{name}", name.replace("Microsoft ", "").split(" - ")[0] + " — Windows"))
    return out


def _files(key):
    """Arquivos de uma voz neural: [(caminho local, endereço para baixar)]."""
    kind, name = key.split(":", 1)
    if kind == "kokoro":
        return [(TTS_DIR / "kokoro-v1.0.onnx", KOKORO + "kokoro-v1.0.onnx"),
                (TTS_DIR / "voices-v1.0.bin", KOKORO + "voices-v1.0.bin")]
    if kind == "piper":
        base = f"pt_BR-{name}-medium.onnx"
        return [(TTS_DIR / base, f"{PIPER}{name}/medium/{base}"),
                (TTS_DIR / f"{base}.json", f"{PIPER}{name}/medium/{base}.json")]
    return []


def _resample(pcm, sr):
    if sr == OUT_RATE:
        return pcm.astype(np.float32)
    n = int(len(pcm) * OUT_RATE / sr)
    return np.interp(np.arange(n) * sr / OUT_RATE, np.arange(len(pcm)), pcm).astype(np.float32)


class _Sapi:
    def __init__(self, name):
        import comtypes
        import comtypes.client as cc
        comtypes.CoInitialize()  # o SAPI precisa do COM nesta tarefa (no modo que o comtypes usa)
        self.cc = cc
        self.voice = cc.CreateObject("SAPI.SpVoice", dynamic=True)
        toks = _tokens()
        pick = next((t for t in toks if t.GetDescription() == name), None) \
            or next((t for t in toks if "portug" in t.GetDescription().lower()), None)
        if pick is not None:
            self.voice.Voice = pick
        self.fmt = cc.CreateObject("SAPI.SpAudioFormat", dynamic=True)
        self.fmt.Type = SAFT_48K_MONO

    def speak(self, text, speed):
        self.voice.Rate = max(0, min(8, round((speed - 1.0) * 10)))
        ms = self.cc.CreateObject("SAPI.SpMemoryStream", dynamic=True)
        ms.Format = self.fmt
        self.voice.AudioOutputStream = ms
        self.voice.Speak(text, 0)
        return np.frombuffer(bytes(ms.GetData()), np.int16).astype(np.float32) / 32768.0


class _Kokoro:
    def __init__(self, name):
        from kokoro_onnx import Kokoro
        model, voices_bin = (p for p, _ in _files(f"kokoro:{name}"))
        self.k = Kokoro(str(model), str(voices_bin))
        self.name = name

    def speak(self, text, speed):
        pcm, sr = self.k.create(text, voice=self.name, speed=min(1.6, speed), lang="pt-br")
        return _resample(pcm, sr)


class _Piper:
    def __init__(self, name):
        from piper import PiperVoice
        self.v = PiperVoice.load(str(_files(f"piper:{name}")[0][0]))

    def speak(self, text, speed):
        from piper import SynthesisConfig
        chunks = list(self.v.synthesize(text, syn_config=SynthesisConfig(length_scale=1 / min(1.8, speed))))
        if not chunks:
            return np.zeros(0, np.float32)
        return _resample(np.concatenate([c.audio_float_array for c in chunks]), chunks[0].sample_rate)


class Dubber(QObject):
    """Decide quando falar cada frase e gera a voz numa tarefa separada."""
    status = pyqtSignal(str)          # avisos para a tela (download da voz, erros)

    def __init__(self, engine, voice="", parent=None):
        super().__init__(parent)
        self.engine = engine
        self.voice_key = voice if ":" in voice else (f"sapi:{voice}" if voice else DEFAULT_VOICE)
        self.full = ""                # versão mais recente da frase atual
        self.spoken = 0               # quantas palavras dela já foram faladas
        self.timer = QTimer(self, singleShot=True, interval=SETTLE_MS, timeout=self._speak_pending)
        self._q = queue.Queue()
        self._gen = 0
        self._stop = threading.Event()
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
        self._stop.set()
        self.reset()
        self._q.put(None)

    def _speak_pending(self):
        words = self.full.split()
        rest = words[self.spoken:]
        if rest:
            self.spoken = len(words)
            self._q.put((self._gen, " ".join(rest)))

    # ---- tarefa da voz
    def _download(self, key):
        TTS_DIR.mkdir(parents=True, exist_ok=True)
        total = NEURAL[key][1]
        done = 0
        for path, url in _files(key):
            if path.exists():
                continue
            part = path.with_suffix(path.suffix + ".part")
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "IPTV-Player"}),
                                        timeout=30) as r, open(part, "wb") as f:
                while chunk := r.read(1 << 20):
                    if self._stop.is_set():
                        raise RuntimeError("cancelado")
                    f.write(chunk)
                    done += len(chunk)
                    self.status.emit(f"Baixando a voz da dublagem ({done / 1e6:.0f} de ~{total} MB) — "
                                     "só na primeira vez…")
            part.replace(path)
        self.status.emit("")

    def _load_voice(self):
        key = self.voice_key
        kind, name = key.split(":", 1)
        if kind in ("kokoro", "piper"):
            try:
                if not all(p.exists() for p, _ in _files(key)):
                    self._download(key)
                voice = (_Kokoro if kind == "kokoro" else _Piper)(name)
                log.info("Dublagem: voz %s", key)
                return voice
            except Exception:  # noqa: BLE001 - sem internet, pacote ausente…: usa a voz do Windows
                if self._stop.is_set():
                    raise
                log.exception("Dublagem: voz %s indisponível; usando a do Windows", key)
                self.status.emit("")
                name = ""
        voice = _Sapi(name)
        log.info("Dublagem: voz do Windows %s", name or "(padrão em português)")
        return voice

    def _synth_loop(self):
        try:
            voice = self._load_voice()
        except Exception:  # noqa: BLE001
            if not self._stop.is_set():
                log.exception("Dublagem: não foi possível iniciar a voz")
                self.status.emit("Não foi possível iniciar a voz da dublagem")
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
            speed = 1.1 + min(0.5, backlog * 0.1)
            try:
                pcm = voice.speak(text, speed)
            except Exception:  # noqa: BLE001
                log.exception("Dublagem: erro ao gerar a voz")
                continue
            if gen == self._gen and len(pcm):
                self.engine.say(pcm)
