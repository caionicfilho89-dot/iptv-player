"""Legendas traduzidas ao vivo, como a tradução automática do YouTube.

Ouve o som que está saindo do computador (loopback do Windows), reconhece a fala com o
Whisper rodando no próprio PC (faster-whisper) e traduz o texto para o português.
As bibliotecas de IA são opcionais: sem elas o botão de legendas só explica o que falta.
"""
import json
import os
import queue
import re
import threading
import time
import urllib.parse
import urllib.request
import warnings
from collections import deque

from PyQt6.QtCore import QPoint, QRect, QRectF, Qt, QThread, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPainterPath
from PyQt6.QtWidgets import QWidget

from .paths import CACHE

# huggingface_hub escreve barras de progresso no stderr, que não existe no .exe sem console
os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
# o soundcard avisa a cada pequena falha na captura; não afeta a legenda
warnings.filterwarnings("ignore", message="data discontinuity")

MODEL_DIR = CACHE / "whisper"
RATE = 16000
BLOCK = RATE // 10           # blocos de 100 ms
MIN_SEG = 2.0                # segundos de fala antes de aceitar uma pausa como fim de frase
MAX_SEG = 5.0                # corta a frase à força para a legenda não atrasar demais
PAUSE_BLOCKS = 4             # 400 ms de silêncio = fim de frase
MAX_BACKLOG = 12.0           # se a IA ficar para trás, descarta o áudio mais antigo

MODELS = {  # nome -> (rótulo, tamanho aproximado do download em MB)
    "base": ("Rápido (menos preciso)", 145),
    "small": ("Equilibrado (recomendado)", 484),
    "medium": ("Preciso (exige PC mais forte)", 1530),
}
SOURCE_LANGS = {
    "auto": "Detectar automaticamente", "en": "Inglês", "es": "Espanhol", "fr": "Francês",
    "it": "Italiano", "de": "Alemão", "ru": "Russo", "ar": "Árabe", "tr": "Turco", "ja": "Japonês",
    "ko": "Coreano", "zh": "Chinês", "hi": "Hindi", "nl": "Holandês", "pl": "Polonês", "pt": "Português",
}
TARGET = "pt"
# frases que o Whisper costuma "inventar" em música ou silêncio
HALLUCINATIONS = re.compile(
    r"(obrigad[oa] por assistir|legendas? (pela|por)|inscreva-se|thanks for watching|thank you for watching|"
    r"subtitles by|amara\.org|please subscribe|www\.|\.com\b|♪|^\W*$)", re.I)


def missing_deps():
    """Lista de pacotes que faltam para as legendas funcionarem."""
    missing = []
    for mod, pkg in (("faster_whisper", "faster-whisper"), ("soundcard", "soundcard"), ("numpy", "numpy")):
        try:
            __import__(mod)
        except Exception:  # noqa: BLE001 - DLL ausente também conta como "não instalado"
            missing.append(pkg)
    return missing


_tr_cache = {}


GOOGLE_CODES = {"zh": "zh-CN", "he": "iw", "jw": "jv"}  # códigos do Whisper que o Google escreve diferente


def translate(text, src, dst=TARGET):
    """Tradução pelo Google Tradutor (mesmo serviço da tradução automática do YouTube)."""
    try:
        return _translate(text, GOOGLE_CODES.get(src, src), dst)
    except Exception:  # noqa: BLE001 - idioma não reconhecido pelo Google: deixa ele detectar
        if not src or src == "auto":
            raise
        return _translate(text, "auto", dst)


def _translate(text, src, dst):
    key = (text, src, dst)
    if key in _tr_cache:
        return _tr_cache[key]
    q = urllib.parse.urlencode({"client": "gtx", "sl": src or "auto", "tl": dst, "dt": "t", "q": text})
    req = urllib.request.Request("https://translate.googleapis.com/translate_a/single?" + q,
                                 headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=6) as r:
        data = json.loads(r.read().decode("utf-8"))
    out = "".join(part[0] for part in data[0] if part and part[0]).strip()
    if len(_tr_cache) > 300:
        _tr_cache.clear()
    _tr_cache[key] = out
    return out


def _com_init():
    """O soundcard usa COM do Windows, que precisa ser iniciado em cada thread."""
    try:
        import ctypes
        ctypes.windll.ole32.CoInitializeEx(None, 0)
    except (AttributeError, OSError):
        pass


def _dir_mb(path):
    total = 0
    for root, _dirs, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total / 1e6


class CaptionWorker(QThread):
    """Captura o áudio, transcreve e traduz em segundo plano."""
    caption = pyqtSignal(str, str, str)   # original, tradução, idioma
    status = pyqtSignal(str)
    failed = pyqtSignal(str)
    ready = pyqtSignal()

    def __init__(self, model_name="small", source="auto", parent=None):
        super().__init__(parent)
        self.model_name = model_name if model_name in MODELS else "small"
        self.source = source
        self._stop = threading.Event()
        self._active = threading.Event()
        self._reset = threading.Event()
        self._audio = queue.Queue()
        self._lang_votes = deque(maxlen=6)
        self.locked_lang = None

    # ---- chamados pela interface
    def set_active(self, on):
        (self._active.set if on else self._active.clear)()
        self._reset.set()

    def reset(self):
        """Canal novo: esquece o áudio acumulado e volta a detectar o idioma."""
        self._reset.set()

    def stop(self):
        self._stop.set()
        self._active.set()  # acorda o laço para ele terminar

    # ---- thread
    def run(self):
        try:
            model = self._load_model()
        except Exception as e:  # noqa: BLE001
            if not self._stop.is_set():
                self.failed.emit(f"Não foi possível carregar a IA de legendas: {e}")
            return
        if self._stop.is_set():
            return
        self.ready.emit()
        while not self._stop.is_set():
            self._active.wait()
            if self._stop.is_set():
                break
            try:
                self._listen(model)
            except Exception as e:  # noqa: BLE001
                self.failed.emit(f"Erro ao ouvir o áudio: {e}")
                self._active.clear()

    def _load_model(self):
        from faster_whisper import WhisperModel
        from faster_whisper.utils import download_model

        MODEL_DIR.mkdir(parents=True, exist_ok=True)
        label, size_mb = MODELS[self.model_name]
        target = MODEL_DIR / self.model_name
        if not (target / "model.bin").exists():
            result = {}

            def dl():
                try:
                    result["path"] = download_model(self.model_name, output_dir=str(target))
                except Exception as e:  # noqa: BLE001
                    result["error"] = e
            t = threading.Thread(target=dl, daemon=True)
            t.start()
            while t.is_alive():
                if self._stop.is_set():
                    raise RuntimeError("cancelado")
                self.status.emit(f"Baixando a IA de legendas ({_dir_mb(target):.0f} de ~{size_mb} MB) — "
                                 "só na primeira vez…")
                t.join(0.7)
            if "error" in result:
                raise result["error"]
        self.status.emit("Carregando a IA de legendas…")
        threads = max(2, min(8, (os.cpu_count() or 4) // 2))
        return WhisperModel(str(target), device="cpu", compute_type="int8", cpu_threads=threads)

    def _listen(self, model):
        import numpy as np
        _com_init()
        import soundcard as sc

        speaker = sc.default_speaker()
        mic = sc.get_microphone(id=str(speaker.name), include_loopback=True)
        capture_stop = threading.Event()

        def capture():
            _com_init()
            try:
                with mic.recorder(samplerate=RATE, channels=1, blocksize=BLOCK) as rec:
                    while not capture_stop.is_set():
                        self._audio.put(rec.record(numframes=BLOCK)[:, 0].astype(np.float32))
            except Exception as e:  # noqa: BLE001
                self._audio.put(e)

        th = threading.Thread(target=capture, daemon=True)
        th.start()
        self.status.emit("")
        buf, levels = [], deque(maxlen=150)   # ~15 s de histórico para achar o nível de ruído
        try:
            while self._active.is_set() and not self._stop.is_set():
                if self._reset.is_set():
                    self._reset.clear()
                    buf.clear()
                    self._lang_votes.clear()
                    self.locked_lang = None
                    self._drain()
                try:
                    block = self._audio.get(timeout=0.5)
                except queue.Empty:
                    continue
                if isinstance(block, Exception):
                    raise block
                # atrasou (IA lenta)? pula para o presente
                if self._audio.qsize() * 0.1 > MAX_BACKLOG:
                    self._drain()
                    buf.clear()
                    continue
                buf.append(block)
                levels.append(float(np.sqrt(np.mean(block * block))))
                floor = float(np.percentile(levels, 20)) if len(levels) > 10 else 0.0
                thr = max(0.004, floor * 2.2)
                voiced = [lv > thr for lv in list(levels)[-len(buf):]]
                dur = len(buf) * 0.1
                if not any(voiced):
                    del buf[:-3]  # silêncio: guarda só um pedacinho para não cortar o início da fala
                    continue
                tail = 0
                for v in reversed(voiced):
                    if v:
                        break
                    tail += 1
                if (dur >= MIN_SEG and tail >= PAUSE_BLOCKS) or dur >= MAX_SEG:
                    if tail >= PAUSE_BLOCKS:
                        cut = len(buf)
                    else:  # corta no ponto mais baixo do último 1,5 s
                        recent = list(levels)[-15:]
                        cut = len(buf) - 15 + min(range(len(recent)), key=recent.__getitem__) + 1
                        cut = max(1, min(len(buf), cut))
                    seg, buf = np.concatenate(buf[:cut]), buf[cut:]
                    self._process(model, seg)
        finally:
            capture_stop.set()
            th.join(1.5)
            self._drain()

    def _drain(self):
        try:
            while True:
                self._audio.get_nowait()
        except queue.Empty:
            pass

    def _process(self, model, audio):
        peak = float(abs(audio).max())
        if peak < 1e-3:
            return
        audio = audio * min(20.0, 0.9 / peak)  # o volume do player não pode atrapalhar o reconhecimento
        lang = self.source if self.source != "auto" else self.locked_lang
        segments, info = model.transcribe(
            audio, language=lang, beam_size=2, vad_filter=True, condition_on_previous_text=False,
            vad_parameters={"min_silence_duration_ms": 300})
        parts = [s.text.strip() for s in segments if s.no_speech_prob < 0.6 and s.avg_logprob > -1.1]
        text = re.sub(r"\s+", " ", " ".join(parts)).strip()
        if not text or HALLUCINATIONS.search(text) or self._reset.is_set():
            return
        detected = lang or info.language
        if self.source == "auto" and not self.locked_lang and info.language_probability > 0.6:
            self._lang_votes.append(info.language)
            best = max(set(self._lang_votes), key=self._lang_votes.count)
            if self._lang_votes.count(best) >= 3:
                self.locked_lang = best  # fixa o idioma do canal: detecção fica mais estável
        if detected == TARGET:
            return self.caption.emit(text, text, detected)
        try:
            translated = translate(text, detected)
        except Exception:  # noqa: BLE001 - sem internet: mostra o original
            translated = ""
        if not self._reset.is_set():
            self.caption.emit(text, translated, detected)


class SubtitleOverlay(QWidget):
    """Legenda estilo YouTube sobre o vídeo (janela própria, pois o vídeo do VLC é nativo)."""
    LINE_TTL = 7.0

    def __init__(self, owner):
        super().__init__(owner, self._flags())
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.lines = deque(maxlen=2)   # (texto, original, expira_em)
        self.notice = ""
        self.scale = 1.0
        self.show_original = False
        self.video_rect = QRect()
        self.bottom_margin = 0

    @staticmethod
    def _flags():
        return (Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowDoesNotAcceptFocus
                | Qt.WindowType.WindowTransparentForInput | Qt.WindowType.NoDropShadowWindowHint)

    def set_owner(self, owner):
        visible = self.isVisible()
        self.setParent(owner, self._flags())
        if visible:
            self.show()

    def add(self, text, original=""):
        self.notice = ""
        self.lines.append((text, original if original != text else "", time.monotonic() + self.LINE_TTL))
        self._relayout()

    def set_notice(self, text):
        self.notice = text
        self._relayout()

    def clear(self):
        self.lines.clear()
        self.notice = ""
        self._relayout()

    def expire(self):
        now = time.monotonic()
        if any(exp < now for _t, _o, exp in self.lines):
            self.lines = deque(((t, o, e) for t, o, e in self.lines if e >= now), maxlen=2)
            self._relayout()

    def place(self, video_rect, bottom_margin=0):
        if (video_rect, bottom_margin) == (self.video_rect, self.bottom_margin):
            return
        self.video_rect, self.bottom_margin = video_rect, bottom_margin
        self._relayout()

    # ---- desenho
    def _fonts(self):
        h = max(120, self.video_rect.height())
        px = int(max(13, min(46, h * 0.042)) * self.scale)
        main = QFont("Segoe UI", -1, QFont.Weight.DemiBold)
        main.setPixelSize(px)
        small = QFont("Segoe UI")
        small.setPixelSize(max(11, int(px * 0.68)))
        return main, small

    def _blocks(self):
        items = []
        if self.notice:
            items.append((self.notice, True))
        for text, original, _exp in self.lines:
            if self.show_original and original:
                items.append((original, True))
            items.append((text or original, False))
        return items

    def _relayout(self):
        blocks = self._blocks()
        if not blocks or self.video_rect.width() < 100:
            self.hide()
            return
        main, small = self._fonts()
        max_w = int(self.video_rect.width() * 0.86)
        pad_x, pad_y, gap = 12, 4, 3
        self._layout = []
        y, width = 0, 0
        for text, is_small in blocks:
            fm = QFontMetrics(small if is_small else main)
            r = fm.boundingRect(QRect(0, 0, max_w - 2 * pad_x, 2000),
                                int(Qt.AlignmentFlag.AlignHCenter | Qt.TextFlag.TextWordWrap), text)
            w, h = r.width() + 2 * pad_x, r.height() + 2 * pad_y
            self._layout.append((text, is_small, y, w, h))
            y += h + gap
            width = max(width, w)
        height = y - gap
        vr = self.video_rect
        x = vr.x() + (vr.width() - width) // 2
        top = vr.bottom() - height - max(int(vr.height() * 0.07), self.bottom_margin)
        self.setGeometry(x, top, width, height)
        self._fonts_cache = (main, small)
        self.update()
        if not self.isVisible():
            self.show()

    def paintEvent(self, e):
        if not getattr(self, "_layout", None):
            return
        main, small = self._fonts_cache
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        for text, is_small, y, w, h in self._layout:
            box = QRectF((self.width() - w) / 2, y, w, h)
            path = QPainterPath()
            path.addRoundedRect(box, 6, 6)
            p.fillPath(path, QColor(8, 8, 10, 150 if is_small else 185))
            p.setFont(small if is_small else main)
            p.setPen(QColor(200, 204, 214) if is_small else QColor(255, 255, 255))
            p.drawText(box.adjusted(12, 4, -12, -4), int(Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap),
                       text)


def video_rect_global(widget):
    return QRect(widget.mapToGlobal(QPoint(0, 0)), widget.size())
