"""Som do canal tocado pelo próprio programa (usado na dublagem por IA).

Normalmente o VLC manda o som direto para a placa de som. Com a dublagem ligada, o VLC entrega o som para
cá; o programa toca cada pedaço na hora certa (sincronizado com o vídeo), mistura a voz da dublagem
abaixando o som original e manda uma cópia limpa (sem a dublagem) para a IA reconhecer a fala.
"""
import ctypes
import threading
from collections import deque

from .log import log
from .vlcload import vlc

RATE = 48000
CHANNELS = 2
OUT_BLOCK = 960              # 20 ms por vez para a placa de som
OUT_LATENCY = 60_000         # µs que o som leva da fila até sair na caixa
LATE_DROP = 250_000          # pedaço atrasado mais que isso é descartado (pula para o presente)
AI_RATE = 16000
AI_BLOCK = AI_RATE // 10     # mesmo bloco de 100 ms da captura das legendas
RAMP = 0.15                  # segundos para abaixar/voltar o som original


class AudioEngine:
    def __init__(self):
        self.player = None
        self.queue = deque()             # (pts em µs, amostras float32 [n, 2])
        self.cur = None                  # pedaço tocando agora e posição dentro dele
        self.pos = 0
        self.volume, self.muted = 1.0, False
        self.paused_at = None
        self.speech = deque()            # falas da dublagem prontas para tocar (float32 [n, 2])
        self.speech_pos = 0
        self.duck_level = 0.25           # volume do original enquanto a dublagem fala
        self.gain = 1.0
        self.ai_sink = None              # recebe blocos de 100 ms, 16 kHz mono, para a IA
        self._ai_rest = None
        self._ai_buf = []
        self._stop = threading.Event()
        self._thread = None
        self._cbs = None

    # ---------------------------------------------------------------- VLC
    @property
    def attached(self):
        return self.player is not None

    def attach(self, player):
        """Passa o som do player para o programa. Vale a partir do próximo play()."""
        import numpy as np
        D = vlc.CallbackDecorators

        @D.AudioPlayCb
        def play(_o, samples, count, pts):
            data = np.frombuffer(ctypes.string_at(samples, count * 2 * CHANNELS), np.int16)
            block = data.reshape(-1, CHANNELS).astype(np.float32) / 32768.0
            self.queue.append((pts, block))
            if self.ai_sink:
                self._feed_ai(block)

        @D.AudioPauseCb
        def pause(_o, pts):
            self.paused_at = pts or vlc.libvlc_clock()

        @D.AudioResumeCb
        def resume(_o, pts):
            # tudo que estava na fila passa a tocar depois da pausa
            if self.paused_at is not None:
                shift = (pts or vlc.libvlc_clock()) - self.paused_at
                self.queue = deque((p + shift, b) for p, b in self.queue)
            self.paused_at = None

        @D.AudioFlushCb
        def flush(_o, _pts):
            self.queue.clear()
            self.cur = None

        @D.AudioDrainCb
        def drain(_o):
            pass

        @D.AudioSetVolumeCb
        def set_volume(_o, volume, mute):
            self.volume, self.muted = float(volume), bool(mute)

        self._cbs = (play, pause, resume, flush, drain, set_volume)  # o ctypes não pode perder as funções
        player.audio_set_callbacks(play, pause, resume, flush, drain, None)
        player.audio_set_volume_callback(set_volume)
        player.audio_set_format("S16N", RATE, CHANNELS)
        self.player = player
        self._stop.clear()
        self._thread = threading.Thread(target=self._output_loop, name="som", daemon=True)
        self._thread.start()
        log.info("Som: tocado pelo programa (dublagem)")

    def detach(self):
        """Devolve o som para o VLC. Chame com o player parado; vale a partir do próximo play()."""
        if not self.player:
            return
        self.player.audio_output_set("mmdevice")  # saída padrão do VLC no Windows
        self.player = None
        self._stop.set()
        if self._thread:
            self._thread.join(1.0)
        self.queue.clear()
        self.speech.clear()
        self.cur = None
        log.info("Som: de volta para o VLC")

    # ---------------------------------------------------------------- dublagem
    def say(self, pcm):
        """Toca uma fala (float32 mono ou [n, 2], 48 kHz) por cima do som do canal."""
        import numpy as np
        if pcm.ndim == 1:
            pcm = np.repeat(pcm[:, None], CHANNELS, axis=1)
        self.speech.append(pcm)

    def speech_backlog(self):
        """Segundos de fala na fila, para a dublagem acelerar se estiver atrasando."""
        total = sum(len(s) for s in self.speech) - self.speech_pos
        return max(0, total) / RATE

    def clear_speech(self):
        self.speech.clear()
        self.speech_pos = 0

    # ---------------------------------------------------------------- cópia para a IA
    def _feed_ai(self, block):
        import numpy as np
        mono = block.mean(axis=1)
        if self._ai_rest is not None:
            mono = np.concatenate([self._ai_rest, mono])
        n = len(mono) // 3 * 3
        self._ai_rest = mono[n:]
        self._ai_buf.append(mono[:n].reshape(-1, 3).mean(axis=1))  # 48 kHz -> 16 kHz
        total = sum(len(b) for b in self._ai_buf)
        if total >= AI_BLOCK:
            buf = np.concatenate(self._ai_buf)
            k = len(buf) // AI_BLOCK * AI_BLOCK
            for i in range(0, k, AI_BLOCK):
                self.ai_sink(buf[i:i + AI_BLOCK])
            self._ai_buf = [buf[k:]]

    # ---------------------------------------------------------------- saída
    def _next_original(self, n):
        """n amostras do canal na hora certa (silêncio enquanto ainda é cedo)."""
        import numpy as np
        out = np.zeros((n, CHANNELS), np.float32)
        if self.paused_at is not None:
            return out
        now = vlc.libvlc_clock()
        filled = 0
        while filled < n:
            if self.cur is None:
                while self.queue and self.queue[0][0] + len(self.queue[0][1]) * 1e6 / RATE < now - LATE_DROP:
                    self.queue.popleft()  # atrasado demais: pula
                if not self.queue or self.queue[0][0] > now + OUT_LATENCY + filled * 1e6 / RATE:
                    break  # ainda não é hora
                self.cur, self.pos = self.queue.popleft()[1], 0
            take = min(n - filled, len(self.cur) - self.pos)
            out[filled:filled + take] = self.cur[self.pos:self.pos + take]
            filled += take
            self.pos += take
            if self.pos >= len(self.cur):
                self.cur = None
        return out

    def _next_speech(self, n):
        import numpy as np
        out = np.zeros((n, CHANNELS), np.float32)
        filled = 0
        while filled < n and self.speech:
            s = self.speech[0]
            take = min(n - filled, len(s) - self.speech_pos)
            out[filled:filled + take] = s[self.speech_pos:self.speech_pos + take]
            filled += take
            self.speech_pos += take
            if self.speech_pos >= len(s):
                self.speech.popleft()
                self.speech_pos = 0
        return out, filled > 0

    def _output_loop(self):
        import numpy as np
        try:
            import soundcard as sc
            from .captions import _com_init
            _com_init()
            speaker = sc.default_speaker()
            step = 1.0 / (RAMP * RATE / OUT_BLOCK)
            with speaker.player(samplerate=RATE, channels=CHANNELS, blocksize=OUT_BLOCK) as sp:
                while not self._stop.is_set():
                    orig = self._next_original(OUT_BLOCK)
                    speech, talking = self._next_speech(OUT_BLOCK)
                    target = self.duck_level if talking else 1.0
                    # rampa suave: o som original abaixa e volta sem "pulos"
                    self.gain += max(-step, min(step, target - self.gain))
                    vol = 0.0 if self.muted else self.volume ** 3  # mesma curva de volume do VLC
                    mix = (orig * self.gain + speech * 0.9) * vol
                    sp.play(np.clip(mix, -1.0, 1.0))
        except Exception:  # noqa: BLE001
            log.exception("Som: a saída de áudio parou")
