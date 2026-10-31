"""Busca pelo sentido: acha filmes e séries por uma descrição ("o cara que acorda sempre no mesmo dia") e os
parecidos com um título ("Mais como este").

Cada sinopse vira um vetor (multilingual-e5-small, ONNX int8, ~120 MB, roda no processador) e a busca compara
o vetor da frase com os das sinopses. Os vetores ficam em .cache/sentido.npz e só as sinopses novas são
calculadas de novo; a primeira vez leva alguns minutos, em segundo plano."""
import hashlib
import re
import threading
import time

from PyQt6.QtCore import QObject, pyqtSignal

from .catalog import catalog_fold
from .log import log
from .paths import CACHE

REPO = "Xenova/multilingual-e5-small"
MODEL_DIR = CACHE / "e5"
INDEX_FILE = CACHE / "sentido.npz"
BATCH = 32
MIN_SCORE = 0.80     # abaixo disso a sinopse não tem a ver com a frase (o e5 dá ~0,75 para textos sem relação)
WORD_BONUS = 0.012   # cada palavra da frase que também está na sinopse sobe o título (sentido + palavras)
CANDIDATES = 400
STOP = set("que com uma para por dos das nos nas num numa sobre sempre ele ela eles elas seu sua mesmo muito mais "
           "quando onde como cara filme filmes serie series historia aquele aquela".split())


def _key_of(text):
    return hashlib.sha1(text.encode("utf8")).hexdigest()[:16]


class SemanticIndex(QObject):
    progress = pyqtSignal(int, int)   # sinopses prontas, total
    ready = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._lock = threading.Lock()
        self._sess = self._tok = None
        self.keys = []                # hash da sinopse, na ordem dos vetores
        self.vecs = None              # numpy [n, 384] float32, normalizados
        self.titles = {}              # hash da sinopse -> [("m", id do filme) ou ("s", série)]
        self.texts = {}               # hash da sinopse -> sinopse sem acento (para somar as palavras da busca)
        self.building = False
        self.done = self.total = 0

    @property
    def available(self):
        return self.vecs is not None and len(self.keys) > 0

    # ---- modelo
    def _load_model(self):
        if self._sess is not None:
            return
        import onnxruntime as ort
        from huggingface_hub import hf_hub_download
        from tokenizers import Tokenizer
        MODEL_DIR.mkdir(parents=True, exist_ok=True)
        model = hf_hub_download(REPO, "onnx/model_int8.onnx", local_dir=MODEL_DIR)
        tok = Tokenizer.from_file(hf_hub_download(REPO, "tokenizer.json", local_dir=MODEL_DIR))
        tok.enable_truncation(256)
        tok.enable_padding()
        so = ort.SessionOptions()
        so.intra_op_num_threads = 4   # deixa processador sobrando para o vídeo
        self._sess = ort.InferenceSession(model, so, providers=["CPUExecutionProvider"])
        self._inputs = {i.name for i in self._sess.get_inputs()}
        self._tok = tok

    def _embed(self, texts):
        import numpy as np
        enc = self._tok.encode_batch(texts)
        ids = np.array([e.ids for e in enc], np.int64)
        mask = np.array([e.attention_mask for e in enc], np.int64)
        feed = {"input_ids": ids, "attention_mask": mask}
        if "token_type_ids" in self._inputs:
            feed["token_type_ids"] = np.zeros_like(ids)
        out = self._sess.run(None, feed)[0]
        v = (out * mask[..., None]).sum(1) / mask.sum(1, keepdims=True)
        return (v / np.linalg.norm(v, axis=1, keepdims=True)).astype("float32")

    # ---- índice
    def build(self, movies, series):
        """movies: {id: info}, series: {nome: info}. Calcula em segundo plano só o que falta."""
        if self.building:
            return
        for _ in range(5):   # o crawler do elenco pode estar adicionando filmes nesse instante
            try:
                movies, series = dict(movies), dict(series)
                break
            except RuntimeError:
                time.sleep(0.05)
        else:
            return
        self.building = True
        threading.Thread(target=self._build, args=(movies, series), name="busca-sentido", daemon=True).start()

    def _build(self, movies, series):
        import numpy as np
        try:
            texts, titles = {}, {}
            for kind, items in (("m", movies), ("s", series)):
                for ident, info in items.items():
                    plot = (info.get("plot") or "").strip()
                    if len(plot) < 40:
                        continue
                    text = plot[:700]
                    k = _key_of(text)
                    texts[k] = text
                    titles.setdefault(k, []).append((kind, ident))
            keys, vecs = [], None
            try:
                data = np.load(INDEX_FILE)
                keys, vecs = list(data["keys"]), data["vecs"].astype("float32")
            except (OSError, ValueError, KeyError):
                pass
            have = set(keys)
            todo = [k for k in texts if k not in have]
            self.total, self.done = len(texts), len(texts) - len(todo)
            with self._lock:
                self.titles = titles
                self.texts = {k: " " + catalog_fold(t) + " " for k, t in texts.items()}
                if vecs is not None:
                    self.keys, self.vecs = keys, vecs
            if todo:
                log.info("Busca pelo sentido: %d sinopses novas para calcular", len(todo))
                self._load_model()
                new_keys, chunks, last_save = [], [], time.time()
                for i in range(0, len(todo), BATCH):
                    part = todo[i:i + BATCH]
                    chunks.append(self._embed(["passage: " + texts[k] for k in part]))
                    new_keys += part
                    self.done += len(part)
                    self.progress.emit(self.done, self.total)
                    if time.time() - last_save > 60 or i + BATCH >= len(todo):
                        with self._lock:
                            add = np.concatenate(chunks)
                            self.keys = self.keys + new_keys
                            self.vecs = add if self.vecs is None else np.concatenate([self.vecs, add])
                            new_keys, chunks = [], []
                            self._save()
                        last_save = time.time()
                log.info("Busca pelo sentido: pronta (%d sinopses)", len(self.keys))
            self.ready.emit()
        except Exception:  # noqa: BLE001
            log.exception("Busca pelo sentido")
        finally:
            self.building = False

    def _save(self):
        import numpy as np
        tmp = INDEX_FILE.with_suffix(".tmp.npz")
        np.savez(tmp, keys=np.array(self.keys), vecs=self.vecs.astype("float16"))
        tmp.replace(INDEX_FILE)

    # ---- buscas
    def _rank(self, qv, limit, skip=(), words=()):
        import numpy as np
        with self._lock:
            if self.vecs is None:
                return []
            scores = self.vecs @ qv
            keys, titles, texts = self.keys, self.titles, self.texts
        order = np.argsort(-scores)
        if words:   # entre os mais parecidos pelo sentido, sobe quem também tem as palavras da frase
            rx = [re.compile(r"(?<![a-z])" + w) for w in words]
            cand = [i for i in order[:CANDIDATES] if scores[i] >= MIN_SCORE]
            bonus = {i: WORD_BONUS * sum(1 for r in rx if r.search(texts.get(keys[i], ""))) for i in cand}
            order = sorted(cand, key=lambda i: -(scores[i] + bonus[i]))
        out = []
        for i in order[:limit * 3]:
            if scores[i] < MIN_SCORE:
                break
            k = keys[i]
            if k in skip:
                continue
            out += titles.get(k, [])
            if len(out) >= limit:
                break
        return out

    def search(self, query, limit=60):
        """[("m", id) ou ("s", série)] mais parecidos com a descrição."""
        if not self.available or len(query.split()) < 2:
            return []
        try:
            self._load_model()
            qv = self._embed(["query: " + query.strip()])[0]
        except Exception:  # noqa: BLE001
            log.exception("Busca pelo sentido")
            return []
        words = sorted({w[:5] for w in re.findall(r"[a-z]{4,}", catalog_fold(query)) if w not in STOP})
        return self._rank(qv, limit, words=words)

    def similar(self, plot, limit=30):
        """Títulos com a história parecida com esta sinopse (sem ela mesma)."""
        if not self.available or not plot:
            return []
        k = _key_of(plot.strip()[:700])
        with self._lock:
            try:
                idx = self.keys.index(k)
            except ValueError:
                return []
            qv = self.vecs[idx]
        return self._rank(qv, limit, skip={k})
