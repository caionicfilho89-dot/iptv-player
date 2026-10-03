"""Pausar e voltar a TV ao vivo (timeshift).

Os canais HLS (.m3u8) ao vivo só guardam os últimos ~30 s: se você pausa, o VLC volta direto para o
"agora". Aqui o programa baixa ele mesmo os pedaços do canal enquanto você assiste, guarda os últimos
minutos no disco e entrega ao VLC uma lista local (servida em 127.0.0.1), que deixa pausar, voltar e
avançar. Os pedaços são baixados uma vez só: o consumo de internet é o mesmo de assistir direto.
Se o canal não for compatível (não-HLS, VOD, áudio separado…), o programa toca direto, como antes.
"""
import re
import shutil
import threading
from concurrent.futures import ThreadPoolExecutor
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from PyQt6.QtCore import QObject, pyqtSignal

from .log import log
from .net import USER_AGENT
from .paths import CACHE

TS_DIR = CACHE / "timeshift"
START_SEGMENTS = 2           # começa pelos últimos 2 pedaços (início rápido, perto do ao vivo)
START_WAIT_MS = 6000         # servidor lento: se o buffer não ficar pronto nisso, toca direto
READY_SEGMENTS = 2           # com menos o VLC fica esperando a lista crescer
MAX_HEIGHT = 1080            # qualidade máxima escolhida entre as opções do canal
# sem isto o VLC "puxa" a lista ao vivo para perto do fim ao despausar e a pausa se perde
LIVE_DELAY_OPT = ":adaptive-livedelay=21600000"
ATTR_RE = re.compile(r'([A-Z0-9-]+)=("[^"]*"|[^,]*)')


def _attrs(line):
    return {k: v.strip('"') for k, v in ATTR_RE.findall(line.split(":", 1)[1])}


def supports(url):
    return ".m3u8" in url.lower().split("?")[0] or "m3u8" in url.lower()


class Unsupported(Exception):
    pass


class Session(threading.Thread):
    """Baixa um canal continuamente e mantém a lista local."""

    def __init__(self, sid, url, opts, max_seconds, owner):
        super().__init__(name=f"timeshift-{sid}", daemon=True)
        self.sid, self.url, self.max_seconds, self.owner = sid, url, max_seconds, owner
        self.headers = {"User-Agent": USER_AGENT.decode()}
        for o in opts:
            if o.startswith(":http-user-agent="):
                self.headers["User-Agent"] = o.split("=", 1)[1]
            elif o.startswith(":http-referrer="):
                self.headers["Referer"] = o.split("=", 1)[1]
        self.dir = TS_DIR / str(sid)
        self.lock = threading.Lock()
        self.segments = []
        self.end = 0.0               # fim do que foi guardado, em segundos desde que o canal abriu
        self._key = self._map = None
        self.target = 6
        self.version = 3
        self.stop_ev = threading.Event()
        self.ready = False
        self._gap = False            # um pedaço se perdeu: o próximo leva a marca de descontinuidade
        self.buffered = 0.0          # segundos guardados

    # ---- rede
    def _get(self, url, binary=False):
        req = urllib.request.Request(url, headers=self.headers)
        with urllib.request.urlopen(req, timeout=15) as r:
            data = r.read()
            return (data if binary else data.decode("utf-8", "replace")), r.geturl()

    def _media_playlist(self):
        text, base = self._get(self.url)
        if "#EXT-X-STREAM-INF" not in text:
            return base
        if re.search(r"#EXT-X-MEDIA:.*TYPE=AUDIO.*URI=", text):
            raise Unsupported("áudio em lista separada")
        best, best_key = None, None
        lines = text.splitlines()
        for i, line in enumerate(lines):
            if line.startswith("#EXT-X-STREAM-INF"):
                a = _attrs(line)
                h = int(a.get("RESOLUTION", "0x0").split("x")[-1] or 0)
                bw = int(a.get("BANDWIDTH", "0") or 0)
                uri = next((x.strip() for x in lines[i + 1:] if x.strip() and not x.startswith("#")), None)
                key = (h <= MAX_HEIGHT, h if h <= MAX_HEIGHT else -h, bw)
                if uri and (best_key is None or key > best_key):
                    best, best_key = urllib.parse.urljoin(base, uri), key
        if not best:
            raise Unsupported("lista sem opções de vídeo")
        return best

    # ---- tarefa
    def run(self):
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            media_url = self._media_playlist()
            last_seq, errors = None, 0
            while not self.stop_ev.is_set():
                try:
                    text, base = self._get(media_url)
                    errors = 0
                except OSError as e:
                    errors += 1
                    if errors >= 4:
                        raise
                    log.warning("Timeshift: falha ao ler a lista (%s), tentando de novo", e)
                    self.stop_ev.wait(2)
                    continue
                if "#EXT-X-ENDLIST" in text and not self.segments:
                    raise Unsupported("não é ao vivo")
                if "#EXT-X-BYTERANGE" in text:
                    raise Unsupported("pedaços por intervalo de bytes")
                new = self._parse(text, base, last_seq)
                if last_seq is None and new:
                    # primeiros pedaços em paralelo: o canal abre quase tão rápido quanto direto
                    with ThreadPoolExecutor(len(new)) as ex:
                        datas = list(ex.map(self._fetch_segment, [s[2] for s in new]))
                else:
                    datas = [None] * len(new)
                for (seq, dur, uri, tags), data in zip(new, datas):
                    if self.stop_ev.is_set():
                        return
                    self._download(seq, dur, uri, tags, data)
                    last_seq = seq
                self.stop_ev.wait(max(1.0, self.target / 2))
        except Unsupported as e:
            log.info("Timeshift indisponível neste canal: %s", e)
            self.owner._emit_failed(self.sid)
        except Exception as e:  # noqa: BLE001
            log.warning("Timeshift parou: %s", e)
            self.owner._emit_failed(self.sid)

    def _parse(self, text, base, last_seq):
        """Pedaços novos da lista do canal: (seq, duração, url absoluta, tags)."""
        seq, dur, tags, out = 0, None, [], []
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            if line.startswith("#EXT-X-TARGETDURATION"):
                self.target = max(1, int(float(line.split(":")[1])))
            elif line.startswith("#EXT-X-VERSION"):
                self.version = max(self.version, int(line.split(":")[1]))
            elif line.startswith("#EXT-X-MEDIA-SEQUENCE"):
                seq = int(line.split(":")[1])
            elif line.startswith("#EXTINF"):
                dur = float(line.split(":")[1].split(",")[0])
            elif line.startswith(("#EXT-X-KEY", "#EXT-X-MAP")):
                # chave/cabeçalho continuam no servidor do canal: só torna o endereço absoluto
                tags.append(re.sub(r'URI="([^"]*)"',
                                   lambda m: f'URI="{urllib.parse.urljoin(base, m.group(1))}"', line))
            elif line.startswith(("#EXT-X-DISCONTINUITY", "#EXT-X-PROGRAM-DATE-TIME")) \
                    and not line.startswith("#EXT-X-DISCONTINUITY-SEQUENCE"):
                tags.append(line)
            elif not line.startswith("#"):
                if dur is not None:
                    out.append((seq, dur, urllib.parse.urljoin(base, line), tags))
                seq += 1
                dur, tags = None, []
        if last_seq is None:
            return out[-START_SEGMENTS:]
        return [s for s in out if s[0] > last_seq]

    def _fetch_segment(self, uri):
        try:
            return self._get(uri, binary=True)[0]
        except OSError:
            return None  # _download tenta de novo

    def _download(self, seq, dur, uri, tags, data=None):
        ext = ".mp4" if any(t.startswith("#EXT-X-MAP") for t in tags) or uri.split("?")[0].endswith(
            (".m4s", ".mp4")) else ".ts"
        path = self.dir / f"{seq}{ext}"
        for attempt in range(3):
            try:
                if data is None:
                    data, _ = self._get(uri, binary=True)
                if self.stop_ev.is_set():
                    return  # canal trocado: a pasta já foi apagada
                path.write_bytes(data)
                break
            except OSError as e:
                data = None
                if self.stop_ev.is_set():
                    return
                if attempt == 2:
                    log.warning("Timeshift: pedaço %d perdido (%s)", seq, e)
                    self._gap = True
                    return
                self.stop_ev.wait(1)
        if self._gap and "#EXT-X-DISCONTINUITY" not in tags:
            tags = ["#EXT-X-DISCONTINUITY"] + tags
        self._gap = False
        for t in tags:  # chave/cabeçalho valem para os pedaços seguintes também
            if t.startswith("#EXT-X-KEY"):
                self._key = t
            elif t.startswith("#EXT-X-MAP"):
                self._map = t
        with self.lock:
            # (seq, duração, arquivo, tags, início em segundos desde que o canal abriu, chave, cabeçalho)
            self.segments.append((seq, dur, path, tags, self.end, self._key, self._map))
            self.end += dur
            self.buffered += dur
            # guarda só os últimos minutos configurados: apaga os mais antigos
            while self.buffered > self.max_seconds and len(self.segments) > START_SEGMENTS:
                old = self.segments.pop(0)
                self.buffered -= old[1]
                old[2].unlink(missing_ok=True)
            count = len(self.segments)
        if not self.ready and count >= READY_SEGMENTS:
            self.ready = True
            self.owner._emit_ready(self.sid)

    def span(self):
        """(início, fim) do que está guardado, em segundos desde que o canal abriu."""
        with self.lock:
            if not self.segments:
                return 0.0, 0.0
            return self.segments[0][4], self.end

    def segment_at(self, pos):
        """(seq, início) do pedaço que contém o instante pos."""
        with self.lock:
            segs = list(self.segments)
        if not segs:
            return None, 0.0
        pick = segs[0]
        for s in segs:
            if s[4] <= pos:
                pick = s
            else:
                break
        return pick[0], pick[4]

    def playlist(self, from_seq=None):
        """Lista para o VLC, começando no pedaço from_seq (o VLC sempre toca do início da lista)."""
        with self.lock:
            segs = [s for s in self.segments if from_seq is None or s[0] >= from_seq] or self.segments[-1:]
        target = max([self.target] + [int(s[1] + 0.999) for s in segs])
        lines = ["#EXTM3U", f"#EXT-X-VERSION:{self.version}", f"#EXT-X-TARGETDURATION:{target}",
                 f"#EXT-X-MEDIA-SEQUENCE:{segs[0][0] if segs else 0}"]
        for i, (seq, dur, path, tags, _start, key, cmap) in enumerate(segs):
            if i == 0:  # começando no meio: repete a chave/cabeçalho que valiam ali
                tags = [t for t in (key, cmap) if t and t not in tags] + tags
            lines += tags
            lines += [f"#EXTINF:{dur:.3f},", f"/{self.sid}/{path.name}"]
        return "\n".join(lines) + "\n"

    def export(self, start, end, dest):
        """Junta os pedaços entre start e end (segundos desde que o canal abriu) num arquivo de vídeo.

        Pedaços .ts emendados formam um .ts válido; nos fMP4 o cabeçalho (EXT-X-MAP) vai no começo."""
        with self.lock:
            segs = [s for s in self.segments if s[4] + s[1] > start and s[4] < end]
        if not segs:
            raise ValueError("nada guardado nesse intervalo")
        if any(s[5] and "METHOD=NONE" not in s[5] for s in segs):
            raise ValueError("este canal é criptografado e não pode ser salvo")
        with open(dest, "wb") as out:
            cmap = segs[0][6]
            if cmap:
                uri = re.search(r'URI="([^"]*)"', cmap).group(1)
                out.write(self._get(uri, binary=True)[0])
            for s in segs:
                if s[2].exists():
                    out.write(s[2].read_bytes())
        return sum(s[1] for s in segs)

    def file(self, name):
        p = self.dir / name
        return p if p.parent == self.dir and p.exists() else None

    def close(self):
        self.stop_ev.set()
        shutil.rmtree(self.dir, ignore_errors=True)


class Timeshift(QObject):
    """Servidor local + sessão do canal atual."""
    ready = pyqtSignal(int, str)     # sessão, endereço local para o VLC
    failed = pyqtSignal(int)
    exported = pyqtSignal(str, str)  # arquivo salvo, mensagem de erro ("" = deu certo)

    def __init__(self, max_minutes=60, parent=None):
        super().__init__(parent)
        self.max_seconds = max_minutes * 60
        self.session = None
        self._sid = 0
        shutil.rmtree(TS_DIR, ignore_errors=True)  # sobras de uma sessão anterior
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_a):
                pass

            def do_GET(self):
                s = owner.session
                path, _, query = self.path.partition("?")
                parts = path.strip("/").split("/")
                if not s or len(parts) != 2 or parts[0] != str(s.sid):
                    return self.send_error(404)
                if parts[1] == "live.m3u8":
                    frm = urllib.parse.parse_qs(query).get("from", [None])[0]
                    body = s.playlist(int(frm) if frm and frm.isdigit() else None).encode()
                    ctype = "application/vnd.apple.mpegurl"
                else:
                    f = s.file(parts[1])
                    if not f:
                        return self.send_error(404)
                    body, ctype = f.read_bytes(), "video/mp2t" if f.suffix == ".ts" else "video/mp4"
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()
                try:
                    self.wfile.write(body)
                except OSError:
                    pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, name="timeshift-http", daemon=True).start()

    def open(self, url, opts):
        """Começa a guardar um canal; devolve o id da sessão (o sinal ready/failed traz o mesmo id)."""
        self.close()
        self._sid += 1
        self.session = Session(self._sid, url, opts, self.max_seconds, self)
        self.session.start()
        return self._sid

    def close(self):
        if self.session:
            self.session.close()
            self.session = None

    def local_url(self, sid, from_seq=None):
        url = f"http://127.0.0.1:{self.port}/{sid}/live.m3u8"
        return url if from_seq is None else f"{url}?from={from_seq}"

    def buffered(self):
        s = self.session
        return s.buffered if s else 0.0

    def span(self):
        s = self.session
        return s.span() if s else (0.0, 0.0)

    def target(self):
        s = self.session
        return s.target if s else 6

    def export(self, start, end, dest):
        """Salva o trecho em segundo plano; avisa pelo sinal exported."""
        s = self.session
        if not s:
            return self.exported.emit("", "o canal não está sendo guardado")

        def work():
            try:
                secs = s.export(start, end, dest)
                log.info("Timeshift: salvos %.0f s em %s", secs, dest)
                self.exported.emit(str(dest), "")
            except Exception as e:  # noqa: BLE001
                log.warning("Timeshift: não foi possível salvar o trecho: %s", e)
                self.exported.emit("", str(e))
        threading.Thread(target=work, name="timeshift-salvar", daemon=True).start()

    def url_at(self, pos):
        """(endereço local que começa no pedaço com o instante pos, início desse pedaço)."""
        s = self.session
        if not s:
            return None, 0.0
        seq, start = s.segment_at(pos)
        return (self.local_url(s.sid, seq), start) if seq is not None else (None, 0.0)

    def shutdown(self):
        self.close()
        self.server.shutdown()

    # chamados pela tarefa da sessão
    def _emit_ready(self, sid):
        self.ready.emit(sid, self.local_url(sid))

    def _emit_failed(self, sid):
        self.failed.emit(sid)
