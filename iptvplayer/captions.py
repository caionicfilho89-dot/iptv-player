"""Legendas traduzidas ao vivo, como a tradução automática do YouTube.

Ouve o som que está saindo do computador (loopback do Windows), reconhece a fala com o
Whisper rodando no próprio PC (faster-whisper) e traduz o texto para o português.
As bibliotecas de IA são opcionais: sem elas o botão de legendas só explica o que falta.
"""
import json
import os
import queue
import re
import shutil
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import warnings
import zipfile
from collections import deque
from pathlib import Path

from PyQt6.QtCore import QPoint, QRect, QRectF, Qt, QThread, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPainterPath
from PyQt6.QtWidgets import QWidget

from .log import log
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
# o som capturado já vem com o volume do player aplicado: com o volume baixo a fala fica bem fraca,
# então só o silêncio digital (quase zero) conta como "nada tocando"
MIN_LEVEL = 0.0002
MAX_BACKLOG = 12.0           # se a IA ficar para trás, descarta o áudio mais antigo
JOIN_GAP = 3.0               # frase sem ponto final + próximo trecho em até 3 s = mesma frase
MAX_JOIN = 220               # limite de caracteres ao juntar pedaços da mesma frase
PARTIAL_EVERY = 1.0          # com placa de vídeo: prévia da frase a cada 1 s enquanto a pessoa fala
PARTIAL_MIN = 1.2            # segundos de fala antes da primeira prévia
RECHECK_EVERY = 6            # com o idioma fixado, confere de novo a cada 6 trechos (o canal pode mudar)

MODELS = {  # nome -> (rótulo, tamanho aproximado do download em MB)
    "base": ("Rápido (menos preciso)", 145),
    "small": ("Equilibrado (recomendado)", 484),
    "medium": ("Preciso (exige PC mais forte)", 1530),
    "large-v3-turbo": ("Máxima (placa de vídeo NVIDIA)", 1620),
}
GPU_MODEL = "large-v3-turbo"  # pesado demais para o processador; sem placa NVIDIA usa o "small"

# placa de vídeo NVIDIA: o CTranslate2 precisa do cuBLAS e do cuDNN, baixados do PyPI só quando pedidos
CUDA_DIR = CACHE / "cuda"
CUDA_WHEELS = (("nvidia-cublas-cu12", "12.9.2.10"), ("nvidia-cudnn-cu12", "9.27.0.42"))
CUDA_DOWNLOAD_MB = 1300
CUDA_DLLS = ("cublas64_12.dll", "cublasLt64_12.dll", "cudnn64_9.dll")
SOURCE_LANGS = {
    "auto": "Detectar automaticamente", "en": "Inglês", "es": "Espanhol", "fr": "Francês",
    "it": "Italiano", "de": "Alemão", "ru": "Russo", "uk": "Ucraniano", "ar": "Árabe", "tr": "Turco",
    "el": "Grego", "he": "Hebraico", "fa": "Persa", "hi": "Hindi", "ja": "Japonês", "ko": "Coreano",
    "zh": "Chinês", "id": "Indonésio", "vi": "Vietnamita", "th": "Tailandês", "nl": "Holandês",
    "sv": "Sueco", "pl": "Polonês", "ro": "Romeno", "pt": "Português",
}
TARGET = "pt"
# frases que o Whisper costuma "inventar" em música, vinheta ou silêncio (em vários idiomas)
HALLUCINATIONS = re.compile(
    r"(obrigad[oa] por assistir|legendas? (pela|por)|inscreva-se|thanks for watching|thank you for watching|"
    r"subtitles by|amara\.org|please subscribe|like and subscribe|subtítulos (realizados )?por|"
    r"gracias por ver|sous-titr|merci d'avoir regardé|untertitel (im auftrag|von|der)|"
    r"продолжение следует|субтитры|ご視聴|字幕|請不吝|请不吝|點贊|点赞|구독|시청해 주셔서|"
    r"www\.|\.com\b|♪|^\W*$)", re.I)
REPEAT_RE = re.compile(r"\b(\w+(?:\W+\w+){0,3})(?:\W+\1\b){3,}", re.I)  # "the the the the…": IA em loop
SENTENCE_END = re.compile(r"[.!?…。！？]\W*$")


def missing_deps():
    """Lista de pacotes que faltam para as legendas funcionarem."""
    missing = []
    for mod, pkg in (("faster_whisper", "faster-whisper"), ("soundcard", "soundcard"), ("numpy", "numpy")):
        try:
            __import__(mod)
        except Exception:  # noqa: BLE001 - DLL ausente também conta como "não instalado"
            missing.append(pkg)
    return missing


def gpu_available():
    """Há uma placa NVIDIA que o CTranslate2 consegue usar?"""
    try:
        import ctranslate2
        return ctranslate2.get_cuda_device_count() > 0
    except Exception:  # noqa: BLE001 - sem driver NVIDIA, DLL ausente…
        return False


def _cuda_dirs():
    dirs = [CUDA_DIR]
    try:  # rodando pelo código-fonte com os pacotes nvidia-* do pip
        import nvidia
        base = Path(list(nvidia.__path__)[0])
        dirs += [base / "cublas" / "bin", base / "cudnn" / "bin"]
    except Exception:  # noqa: BLE001
        pass
    return [d for d in dirs if d.is_dir()]


def cuda_ready():
    found = {f.lower() for d in _cuda_dirs() for f in os.listdir(d)}
    return all(dll.lower() in found for dll in CUDA_DLLS)


def _enable_cuda_dirs():
    for d in _cuda_dirs():
        os.add_dll_directory(str(d))
        if str(d) not in os.environ.get("PATH", ""):
            os.environ["PATH"] = str(d) + os.pathsep + os.environ.get("PATH", "")


def download_cuda(progress, cancelled):
    """Baixa o cuBLAS e o cuDNN (pacotes oficiais da NVIDIA no PyPI) e guarda só as DLLs."""
    CUDA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = CUDA_DIR / "download.whl"
    done = 0
    try:
        for name, ver in CUDA_WHEELS:
            with urllib.request.urlopen(f"https://pypi.org/pypi/{name}/{ver}/json", timeout=20) as r:
                info = json.loads(r.read().decode("utf-8"))
            url = next(u["url"] for u in info["urls"] if u["filename"].endswith("win_amd64.whl"))
            with urllib.request.urlopen(url, timeout=30) as r, open(tmp, "wb") as f:
                while chunk := r.read(1 << 20):
                    if cancelled():
                        raise RuntimeError("cancelado")
                    f.write(chunk)
                    done += len(chunk)
                    progress(done / 1e6)
            with zipfile.ZipFile(tmp) as z:
                for n in z.namelist():
                    if n.lower().endswith(".dll") and "/bin/" in n:
                        part = CUDA_DIR / (os.path.basename(n) + ".part")
                        with z.open(n) as src, open(part, "wb") as dst:
                            shutil.copyfileobj(src, dst)
                        os.replace(part, CUDA_DIR / os.path.basename(n))  # DLL pela metade nunca fica no lugar
    finally:
        tmp.unlink(missing_ok=True)


def is_garbage(text):
    """Texto que não deve virar legenda: frases inventadas pela IA ou palavras repetidas em loop."""
    return bool(HALLUCINATIONS.search(text) or REPEAT_RE.search(text))


_tr_cache = {}
_tr_down_until = 0.0  # sem internet: não tenta traduzir de novo por alguns segundos


BR_BILLION = {"ão": "bilhão", "ões": "bilhões"}
GOOGLE_CODES = {"zh": "zh-CN", "he": "iw", "jw": "jv", "yue": "zh-TW"}  # códigos que o Google escreve diferente


def translate(text, src, dst=TARGET):
    """Tradução pelo Google Tradutor (mesmo serviço da tradução automática do YouTube).

    Devolve "" quando não dá para traduzir agora (sem internet); aí a legenda mostra o original."""
    global _tr_down_until
    if time.monotonic() < _tr_down_until:
        return ""
    try:
        try:
            return _translate(text, GOOGLE_CODES.get(src, src), dst)
        except urllib.error.HTTPError:
            if not src or src == "auto":
                raise
            return _translate(text, "auto", dst)  # idioma não reconhecido pelo Google: deixa ele detectar
    except (OSError, ValueError, LookupError, TypeError) as e:  # sem conexão, tempo esgotado ou resposta estranha
        log.warning("Tradução indisponível por 20 s: %s", e)
        _tr_down_until = time.monotonic() + 20
        return ""


def _translate(text, src, dst):
    key = (text, src, dst)
    if key in _tr_cache:
        return _tr_cache[key]
    q = urllib.parse.urlencode({"client": "gtx", "sl": src or "auto", "tl": dst, "dt": "t", "q": text})
    req = urllib.request.Request("https://translate.googleapis.com/translate_a/single?" + q,
                                 headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=5) as r:
        data = json.loads(r.read().decode("utf-8"))
    out = "".join(part[0] for part in data[0] if part and part[0]).strip()
    if dst == "pt":  # o Google às vezes escreve números grandes como em Portugal
        out = re.sub(r"\b(\d+(?:[.,]\d+)?) mil milh(ão|ões)", lambda m: f"{m.group(1)} {BR_BILLION[m.group(2)]}", out)
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


# ---------------------------------------------------------------- tradução sem internet (NLLB-200)
TRANSLATORS = {"google": "Google Tradutor (internet)", "local": "No próprio PC, sem internet (NLLB)"}
NLLB_REPO = "JustFrederik/nllb-200-distilled-600M-ct2-int8"
NLLB_DIR = CACHE / "nllb"
NLLB_MB = 620
# códigos do Whisper -> códigos do NLLB (FLORES-200)
NLLB_CODES = {
    "en": "eng_Latn", "es": "spa_Latn", "fr": "fra_Latn", "it": "ita_Latn", "de": "deu_Latn", "ru": "rus_Cyrl",
    "uk": "ukr_Cyrl", "ar": "arb_Arab", "tr": "tur_Latn", "el": "ell_Grek", "he": "heb_Hebr", "fa": "pes_Arab",
    "hi": "hin_Deva", "ja": "jpn_Jpan", "ko": "kor_Hang", "zh": "zho_Hans", "id": "ind_Latn", "vi": "vie_Latn",
    "th": "tha_Thai", "nl": "nld_Latn", "sv": "swe_Latn", "pl": "pol_Latn", "ro": "ron_Latn", "sq": "als_Latn",
    "ca": "cat_Latn", "cs": "ces_Latn", "hu": "hun_Latn", "bg": "bul_Cyrl", "sr": "srp_Cyrl", "hr": "hrv_Latn",
    "bs": "bos_Latn", "sk": "slk_Latn", "sl": "slv_Latn", "da": "dan_Latn", "no": "nob_Latn", "nn": "nno_Latn",
    "fi": "fin_Latn", "et": "est_Latn", "lv": "lvs_Latn", "lt": "lit_Latn", "ta": "tam_Taml", "te": "tel_Telu",
    "bn": "ben_Beng", "ur": "urd_Arab", "ms": "zsm_Latn", "tl": "tgl_Latn", "sw": "swh_Latn", "mk": "mkd_Cyrl",
    "hy": "hye_Armn", "ka": "kat_Geor", "az": "azj_Latn", "kk": "kaz_Cyrl", "pa": "pan_Guru", "gu": "guj_Gujr",
    "mr": "mar_Deva", "ne": "npi_Deva", "si": "sin_Sinh", "km": "khm_Khmr", "my": "mya_Mymr", "am": "amh_Ethi",
    "so": "som_Latn", "ha": "hau_Latn", "yo": "yor_Latn", "af": "afr_Latn", "is": "isl_Latn", "ga": "gle_Latn",
    "cy": "cym_Latn", "eu": "eus_Latn", "gl": "glg_Latn", "be": "bel_Cyrl", "pt": "por_Latn",
}
SENTENCES = re.compile(r"(?<=[.!?…。！？])\s+")


def nllb_ready():
    return (NLLB_DIR / "model.bin").exists() and (NLLB_DIR / "tokenizer.json").exists()


class LocalTranslator:
    """Tradutor NLLB-200 rodando no PC (CTranslate2): funciona sem internet."""

    def __init__(self, device="cpu"):
        import ctranslate2
        from tokenizers import Tokenizer
        self.tok = Tokenizer.from_file(str(NLLB_DIR / "tokenizer.json"))
        compute = "int8_float16" if device == "cuda" else "int8"
        self.tr = ctranslate2.Translator(str(NLLB_DIR), device=device, compute_type=compute,
                                         intra_threads=max(2, min(4, (os.cpu_count() or 4) // 4)))

    @staticmethod
    def download(progress, cancelled):
        from huggingface_hub import snapshot_download
        result = {}

        def dl():
            try:
                snapshot_download(NLLB_REPO, local_dir=str(NLLB_DIR))
            except Exception as e:  # noqa: BLE001
                result["error"] = e
        t = threading.Thread(target=dl, daemon=True)
        t.start()
        while t.is_alive():
            if cancelled():
                raise RuntimeError("cancelado")
            progress(_dir_mb(NLLB_DIR))
            t.join(0.7)
        if "error" in result:
            raise result["error"]

    def translate(self, text, src):
        code = NLLB_CODES.get(src)
        if not code:
            return ""
        # uma frase por vez: com várias juntas o NLLB às vezes pula alguma
        parts = [p for p in SENTENCES.split(text) if p.strip()]
        batch = [[code] + self.tok.encode(p, add_special_tokens=False).tokens + ["</s>"] for p in parts]
        res = self.tr.translate_batch(batch, target_prefix=[["por_Latn"]] * len(batch), beam_size=4,
                                      max_decoding_length=256)
        out = [self.tok.decode([self.tok.token_to_id(t) for t in r.hypotheses[0][1:]], skip_special_tokens=True)
               for r in res]
        clean = []
        for o in out:
            # o NLLB às vezes inventa um diálogo ("- Como estás? - Bem."): fica só a primeira fala
            o = re.split(r"(?<=[.!?])\s+-\s+", re.sub(r"^\s*-\s*", "", o.strip()))[0]
            if o:
                clean.append(o)
        return re.sub(r"\s+([.,!?;:…])", r"\1", " ".join(clean))


class Segmenter:
    """Junta blocos de 100 ms de áudio em trechos de fala, cortando nas pausas."""

    def __init__(self):
        self.buf = []
        self.levels = deque(maxlen=150)   # ~15 s de histórico para achar o nível de ruído

    def clear(self):
        self.buf.clear()

    def feed(self, block):
        """Recebe um bloco; devolve um trecho pronto para reconhecer (ou None)."""
        import numpy as np
        buf, levels = self.buf, self.levels
        buf.append(block)
        levels.append(float(np.sqrt(np.mean(block * block))))
        if len(levels) > 10:
            floor, loud = np.percentile(levels, (20, 80))
            # fala contínua (telejornal, trilha de fundo) não tem silêncio de verdade: o limite não pode
            # passar do nível normal da fala, senão ela é tratada como silêncio e jogada fora
            thr = max(MIN_LEVEL, min(float(floor) * 2.2, float(loud) * 0.35))
        else:
            thr = MIN_LEVEL
        voiced = [lv > thr for lv in list(levels)[-len(buf):]]
        if not any(voiced):
            del buf[:-3]  # silêncio: guarda só um pedacinho para não cortar o início da fala
            return None
        dur = len(buf) * 0.1
        tail = 0
        for v in reversed(voiced):
            if v:
                break
            tail += 1
        if not ((dur >= MIN_SEG and tail >= PAUSE_BLOCKS) or dur >= MAX_SEG):
            return None
        if tail >= PAUSE_BLOCKS:
            cut = len(buf)
        else:  # corta no ponto mais baixo do último 1,5 s
            recent = list(levels)[-15:]
            cut = len(buf) - 15 + min(range(len(recent)), key=recent.__getitem__) + 1
            cut = max(1, min(len(buf), cut))
        seg = np.concatenate(buf[:cut])
        del buf[:cut]
        return seg


class CaptionWorker(QThread):
    """Captura o áudio, transcreve e traduz em segundo plano."""
    caption = pyqtSignal(str, str, str, bool)   # original, tradução, idioma, substitui a última linha
    partial = pyqtSignal(str, str)              # prévia da frase em andamento: original, tradução
    status = pyqtSignal(str)
    failed = pyqtSignal(str)
    ready = pyqtSignal()

    def __init__(self, model_name="small", source="auto", translator="google", parent=None):
        super().__init__(parent)
        self.model_name = model_name if model_name in MODELS else "small"
        self.source = source
        self._stop = threading.Event()
        self._active = threading.Event()
        self._reset = threading.Event()
        self._audio = queue.Queue()
        self._tr_queue = queue.Queue()
        self._gen = 0                 # muda a cada troca de canal: traduções pendentes são descartadas
        self._lang_votes = deque(maxlen=6)
        self._mismatch = 0
        self._count = 0
        self._last = None             # (texto, momento, idioma) da última legenda, para juntar frases
        self.locked_lang = None
        self.device = "cpu"
        self.translator = translator if translator in TRANSLATORS else "google"
        self._local = None            # LocalTranslator, carregado só quando precisa
        self._local_failed = False
        self.tap_mode = False         # True: o som vem direto do player (dublagem), não da caixa de som
        self._restart = threading.Event()

    # ---- chamados pela interface
    def set_active(self, on):
        (self._active.set if on else self._active.clear)()
        self._reset.set()

    def use_tap(self, on):
        """Liga/desliga a escuta direta do player (feed) no lugar da captura da caixa de som."""
        if on != self.tap_mode:
            self.tap_mode = on
            self._restart.set()

    def feed(self, block):
        """Bloco de 100 ms, 16 kHz mono, vindo do player (chamado pela tarefa do som)."""
        if self.tap_mode and self._active.is_set():
            self._audio.put(block)

    def reset(self):
        """Canal novo: esquece o áudio acumulado e volta a detectar o idioma."""
        self._reset.set()

    def stop(self):
        self._stop.set()
        self._active.set()  # acorda o laço para ele terminar
        self._tr_queue.put(None)

    # ---- thread
    def run(self):
        try:
            model = self._load_model()
        except Exception as e:  # noqa: BLE001
            if not self._stop.is_set():
                log.exception("Não foi possível carregar a IA de legendas")
                self.failed.emit(f"Não foi possível carregar a IA de legendas: {e}")
            return
        if self._stop.is_set():
            return
        # a tradução roda à parte: internet lenta não atrasa o reconhecimento da próxima frase
        threading.Thread(target=self._translate_loop, daemon=True).start()
        self.ready.emit()
        while not self._stop.is_set():
            self._active.wait()
            if self._stop.is_set():
                break
            try:
                self._listen(model)
            except Exception as e:  # noqa: BLE001
                log.exception("Erro ao ouvir o áudio")
                self.failed.emit(f"Erro ao ouvir o áudio: {e}")
                self._active.clear()

    def _load_model(self):
        from faster_whisper import WhisperModel

        name = self.model_name
        use_gpu = gpu_available() and (name == GPU_MODEL or cuda_ready())
        if use_gpu and not cuda_ready():
            try:
                download_cuda(lambda mb: self.status.emit(
                    f"Baixando o acelerador da placa de vídeo ({mb:.0f} de ~{CUDA_DOWNLOAD_MB} MB) — "
                    "só na primeira vez…"), self._stop.is_set)
            except Exception:  # noqa: BLE001 - sem internet ou cancelado: segue no processador
                if self._stop.is_set():
                    raise
                log.exception("Não foi possível baixar o acelerador da placa de vídeo")
                use_gpu = False
        if not use_gpu and name == GPU_MODEL:
            name = "small"
        if use_gpu:
            try:
                _enable_cuda_dirs()
                path = self._fetch(name)
                self.status.emit("Carregando a IA de legendas na placa de vídeo…")
                model = WhisperModel(str(path), device="cuda", compute_type="float16")
                import numpy as np
                list(model.transcribe(np.zeros(RATE, np.float32))[0])  # testa as DLLs da placa de verdade
                self.device = "cuda"
                log.info("Legendas: modelo %s na placa de vídeo", name)
                return model
            except Exception:  # noqa: BLE001 - driver antigo, pouca memória de vídeo…: usa o processador
                if self._stop.is_set():
                    raise
                log.exception("Placa de vídeo falhou; usando o processador")
                if name == GPU_MODEL:
                    name = "small"
        path = self._fetch(name)
        self.status.emit("Carregando a IA de legendas…")
        threads = max(2, min(8, (os.cpu_count() or 4) // 2))
        log.info("Legendas: modelo %s no processador (%d threads)", name, threads)
        return WhisperModel(str(path), device="cpu", compute_type="int8", cpu_threads=threads)

    def _fetch(self, name):
        """Pasta do modelo, baixando na primeira vez."""
        from faster_whisper.utils import download_model

        MODEL_DIR.mkdir(parents=True, exist_ok=True)
        _label, size_mb = MODELS[name]
        target = MODEL_DIR / name
        if not (target / "model.bin").exists():
            result = {}

            def dl():
                try:
                    result["path"] = download_model(name, output_dir=str(target))
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
        return target

    def _listen(self, model):
        self._restart.clear()
        self._drain()
        capture_stop = threading.Event()
        th = None
        if self.tap_mode:
            log.info("Legendas: ouvindo o som direto do player")
        else:
            th = self._start_capture(capture_stop)
        self.status.emit("")
        seg = Segmenter()
        last_partial = 0.0
        try:
            while self._active.is_set() and not self._stop.is_set():
                if self._restart.is_set():
                    break  # mudou de onde vem o som: recomeça a escuta
                if self._reset.is_set():
                    self._reset.clear()
                    seg.clear()
                    self._forget()
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
                    seg.clear()
                    self._last = None
                    continue
                audio = seg.feed(block)
                if audio is not None:
                    self._process(model, audio)
                    last_partial = time.monotonic()
                elif self.device == "cuda" and time.monotonic() - last_partial >= PARTIAL_EVERY:
                    last_partial = time.monotonic()
                    self._preview(model, seg)
        finally:
            capture_stop.set()
            if th:
                th.join(1.5)
            self._drain()

    def _start_capture(self, capture_stop):
        """Captura o som que sai da caixa de som padrão (loopback do Windows)."""
        import numpy as np
        _com_init()
        import soundcard as sc

        speaker = sc.default_speaker()
        mic = sc.get_microphone(id=str(speaker.name), include_loopback=True)
        log.info("Legendas: ouvindo o som de \"%s\"", speaker.name)

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
        return th

    def _forget(self):
        self._gen += 1
        self._lang_votes.clear()
        self._mismatch = self._count = 0
        self._last = None
        self.locked_lang = None

    def _drain(self):
        try:
            while True:
                self._audio.get_nowait()
        except queue.Empty:
            pass

    def _process(self, model, audio):
        peak = float(abs(audio).max())
        if peak < MIN_LEVEL:
            return
        audio = audio * min(500.0, 0.9 / peak)  # o volume do player não pode atrapalhar o reconhecimento
        self._count += 1
        if self.source == "auto" and self.locked_lang and self._count % RECHECK_EVERY == 0:
            self._recheck_lang(model, audio)
        lang = self.source if self.source != "auto" else self.locked_lang
        segments, info = model.transcribe(
            audio, language=lang, beam_size=5 if self.device == "cuda" else 2, vad_filter=True, condition_on_previous_text=False,
            vad_parameters={"min_silence_duration_ms": 300})
        parts = [s.text.strip() for s in segments
                 if s.no_speech_prob < 0.6 and s.avg_logprob > -1.1 and s.compression_ratio < 2.4]
        text = re.sub(r"\s+", " ", " ".join(parts)).strip()
        if not text or is_garbage(text) or self._reset.is_set():
            return
        detected = lang or info.language
        if self.source == "auto" and not self.locked_lang and info.language_probability > 0.6:
            self._lang_votes.append(info.language)
            best = max(set(self._lang_votes), key=self._lang_votes.count)
            if self._lang_votes.count(best) >= 3:
                self.locked_lang = best  # fixa o idioma do canal: detecção fica mais estável
                log.info("Legendas: idioma do canal = %s", best)
        self._emit(text, detected)

    def _emit(self, text, lang):
        now = time.monotonic()
        prev = self._last
        if prev and prev[0].lower().endswith(text.lower()) and now - prev[1] < 2 * JOIN_GAP:
            return  # a IA repetiu o mesmo trecho
        # continua a frase anterior se ela não terminou, ou se o Whisper pôs um ponto no meio dela
        # (o trecho novo começa com minúscula: "…na capital." + "que custará…")
        join = bool(prev and prev[2] == lang and now - prev[1] < JOIN_GAP and len(prev[0]) + len(text) < MAX_JOIN
                    and (not SENTENCE_END.search(prev[0]) or text[:1].islower()))
        full = f"{prev[0].rstrip('.') if text[:1].islower() else prev[0]} {text}" if join else text
        self._last = (full, now, lang)
        # frase cortada no meio: traduz a frase inteira (fica bem melhor) e troca a linha anterior
        self._tr_queue.put((self._gen, full, lang, join))

    def _preview(self, model, seg):
        """Prévia rápida do trecho que ainda está sendo falado (só com o idioma já conhecido)."""
        import numpy as np
        lang = self.source if self.source != "auto" else self.locked_lang
        if not lang or len(seg.buf) * 0.1 < PARTIAL_MIN or self._tr_queue.qsize() > 1:
            return
        audio = np.concatenate(seg.buf)
        peak = float(abs(audio).max())
        if peak < MIN_LEVEL:
            return
        segments, _ = model.transcribe(audio * min(500.0, 0.9 / peak), language=lang, beam_size=1,
                                       vad_filter=False, condition_on_previous_text=False,
                                       without_timestamps=True)
        text = re.sub(r"\s+", " ", " ".join(s.text.strip() for s in segments
                                             if s.no_speech_prob < 0.6 and s.avg_logprob > -1.0)).strip()
        if text and not is_garbage(text) and not self._reset.is_set():
            self._tr_queue.put((self._gen, text, lang, None))  # None = prévia

    def _recheck_lang(self, model, audio):
        """O idioma fixado ainda vale? (troca de programa, comercial em outra língua…)"""
        try:
            lang, prob, _ = model.detect_language(audio)
        except Exception:  # noqa: BLE001 - versão antiga do faster-whisper
            return
        if lang != self.locked_lang and prob > 0.8:
            self._mismatch += 1
            if self._mismatch >= 2:
                log.info("Legendas: idioma mudou de %s para %s", self.locked_lang, lang)
                self.locked_lang = None
                self._lang_votes.clear()
                self._lang_votes.append(lang)
                self._mismatch = 0
        else:
            self._mismatch = 0

    def _translate_local(self, text, lang):
        if self._local is None and not self._local_failed:
            try:
                if not nllb_ready():
                    LocalTranslator.download(
                        lambda mb: self.status.emit(f"Baixando o tradutor sem internet ({mb:.0f} de ~{NLLB_MB} MB)"
                                                    " — só na primeira vez…"), self._stop.is_set)
                    self.status.emit("")
                self._local = LocalTranslator(self.device)
                log.info("Tradução: NLLB no PC (%s)", self.device)
            except Exception:  # noqa: BLE001
                log.exception("Tradução sem internet indisponível")
                self._local_failed = True
        if not self._local:
            return translate(text, lang)
        try:
            return self._local.translate(text, lang)
        except Exception:  # noqa: BLE001
            log.exception("Tradução sem internet falhou")
            return ""

    def _translate_loop(self):
        while True:
            item = self._tr_queue.get()
            if item is None or self._stop.is_set():
                return
            gen, text, lang, replace = item
            if gen != self._gen:
                continue  # o canal já mudou
            if replace is None and not self._tr_queue.empty():
                continue  # prévia velha: já chegou coisa mais nova
            if lang == TARGET:
                translated = text
            elif self.translator == "local":
                translated = self._translate_local(text, lang)
            else:
                translated = translate(text, lang)
                if not translated and nllb_ready():  # sem internet: usa o tradutor do PC, se já baixado
                    translated = self._translate_local(text, lang)
            if gen != self._gen or self._stop.is_set():
                continue
            if replace is None:
                self.partial.emit(text, translated)
            else:
                self.caption.emit(text, translated, lang, replace)


class SubtitleOverlay(QWidget):
    """Legenda estilo YouTube sobre o vídeo (janela própria, pois o vídeo do VLC é nativo)."""
    LINE_TTL = 7.0

    def __init__(self, owner):
        super().__init__(owner, self._flags())
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.lines = deque(maxlen=2)   # (texto, original, expira_em)
        self.preview = ""              # frase em andamento (mais clara, vira legenda na pausa)
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

    def set_preview(self, text):
        self.preview = text
        self._relayout()

    def add(self, text, original="", replace=False):
        self.notice = ""
        self.preview = ""
        if replace and self.lines:
            self.lines.pop()  # mesma frase, agora completa
        ttl = min(10.0, self.LINE_TTL + len(text) / 40)  # frase longa fica mais tempo na tela
        self.lines.append((text, original if original != text else "", time.monotonic() + ttl))
        self._relayout()

    def set_notice(self, text):
        self.notice = text
        self._relayout()

    def clear(self):
        self.lines.clear()
        self.preview = ""
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
        if self.preview:
            items.append((self.preview, "preview"))
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
            fm = QFontMetrics(small if is_small and is_small != "preview" else main)
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
            preview = is_small == "preview"
            p.fillPath(path, QColor(8, 8, 10, 150 if is_small and not preview else 185))
            p.setFont(small if is_small and not preview else main)
            p.setPen(QColor(205, 210, 222) if is_small else QColor(255, 255, 255))
            p.drawText(box.adjusted(12, 4, -12, -4), int(Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap),
                       text)


def video_rect_global(widget):
    return QRect(widget.mapToGlobal(QPoint(0, 0)), widget.size())
