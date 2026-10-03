"""Pausar e voltar a TV ao vivo (timeshift).

Os canais HLS (.m3u8) ao vivo só guardam os últimos ~30 s: se você pausa, o VLC volta direto para o
"agora". Aqui o programa baixa ele mesmo os pedaços do canal enquanto você assiste, guarda os últimos
minutos no disco e entrega ao VLC uma lista local (servida em 127.0.0.1), que deixa pausar, voltar e
avançar. Os pedaços são baixados uma vez só: o consumo de internet é o mesmo de assistir direto.
Canais com o áudio numa lista separada também funcionam: vídeo e áudio são guardados lado a lado
(os pedaços dos dois têm a mesma numeração). Se o canal não for compatível (não-HLS, VOD…), o
programa toca direto, como antes.
"""
import re
import shutil
import threading
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from PyQt6.QtCore import QObject, pyqtSignal

from .log import log
from .net import USER_AGENT
from .paths import CACHE

TS_DIR = CACHE / "timeshift"
START_SEGMENTS = 4           # começa pelos últimos 4 pedaços, baixados em paralelo (folga para o VLC)
START_WAIT_MS = 6000         # servidor lento: se o buffer não ficar pronto nisso, toca direto
READY_SEGMENTS = 3           # com menos o VLC alcança o fim do buffer e engasga
MAX_HEIGHT = 1080            # qualidade máxima escolhida entre as opções do canal
# sem isto o VLC "puxa" a lista ao vivo para perto do fim ao despausar e a pausa se perde
LIVE_DELAY_OPT = ":adaptive-livedelay=21600000"
ATTR_RE = re.compile(r'([A-Z0-9-]+)=("[^"]*"|[^,]*)')
M3U8 = "application/vnd.apple.mpegurl"


def _attrs(line):
    return {k: v.strip('"') for k, v in ATTR_RE.findall(line.split(":", 1)[1])}


def supports(url):
    return "m3u8" in url.lower()


class Unsupported(Exception):
    pass


@dataclass
class Part:
    """Um pedaço guardado de uma faixa (vídeo ou áudio)."""
    path: Path
    tags: list
    key: str = None              # chave/cabeçalho que valiam neste pedaço (repetidos ao começar no meio)
    cmap: str = None


@dataclass
class Seg:
    seq: int
    dur: float
    start: float                 # segundos desde que o canal abriu
    video: Part
    audio: Part = None


@dataclass
class Track:
    """Estado de uma lista do canal (vídeo ou áudio) enquanto é baixada."""
    name: str
    url: str
    key: str = None
    cmap: str = None
    gap: bool = False
    pending: dict = field(default_factory=dict)   # seq -> (duração, url, tags) ainda não baixados


class Session(threading.Thread):
    """Baixa um canal continuamente e mantém as listas locais."""

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
        self.target = 6
        self.version = 3
        self.stop_ev = threading.Event()
        self.ready = False
        self.buffered = 0.0          # segundos guardados
        self.video = self.audio = None
        self.audio_info = ""         # atributos do EXT-X-MEDIA de áudio para a lista principal local

    # ---- rede
    def _get(self, url, binary=False):
        req = urllib.request.Request(url, headers=self.headers)
        with urllib.request.urlopen(req, timeout=15) as r:
            data = r.read()
            return (data if binary else data.decode("utf-8", "replace")), r.geturl()

    def _choose_tracks(self):
        """Lista de vídeo escolhida (até 1080p) e, se houver, a lista de áudio separada dela."""
        text, base = self._get(self.url)
        if "#EXT-X-STREAM-INF" not in text:
            return base, None
        best, best_key, group = None, None, None
        lines = text.splitlines()
        for i, line in enumerate(lines):
            if line.startswith("#EXT-X-STREAM-INF"):
                a = _attrs(line)
                h = int(a.get("RESOLUTION", "0x0").split("x")[-1] or 0)
                bw = int(a.get("BANDWIDTH", "0") or 0)
                uri = next((x.strip() for x in lines[i + 1:] if x.strip() and not x.startswith("#")), None)
                key = (h <= MAX_HEIGHT, h if h <= MAX_HEIGHT else -h, bw)
                if uri and (best_key is None or key > best_key):
                    best, best_key, group = urllib.parse.urljoin(base, uri), key, a.get("AUDIO")
        if not best:
            raise Unsupported("lista sem opções de vídeo")
        audio = None
        if group:
            options = [_attrs(x) for x in lines if x.startswith("#EXT-X-MEDIA") and "TYPE=AUDIO" in x]
            options = [o for o in options if o.get("GROUP-ID") == group and o.get("URI")]
            if options:
                pick = next((o for o in options if o.get("DEFAULT") == "YES"), options[0])
                audio = urllib.parse.urljoin(base, pick["URI"])
                lang = f',LANGUAGE="{pick["LANGUAGE"]}"' if pick.get("LANGUAGE") else ""
                self.audio_info = f'NAME="{pick.get("NAME", "audio")}"{lang}'
        return best, audio

    # ---- tarefa
    def run(self):
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            vurl, aurl = self._choose_tracks()
            self.video = Track("v", vurl)
            self.audio = Track("a", aurl) if aurl else None
            last_seq, errors = None, 0
            while not self.stop_ev.is_set():
                try:
                    for t in filter(None, (self.video, self.audio)):
                        self._refresh(t, last_seq)
                    errors = 0
                except Unsupported:
                    raise
                except OSError as e:
                    errors += 1
                    if errors >= 4:
                        raise
                    log.warning("Timeshift: falha ao ler a lista (%s), tentando de novo", e)
                    self.stop_ev.wait(2)
                    continue
                # pedaços prontos: vídeo (e áudio com o mesmo número, quando o áudio é separado)
                seqs = sorted(s for s in self.video.pending
                              if self.audio is None or s in self.audio.pending)
                if last_seq is None:
                    if self.audio and self.video.pending and not seqs:
                        raise Unsupported("vídeo e áudio com numeração diferente")
                    seqs = seqs[-START_SEGMENTS:]
                self._download_batch(seqs, parallel=last_seq is None)
                if seqs:
                    last_seq = seqs[-1]
                for t in filter(None, (self.video, self.audio)):  # esquece o que ficou para trás
                    t.pending = {s: v for s, v in t.pending.items() if s > (last_seq or -1)}
                self.stop_ev.wait(max(1.0, self.target / 2))
        except Unsupported as e:
            log.info("Timeshift indisponível neste canal: %s", e)
            self.owner._emit_failed(self.sid)
        except Exception as e:  # noqa: BLE001
            log.warning("Timeshift parou: %s", e)
            self.owner._emit_failed(self.sid)

    def _refresh(self, track, last_seq):
        text, base = self._get(track.url)
        if "#EXT-X-ENDLIST" in text and not self.segments:
            raise Unsupported("não é ao vivo")
        if "#EXT-X-BYTERANGE" in text:
            raise Unsupported("pedaços por intervalo de bytes")
        seq, dur, tags = 0, None, []
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            if line.startswith("#EXT-X-TARGETDURATION"):
                self.target = max(self.target if track.name == "a" else 1, int(float(line.split(":")[1])))
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
                if dur is not None and (last_seq is None or seq > last_seq):
                    track.pending.setdefault(seq, (dur, urllib.parse.urljoin(base, line), tags))
                seq += 1
                dur, tags = None, []

    def _fetch(self, uri):
        try:
            return self._get(uri, binary=True)[0]
        except OSError:
            return None  # _store tenta de novo

    def _download_batch(self, seqs, parallel):
        tracks = [t for t in (self.video, self.audio) if t]
        datas = {}
        if parallel and seqs:
            # primeiros pedaços em paralelo: o canal abre quase tão rápido quanto direto
            jobs = [(t, s) for s in seqs for t in tracks]
            with ThreadPoolExecutor(len(jobs)) as ex:
                for (t, s), d in zip(jobs, ex.map(lambda j: self._fetch(j[0].pending[j[1]][1]), jobs)):
                    datas[(t.name, s)] = d
        for s in seqs:
            if self.stop_ev.is_set():
                return
            parts = [self._store(t, s, datas.get((t.name, s))) for t in tracks]
            if any(p is None for p in parts):
                continue  # pedaço perdido: o próximo leva a marca de descontinuidade
            self._add(Seg(s, self.video.pending[s][0], 0.0, parts[0], parts[1] if self.audio else None))

    def _store(self, track, seq, data):
        dur, uri, tags = track.pending[seq]
        ext = ".mp4" if any(t.startswith("#EXT-X-MAP") for t in tags) or track.cmap or \
            uri.split("?")[0].endswith((".m4s", ".mp4")) else ".ts"
        path = self.dir / f"{track.name}{seq}{ext}"
        for attempt in range(3):
            try:
                if data is None:
                    data, _ = self._get(uri, binary=True)
                if self.stop_ev.is_set():
                    return None  # canal trocado: a pasta já foi apagada
                path.write_bytes(data)
                break
            except OSError as e:
                data = None
                if self.stop_ev.is_set():
                    return None
                if attempt == 2:
                    log.warning("Timeshift: pedaço %s%d perdido (%s)", track.name, seq, e)
                    track.gap = True
                    return None
                self.stop_ev.wait(1)
        if track.gap and "#EXT-X-DISCONTINUITY" not in tags:
            tags = ["#EXT-X-DISCONTINUITY"] + tags
        track.gap = False
        for t in tags:  # chave/cabeçalho valem para os pedaços seguintes também
            if t.startswith("#EXT-X-KEY"):
                track.key = t
            elif t.startswith("#EXT-X-MAP"):
                track.cmap = t
        return Part(path, tags, track.key, track.cmap)

    def _add(self, seg):
        with self.lock:
            seg.start = self.end
            self.segments.append(seg)
            self.end += seg.dur
            self.buffered += seg.dur
            # guarda só os últimos minutos configurados: apaga os mais antigos
            while self.buffered > self.max_seconds and len(self.segments) > START_SEGMENTS:
                old = self.segments.pop(0)
                self.buffered -= old.dur
                for p in filter(None, (old.video, old.audio)):
                    p.path.unlink(missing_ok=True)
            count = len(self.segments)
        if not self.ready and count >= READY_SEGMENTS:
            self.ready = True
            self.owner._emit_ready(self.sid)

    # ---- consultas
    def span(self):
        """(início, fim) do que está guardado, em segundos desde que o canal abriu."""
        with self.lock:
            if not self.segments:
                return 0.0, 0.0
            return self.segments[0].start, self.end

    def segment_at(self, pos):
        """(seq, início) do pedaço que contém o instante pos."""
        with self.lock:
            segs = list(self.segments)
        if not segs:
            return None, 0.0
        pick = segs[0]
        for s in segs:
            if s.start <= pos:
                pick = s
            else:
                break
        return pick.seq, pick.start

    def _range(self, from_seq=None, to_seq=None):
        with self.lock:
            segs = [s for s in self.segments if (from_seq is None or s.seq >= from_seq)
                    and (to_seq is None or s.seq <= to_seq)]
            return segs or self.segments[-1:]

    def media_playlist(self, kind, from_seq=None, to_seq=None, vod=False):
        """Lista de uma faixa ("v" ou "a") começando em from_seq (o VLC sempre toca do início da lista)."""
        segs = self._range(from_seq, to_seq)
        target = max([self.target] + [int(s.dur + 0.999) for s in segs])
        lines = ["#EXTM3U", f"#EXT-X-VERSION:{self.version}", f"#EXT-X-TARGETDURATION:{target}",
                 f"#EXT-X-MEDIA-SEQUENCE:{segs[0].seq if segs else 0}"]
        if vod:
            lines.append("#EXT-X-PLAYLIST-TYPE:VOD")
        for i, s in enumerate(segs):
            part = s.video if kind == "v" else s.audio
            tags = part.tags
            if i == 0:  # começando no meio: repete a chave/cabeçalho que valiam ali
                tags = [t for t in (part.key, part.cmap) if t and t not in tags] + tags
            lines += tags
            lines += [f"#EXTINF:{s.dur:.3f},", f"/{self.sid}/{part.path.name}"]
        if vod:
            lines.append("#EXT-X-ENDLIST")
        return "\n".join(lines) + "\n"

    def playlist(self, query):
        """Lista que o VLC abre: a de vídeo, ou uma principal com vídeo + áudio separado."""
        frm, to, vod = query.get("from"), query.get("to"), "vod" in query
        if not self.audio:
            return self.media_playlist("v", frm, to, vod)
        q = "&".join(f"{k}={v}" for k, v in (("from", frm), ("to", to)) if v is not None) + ("&vod=1" if vod else "")
        return ("#EXTM3U\n"
                f'#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="aud",{self.audio_info},DEFAULT=YES,AUTOSELECT=YES,'
                f'URI="/{self.sid}/a.m3u8?{q}"\n'
                f'#EXT-X-STREAM-INF:BANDWIDTH=5000000,AUDIO="aud"\n/{self.sid}/v.m3u8?{q}\n')

    def export(self, start, end, dest, remux):
        """Salva os pedaços entre start e end (segundos desde que o canal abriu) num arquivo de vídeo.

        Pedaços .ts emendados formam um .ts válido; nos fMP4 o cabeçalho (EXT-X-MAP) vai no começo.
        Com o áudio separado, o VLC junta as duas faixas (remux) a partir de uma lista local fechada."""
        with self.lock:
            segs = [s for s in self.segments if s.start + s.dur > start and s.start < end]
        if not segs:
            raise ValueError("nada guardado nesse intervalo")
        if any(p and p.key and "METHOD=NONE" not in p.key for s in segs for p in (s.video, s.audio)):
            raise ValueError("este canal é criptografado e não pode ser salvo")
        if self.audio:
            remux(f"/{self.sid}/live.m3u8?from={segs[0].seq}&to={segs[-1].seq}&vod=1", dest)
        else:
            with open(dest, "wb") as out:
                if segs[0].video.cmap:
                    uri = re.search(r'URI="([^"]*)"', segs[0].video.cmap).group(1)
                    out.write(self._get(uri, binary=True)[0])
                for s in segs:
                    if s.video.path.exists():
                        out.write(s.video.path.read_bytes())
        return sum(s.dur for s in segs)

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

    def __init__(self, max_minutes=60, instance=None, parent=None):
        super().__init__(parent)
        self.max_seconds = max_minutes * 60
        self.instance = instance     # VLC usado para juntar vídeo + áudio ao salvar trechos
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
                q = {k: v[0] for k, v in urllib.parse.parse_qs(query).items()}
                num = {k: int(v) for k, v in q.items() if k in ("from", "to") and v.isdigit()}
                if "vod" in q:
                    num["vod"] = 1
                name = parts[1]
                if name == "live.m3u8":
                    body, ctype = s.playlist(num).encode(), M3U8
                elif name in ("v.m3u8", "a.m3u8"):
                    body = s.media_playlist(name[0], num.get("from"), num.get("to"), "vod" in num).encode()
                    ctype = M3U8
                else:
                    f = s.file(name)
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

    def fmp4(self):
        s = self.session
        return bool(s and s.video and s.video.cmap)

    def export(self, start, end, dest):
        """Salva o trecho em segundo plano; avisa pelo sinal exported."""
        s = self.session
        if not s:
            return self.exported.emit("", "o canal não está sendo guardado")

        def work():
            try:
                secs = s.export(start, end, dest, self._remux)
                log.info("Timeshift: salvos %.0f s em %s", secs, dest)
                self.exported.emit(str(dest), "")
            except Exception as e:  # noqa: BLE001
                log.warning("Timeshift: não foi possível salvar o trecho: %s", e)
                self.exported.emit("", str(e))
        threading.Thread(target=work, name="timeshift-salvar", daemon=True).start()

    def _remux(self, path, dest):
        """O VLC lê a lista local fechada (vídeo + áudio) e grava as duas faixas num .ts, sem recodificar."""
        from .vlcload import vlc
        media = self.instance.media_new(f"http://127.0.0.1:{self.port}{path}")
        dst = str(dest).replace("\\", "/").replace('"', "")
        media.add_option(f':sout=#std{{access=file,mux=ts,dst="{dst}"}}')
        media.add_option(":sout-all")
        media.add_option(":no-sout-display")
        player = self.instance.media_player_new()
        player.set_media(media)
        player.play()
        t0 = time.monotonic()
        while player.get_state() not in (vlc.State.Ended, vlc.State.Error, vlc.State.Stopped) \
                and time.monotonic() - t0 < 3600:
            time.sleep(0.3)
        ok = player.get_state() == vlc.State.Ended
        player.stop()
        player.release()
        if not ok or not Path(dest).exists() or Path(dest).stat().st_size == 0:
            raise ValueError("o VLC não conseguiu juntar vídeo e áudio")

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
