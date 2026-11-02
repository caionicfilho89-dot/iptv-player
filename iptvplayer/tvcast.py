"""Mandar pra TV: o player da própria TV (DLNA/UPnP) toca o canal, filme ou episódio, repassado pelo PC.

A TV é achada na rede de casa por SSDP (o serviço AVTransport, porta 9197 nas Samsung) e recebe um endereço
no PC; o PC busca o vídeo no servidor e repassa. Assim a conta usa uma conexão só (a do PC) e não precisa de
navegador nem de permissão na TV.
- filme/episódio (MP4): o PC resolve o link da conta uma vez e repassa os pedaços que a TV pedir (Range), então
  dá para avançar/voltar e começar de onde estava;
- canal da conta (.ts): repassa o fluxo direto;
- canal em HLS (.m3u8): o PC junta os pedaços .ts num fluxo contínuo."""
import re
import secrets
import shutil
import socket
import subprocess
import threading
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from xml.sax.saxutils import escape

import requests
from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from .log import log

UA = "Mozilla/5.0"
SSDP_ST = "urn:schemas-upnp-org:service:AVTransport:1"
RENDERING = "urn:schemas-upnp-org:service:RenderingControl:1"
DLNA_VOD = "DLNA.ORG_OP=01;DLNA.ORG_CI=0;DLNA.ORG_FLAGS=01700000000000000000000000000000"
DLNA_LIVE = "DLNA.ORG_OP=00;DLNA.ORG_CI=0;DLNA.ORG_FLAGS=01700000000000000000000000000000"
BUSY_TRIES = 6              # o servidor ainda conta a conexão que o PC acabou de largar por alguns segundos
RESUME_TRIES = 8            # o servidor da conta engasgou no meio: reconecta do ponto onde parou, sem a TV perceber
READ_TIMEOUT = 12           # segundos sem chegar nada do servidor = conexão travada
CREATE_NO_WINDOW = 0x08000000   # Windows: não pisca janela de console ao rodar o ffmpeg

# Modo madrugada na TV: a TV decodifica o som sozinha, então o compressor do VLC do PC não a alcança.
# Só pra filme/episódio e só com o modo madrugada ligado, o PC transcodifica o ÁUDIO antes de repassar
# (o vídeo é copiado, sem recodificar — CPU baixa). O mesmo efeito do modo madrugada: levanta os diálogos
# baixos (acompressor) e segura os estouros (alimiter). Sem ffmpeg, repassa normal (sem o efeito).
NIGHT_AF = ("acompressor=threshold=-27dB:ratio=6:attack=20:release=250:makeup=6,"
            "alimiter=level_in=1:limit=0.9:attack=5:release=50")


def ip_towards(target, port=80):
    """IP deste PC na placa de rede que chega em target (não o das placas virtuais, como as do VMware)."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.connect((target, port))   # não envia nada: só escolhe a placa de rede
        return s.getsockname()[0]


def find_renderer(timeout=3):
    """Endereço de controle (AVTransport) da TV na rede de casa, ou None."""
    msg = (f'M-SEARCH * HTTP/1.1\r\nHOST: 239.255.255.250:1900\r\nMAN: "ssdp:discover"\r\nMX: 2\r\n'
           f"ST: {SSDP_ST}\r\n\r\n").encode()
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(timeout)
    try:
        s.bind((ip_towards("8.8.8.8"), 0))   # pela placa da rede de casa
        s.sendto(msg, ("239.255.255.250", 1900))
        end = time.time() + timeout
        while time.time() < end:
            try:
                data, _addr = s.recvfrom(4096)
            except socket.timeout:
                break
            m = re.search(rb"(?i)location:\s*(\S+)", data)
            if not m:
                continue
            loc = m.group(1).decode()
            try:
                desc = urllib.request.urlopen(loc, timeout=5).read().decode("utf8", "ignore")
            except OSError:
                continue
            ctrl = re.search(r"AVTransport:1</serviceType>.*?<controlURL>(.*?)</controlURL>", desc, re.S)
            if ctrl:
                name = re.search(r"<friendlyName>(.*?)</friendlyName>", desc)
                log.info("TV: achei %s", name.group(1) if name else loc)
                return urllib.parse.urljoin(loc, ctrl.group(1).strip())
    finally:
        s.close()
    return None


def soap(ctrl, action, args="", timeout=15, service=SSDP_ST):
    body = (f'<?xml version="1.0" encoding="utf-8"?><s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
            f's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/"><s:Body><u:{action} '
            f'xmlns:u="{service}"><InstanceID>0</InstanceID>{args}</u:{action}></s:Body></s:Envelope>').encode()
    req = urllib.request.Request(ctrl, data=body, method="POST", headers={
        "Content-Type": 'text/xml; charset="utf-8"', "SOAPACTION": f'"{service}#{action}"'})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf8", "ignore")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf8", "ignore")


def _secs(clock):
    """"0:12:34" -> 754.0 (a TV responde "NOT_IMPLEMENTED" ou vazio quando não sabe)."""
    try:
        h, m, sec = clock.split(":")
        return int(h) * 3600 + int(m) * 60 + float(sec)
    except (ValueError, AttributeError):
        return 0.0


def _clock(sec):
    return time.strftime("%H:%M:%S", time.gmtime(max(0, int(sec))))


def _kind(url):
    path = urllib.parse.urlsplit(url).path.lower()
    if path.endswith(".m3u8"):
        return "hls"
    if path.endswith((".mp4", ".m4v", ".mkv", ".avi", ".mov")):
        return "file"
    return "ts"


class Relay:
    """Servidor no PC que a TV acessa: repassa a fonte atual (uma conexão por vez com o servidor)."""

    def __init__(self, cfg=None):
        self.cfg = cfg
        self.http = requests.Session()
        self.http.headers["User-Agent"] = UA
        self.lock = threading.Lock()
        self.src = None       # {"url", "kind", "edge", "size", "token", "headers", "ff", "dur"}
        self.upstream = None
        self.proc = None      # ffmpeg do modo madrugada na TV (quando "ff" está no src)
        relay = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *_a):
                pass

            def _current(self):
                src = relay.src
                if not src or src["token"] not in self.path:
                    self.send_response(404)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return None
                return src

            def _headers(self, src, extra=()):
                vod = src["kind"] == "file"
                self.send_header("Content-Type", "video/mp4" if vod else "video/mp2t")
                self.send_header("transferMode.dlna.org", "Streaming")
                self.send_header("contentFeatures.dlna.org", DLNA_VOD if vod else DLNA_LIVE)
                if vod:
                    self.send_header("Accept-Ranges", "bytes")
                for k, v in extra:
                    self.send_header(k, v)
                if not vod:
                    self.send_header("Connection", "close")
                self.end_headers()

            def do_HEAD(self):
                src = self._current()
                if src:
                    self.send_response(200)
                    self._headers(src, [("Content-Length", str(src["size"]))] if src.get("size") else [])

            def do_GET(self):
                src = self._current()
                if not src:
                    return
                if src["kind"] == "hls":
                    return relay.serve_hls(self, src)
                if src["kind"] == "file" and src.get("ff"):   # modo madrugada: transcodifica o áudio
                    return relay.serve_transcode(self, src)
                relay.serve_raw(self, src, self.headers.get("Range"))

        self.server = ThreadingHTTPServer(("0.0.0.0", 0), Handler)
        self.server.daemon_threads = True
        self.server.handle_error = lambda *_a: None   # a TV fecha conexões ao pular de ponto: não é erro
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, name="tv-relay", daemon=True).start()

    def set_source(self, url, headers):
        """Troca a fonte; devolve o caminho novo (a TV busca de novo e não reaproveita o anterior)."""
        kind = _kind(url)
        src = {"url": url, "kind": kind, "edge": url, "size": 0, "token": secrets.token_hex(4), "headers": headers}
        if kind == "file":   # o link da conta redireciona para um servidor com token: resolve uma vez só
            r = self._get(url, allow_redirects=False)
            if r is not None:
                src["edge"] = urllib.parse.urljoin(url, r.headers.get("Location", url)) if r.is_redirect else url
                r.close()
            r = self._get(src["edge"], extra={"Range": "bytes=0-0"})
            if r is not None:
                src["size"] = int(r.headers.get("Content-Range", "/0").split("/")[-1] or 0)
                r.close()
            if self.cfg is not None and self.cfg.get("night_audio_tv") and self.cfg.get("night_audio"):
                ff = shutil.which("ffmpeg")
                if ff:   # modo madrugada na TV (experimental): transcodifica o áudio; sonda a duração pro seek
                    src["ff"] = ff
                    src["dur"] = self._probe_duration(src["edge"], headers)
                    log.info("TV: modo madrugada ligado — transcodificando o áudio (ffmpeg)")
        with self.lock:
            self._close_upstream()
            self.src = src
        ext = {"file": "mp4", "ts": "ts", "hls": "ts"}[kind]
        return f"/{src['token']}/video.{ext}", kind

    def clear(self):
        with self.lock:
            self._close_upstream()
            self._kill_proc()
            self.src = None

    def _close_upstream(self):
        if self.upstream is not None:
            try:
                self.upstream.close()
            except Exception:  # noqa: BLE001
                pass
            self.upstream = None

    def _kill_proc(self):
        if self.proc is not None:
            try:
                self.proc.kill()
            except Exception:  # noqa: BLE001
                pass
            self.proc = None

    def _probe_duration(self, url, headers):
        """Duração do vídeo em segundos (ffprobe), ou 0 se não der — aí o seek na TV fica desligado."""
        ff = shutil.which("ffprobe")
        if not ff:
            return 0.0
        cmd = [ff, "-v", "error", "-user_agent", UA]
        ref = (headers or {}).get("Referer")
        if ref:
            cmd += ["-headers", f"Referer: {ref}\r\n"]
        cmd += ["-show_entries", "format=duration", "-of", "default=nk=1:nw=1", url]
        try:
            out = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                 timeout=20, creationflags=CREATE_NO_WINDOW).stdout.decode("ascii", "ignore")
            return float(out.strip() or 0)
        except (OSError, ValueError, subprocess.SubprocessError):
            return 0.0

    def _get(self, url, extra=None, stream=True, allow_redirects=True):
        for _ in range(BUSY_TRIES):
            try:
                r = self.http.get(url, headers=extra or {}, stream=stream, timeout=(10, READ_TIMEOUT),
                                  allow_redirects=allow_redirects)
            except requests.RequestException:
                time.sleep(2)
                continue
            if r.status_code < 400:
                return r
            r.close()
            time.sleep(2.5)   # 403/503: a conexão anterior ainda não foi liberada no servidor
        return None

    def open_upstream(self, src, rng):
        with self.lock:   # a TV abre conexões novas ao pular de ponto: a nova substitui a anterior
            self._close_upstream()
            r = self._get(src["edge"], extra={"Range": rng} if rng else None)
            if r is None and src["kind"] == "file":   # o link com token pode ter vencido: pede outro à conta
                edge = self._resolve(src["url"])
                if edge:
                    src["edge"] = edge
                    r = self._get(edge, extra={"Range": rng} if rng else None)
            self.upstream = r
            return r

    def _resolve(self, url):
        r = self._get(url, allow_redirects=False)
        if r is None:
            return None
        edge = urllib.parse.urljoin(url, r.headers.get("Location", url)) if r.is_redirect else url
        r.close()
        return edge

    def pump(self, handler, src, up, rng):
        """Repassa o vídeo para a TV. Se o servidor da conta travar ou cair no meio, reconecta a partir do
        byte onde parou (filme) ou de novo (canal ao vivo) e continua na mesma conexão com a TV."""
        start, end = 0, ""
        m = re.match(r"bytes=(\d+)-(\d*)", rng or "")
        if m:
            start, end = int(m.group(1)), m.group(2)
        total = int(up.headers.get("Content-Length") or 0)
        sent, tries = 0, 0
        while True:
            try:
                for chunk in up.iter_content(65536):
                    try:
                        handler.wfile.write(chunk)
                    except OSError:
                        return up.close()   # a TV fechou: pulou para outro ponto ou parou
                    sent += len(chunk)
                    tries = 0
                if src["kind"] == "file" and (not total or sent >= total):
                    return up.close()   # mandou tudo
                reason = "o servidor fechou antes do fim"
            except Exception as e:  # noqa: BLE001 - travou, caiu ou foi fechada por outra conexão
                reason = type(e).__name__
            up.close()
            if self.src is not src or self.upstream is not up:
                return   # trocou de vídeo, parou, ou a TV já abriu outra conexão (pulou de ponto)
            tries += 1
            if tries > RESUME_TRIES:
                log.warning("TV: o servidor não voltou depois de %d tentativas; desisti", RESUME_TRIES)
                return
            log.warning("TV: o servidor travou no meio (%s), reconectando (tentativa %d)", reason, tries)
            new_rng = f"bytes={start + sent}-{end}" if src["kind"] == "file" else None
            new = self.open_upstream(src, new_rng)
            if new is None:
                continue
            if src["kind"] == "file" and new.status_code != 206:
                new.close()   # o servidor não aceitou continuar do meio: não dá para emendar
                log.warning("TV: o servidor não continuou do ponto (%s)", new.status_code)
                return
            up = new

    def serve_raw(self, handler, src, rng):
        """Repasse direto (sem mexer no áudio): canais ao vivo e filmes fora do modo madrugada."""
        up = self.open_upstream(src, rng)
        if up is None:
            handler.send_response(503)
            handler.send_header("Content-Length", "0")
            handler.end_headers()
            return
        handler.send_response(up.status_code)
        handler._headers(src, [(k, up.headers[k]) for k in ("Content-Length", "Content-Range") if up.headers.get(k)])
        self.pump(handler, src, up, rng)

    def open_transcode(self, src, start_sec):
        """Sobe um ffmpeg que lê o filme a partir de start_sec, copia o vídeo e recomprime só o áudio.
        A nova conexão (seek) substitui a anterior, como no open_upstream. Devolve o processo ou None."""
        cmd = [src["ff"], "-hide_banner", "-loglevel", "error", "-nostdin", "-user_agent", UA]
        ref = (src.get("headers") or {}).get("Referer")
        if ref:
            cmd += ["-headers", f"Referer: {ref}\r\n"]
        if start_sec and start_sec > 1:
            cmd += ["-ss", f"{start_sec:.3f}"]   # seek na entrada (rápido): pula sem recodificar vídeo
        cmd += ["-i", src["edge"], "-map", "0:v:0?", "-map", "0:a:0?",
                "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-ac", "2", "-af", NIGHT_AF,
                # mp4 fragmentado: dá para transmitir sem conhecer o fim (casa com o video/mp4 do DIDL)
                "-movflags", "frag_keyframe+empty_moov+default_base_moof", "-f", "mp4", "pipe:1"]
        with self.lock:
            self._kill_proc()
            try:
                p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                     creationflags=CREATE_NO_WINDOW)
            except OSError as e:
                log.warning("TV: ffmpeg não subiu (%s) — repassando sem o modo madrugada", e)
                return None
            self.proc = p
            return p

    def serve_transcode(self, handler, src):
        """Modo madrugada na TV: entrega o filme com o áudio já recomprimido. O seek por byte que a TV pede
        é convertido em tempo (proporcional ao tamanho) e vira um -ss no ffmpeg. Se o ffmpeg não subir,
        cai no repasse normal para o filme não quebrar."""
        size, dur = src.get("size") or 0, src.get("dur") or 0
        rng = handler.headers.get("Range")
        start = 0
        m = re.match(r"bytes=(\d+)-", rng or "")
        if m:
            start = int(m.group(1))
        seekable = bool(size and dur)
        t = (start / size) * dur if (seekable and start) else 0.0
        proc = self.open_transcode(src, t)
        if proc is None:
            return self.serve_raw(handler, src, rng)   # plano B: repassa sem o efeito
        if seekable and start:
            handler.send_response(206)
            extra = [("Content-Range", f"bytes {start}-{size - 1}/{size}"), ("Content-Length", str(size - start))]
        else:
            handler.send_response(200)
            extra = [("Content-Length", str(size))] if size else []
        handler.send_header("Content-Type", "video/mp4")
        handler.send_header("transferMode.dlna.org", "Streaming")
        handler.send_header("contentFeatures.dlna.org", DLNA_VOD)
        if seekable:
            handler.send_header("Accept-Ranges", "bytes")
        for k, v in extra:
            handler.send_header(k, v)
        handler.end_headers()
        self.pump_transcode(handler, src, proc)

    def pump_transcode(self, handler, src, proc):
        """Repassa a saída do ffmpeg para a TV. Para quando a TV fecha (parou ou pulou de ponto) ou quando
        outra conexão/seek substitui este processo."""
        try:
            while self.src is src and self.proc is proc:
                chunk = proc.stdout.read(65536)
                if not chunk:
                    break   # ffmpeg terminou (fim do filme) ou foi encerrado por um seek
                try:
                    handler.wfile.write(chunk)
                except OSError:
                    break   # a TV fechou a conexão
        finally:
            try:
                proc.stdout.close()
            except Exception:  # noqa: BLE001
                pass
            with self.lock:
                if self.proc is proc:
                    self._kill_proc()

    def serve_hls(self, handler, src):
        """Canal HLS: baixa a lista e os pedaços .ts e manda tudo num fluxo contínuo."""
        handler.send_response(200)
        handler._headers(src)
        token, last, url = src["token"], -1, src["url"]
        try:
            text = self.http.get(url, timeout=15).text
            if "#EXT-X-STREAM-INF" in text:   # lista principal: a melhor opção até 1080p
                best, best_bw = None, -1
                lines = text.splitlines()
                for i, line in enumerate(lines):
                    if line.startswith("#EXT-X-STREAM-INF"):
                        bw = int((re.search(r"BANDWIDTH=(\d+)", line) or [0, 0])[1])
                        h = int((re.search(r"RESOLUTION=\d+x(\d+)", line) or [0, 0])[1])
                        nxt = next((x for x in lines[i + 1:] if x and not x.startswith("#")), None)
                        if nxt and h <= 1080 and bw > best_bw:
                            best, best_bw = urllib.parse.urljoin(url, nxt.strip()), bw
                url = best or url
            while self.src is not None and self.src["token"] == token:
                r = self.http.get(url, timeout=15)
                seq = int((re.search(r"#EXT-X-MEDIA-SEQUENCE:(\d+)", r.text) or [0, 0])[1])
                segs = [urllib.parse.urljoin(r.url, x.strip()) for x in r.text.splitlines() if x.strip()
                        and not x.startswith("#")]
                start = max(0, len(segs) - 3) if last < 0 else 0
                for i, seg in enumerate(segs[start:], start):
                    if seq + i <= last:
                        continue
                    handler.wfile.write(self.http.get(seg, timeout=20).content)
                    last = seq + i
                time.sleep(2)
        except Exception:  # noqa: BLE001 - a TV parou ou o canal caiu
            pass


class TvCast(QObject):
    status = pyqtSignal(str, str)    # texto para a tela, cor ("ok", "warn", "bad"); "" = acabou
    progress = pyqtSignal(float, float, str)   # posição, duração (s) e estado da TV ("PLAYING", "PAUSED_PLAYBACK"…)
    _done = pyqtSignal(int, str, str)
    _polled = pyqtSignal(int, float, float, str)

    def __init__(self, cfg, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.relay = None
        self.channel = None
        self.vod = False          # filme/episódio (dá para voltar/avançar); canal ao vivo só pausa
        self.playing = False      # a TV já confirmou que começou
        self.state = ""
        self.pos = self.dur = 0.0
        self.volume0 = None     # volume da TV quando começou a passar
        self._job = 0
        self._polling = False
        self._hold_until = 0.0    # depois de um comando, ignora a posição antiga que a TV ainda informa
        self._done.connect(self._on_done)
        self._polled.connect(self._on_polled)
        self._poll_timer = QTimer(self, interval=1000, timeout=self._poll)

    @property
    def active(self):
        return self.channel is not None

    # ---- começar / parar
    def start(self, ch, url, opts):
        """Canal ao vivo."""
        return self._send(ch, url, opts, 0)

    def start_vod(self, item, url, position=0):
        """Filme ou episódio, a partir de position (segundos)."""
        return self._send(item, url, list(getattr(item, "opts", []) or []), int(position or 0))

    def _send(self, item, url, opts, position):
        if not str(url).startswith(("http://", "https://")):
            return False
        headers = {}
        for o in opts:
            if str(o).startswith(":http-referrer="):
                headers["Referer"] = str(o).split("=", 1)[1]
        self.channel = item
        self.vod = _kind(url) == "file"
        self.playing = False
        self.state, self.pos, self.dur = "", float(position or 0), 0.0
        self._poll_timer.stop()
        self._job += 1
        job = self._job
        self.status.emit(f"📺 Mandando “{item.name}” para a TV…", "warn")
        threading.Thread(target=self._work, args=(job, item.name, url, headers, position), name="tv-enviar",
                         daemon=True).start()
        return True

    def stop(self):
        name = self.channel.name if self.channel else ""
        self.channel = None
        self.playing = False
        self._poll_timer.stop()
        self._job += 1
        if self.relay:
            self.relay.clear()
        ctrl = self.cfg["tv_dmr"]
        if name and ctrl:
            log.info("TV: parou de mandar %s", name)
            threading.Thread(target=lambda: soap(ctrl, "Stop", timeout=8), name="tv-parar", daemon=True).start()
        self.status.emit("", "")

    # ---- trabalho (em tarefa: a TV e o servidor são lentos)
    def _renderer(self):
        ctrl = self.cfg["tv_dmr"]
        if ctrl:
            try:
                code, _ = soap(ctrl, "GetTransportInfo", timeout=6)
                if code == 200:
                    return ctrl
            except OSError:
                pass   # a TV mudou de endereço: procura de novo
        ctrl = find_renderer()
        if ctrl:
            self.cfg["tv_dmr"] = ctrl
        return ctrl

    def _work(self, job, name, url, headers, position):
        try:
            ctrl = self._renderer()
            if not ctrl:
                raise OSError("não achei a TV na rede. Ela está ligada e na mesma rede do PC?")
            if self.relay is None:
                self.relay = Relay(self.cfg)
            path, kind = self.relay.set_source(url, headers)
            host = urllib.parse.urlsplit(ctrl).hostname
            link = f"http://{ip_towards(host, 9197)}:{self.relay.port}{path}"
            vod = kind == "file"
            mime = "video/mp4" if vod else "video/mp2t"
            didl = ('<DIDL-Lite xmlns="urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/" '
                    'xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:upnp="urn:schemas-upnp-org:metadata-1-0/upnp/">'
                    f'<item id="1" parentID="0" restricted="1"><dc:title>{escape(name)}</dc:title>'
                    f'<upnp:class>object.item.videoItem{".movie" if vod else ".videoBroadcast"}</upnp:class>'
                    f'<res protocolInfo="http-get:*:{mime}:{DLNA_VOD if vod else DLNA_LIVE}">{escape(link)}</res>'
                    '</item></DIDL-Lite>')
            soap(ctrl, "Stop", timeout=8)
            code, body = soap(ctrl, "SetAVTransportURI",
                              f"<CurrentURI>{escape(link)}</CurrentURI><CurrentURIMetaData>{escape(didl)}</CurrentURIMetaData>")
            if code != 200:
                raise OSError(f"a TV recusou o vídeo ({_upnp_error(body)})")
            time.sleep(1.5)
            soap(ctrl, "Play", "<Speed>1</Speed>")
            for _ in range(40):   # até 20 s para a TV começar
                if job != self._job:
                    return
                state = re.findall(r"<CurrentTransportState>(\w+)", soap(ctrl, "GetTransportInfo", timeout=6)[1])
                if state == ["PLAYING"]:
                    break
                time.sleep(0.5)
            else:
                raise OSError("a TV não começou a tocar (ela mostrou alguma mensagem?)")
            if vod and position > 5:
                soap(ctrl, "Seek", f"<Unit>REL_TIME</Unit><Target>{time.strftime('%H:%M:%S', time.gmtime(position))}</Target>")
            vol = re.findall(r"<CurrentVolume>(\d+)", soap(ctrl.replace("AVTransport", "RenderingControl"), "GetVolume",
                             "<Channel>Master</Channel>", timeout=5, service=RENDERING)[1])
            self.volume0 = int(vol[0]) if vol else None
            log.info("TV: tocando %s no player da TV", name)
            self._done.emit(job, f"📺 Passando na TV: {name}", "ok")
        except Exception as e:  # noqa: BLE001
            log.warning("TV: não deu para mandar %s: %s", name, e)
            self._done.emit(job, f"✕ Não deu para mandar para a TV: {e}", "bad")

    def _on_done(self, job, text, color):
        if job != self._job:
            return   # já trocou ou parou
        if color == "bad":
            self.channel = None
            if self.relay:
                self.relay.clear()
        elif color == "ok":
            self.playing = True
            self.state = "PLAYING"
            self._hold_until = time.monotonic() + 3   # a TV ainda está indo para o ponto de onde parou
            self._poll_timer.start()
        self.status.emit(text, color)

    # ---- controles: os botões do Pudim no PC mandam na TV
    def _command(self, action, args="", service=SSDP_ST):
        ctrl = self.cfg["tv_dmr"]
        if not (self.playing and ctrl):
            return False
        if service == RENDERING:
            ctrl = ctrl.replace("AVTransport", "RenderingControl")

        def run():
            try:
                code, body = soap(ctrl, action, args, timeout=8, service=service)
                if code != 200:
                    log.warning("TV: %s recusado (%s)", action, _upnp_error(body))
            except OSError as e:
                log.warning("TV: %s falhou: %s", action, e)
        threading.Thread(target=run, name=f"tv-{action}", daemon=True).start()
        return True

    def toggle_pause(self):
        """Pausa ou continua. Devolve True se ficou pausado."""
        pause = self.state == "PLAYING"
        if self._command("Pause" if pause else "Play", "" if pause else "<Speed>1</Speed>"):
            self.state = "PAUSED_PLAYBACK" if pause else "PLAYING"
            self._hold_until = time.monotonic() + 2
            self.progress.emit(self.pos, self.dur, self.state)
        return pause

    def seek_to(self, sec):
        if not self.vod:
            return False
        sec = max(0.0, min(sec, self.dur - 3) if self.dur else sec)
        if not self._command("Seek", f"<Unit>REL_TIME</Unit><Target>{_clock(sec)}</Target>"):
            return False
        self.pos = sec
        self._hold_until = time.monotonic() + 3
        self.progress.emit(self.pos, self.dur, self.state)
        return True

    def seek_by(self, seconds):
        return self.seek_to(self.pos + seconds)

    def set_volume(self, volume):
        return self._command("SetVolume", f"<Channel>Master</Channel><DesiredVolume>{int(volume)}</DesiredVolume>",
                             RENDERING)

    def set_mute(self, mute):
        return self._command("SetMute", f"<Channel>Master</Channel><DesiredMute>{int(bool(mute))}</DesiredMute>",
                             RENDERING)

    def _poll(self):
        ctrl = self.cfg["tv_dmr"]
        if self._polling or not (self.playing and ctrl):
            return
        self._polling = True
        job = self._job

        def run():
            try:
                info = soap(ctrl, "GetPositionInfo", timeout=5)[1]
                state = re.findall(r"<CurrentTransportState>(\w+)", soap(ctrl, "GetTransportInfo", timeout=5)[1])
                rel = re.search(r"<RelTime>([^<]*)</RelTime>", info)
                dur = re.search(r"<TrackDuration>([^<]*)</TrackDuration>", info)
                self._polled.emit(job, _secs(rel.group(1)) if rel else 0.0, _secs(dur.group(1)) if dur else 0.0,
                                  state[0] if state else "")
            except OSError:
                pass   # a TV demorou: tenta no próximo segundo
            finally:
                self._polling = False
        threading.Thread(target=run, name="tv-posicao", daemon=True).start()

    def _on_polled(self, job, pos, dur, state):
        if job != self._job or not self.playing:
            return
        if dur > 0:
            self.dur = dur
        if time.monotonic() >= self._hold_until:
            self.pos = pos
            if state in ("PLAYING", "PAUSED_PLAYBACK", "STOPPED"):
                self.state = state
        self.progress.emit(self.pos, self.dur, self.state)


def _upnp_error(body):
    code = re.search(r"<errorCode>(\d+)</errorCode>", body or "")
    desc = re.search(r"<errorDescription>(.*?)</errorDescription>", body or "")
    return " ".join(x.group(1) for x in (code, desc) if x) or "erro desconhecido"
