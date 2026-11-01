"""Mandar pra TV: o PC baixa o canal e a TV Samsung assiste pelo navegador dela, pela rede de casa.

O canal é baixado em HLS pelo buffer do timeshift (agora aberto para a rede de casa) e a TV abre uma página
com um player. A conta continua usando uma conexão só: a do PC. Links de conta Xtream em .ts viram o mesmo
canal em .m3u8 (o servidor entrega os dois formatos). A TV é achada na rede pela API dela (porta 8001) e
controlada pelo websocket do controle remoto (porta 8002): na primeira vez ela pede para permitir o Pudim."""
import base64
import json
import socket
import ssl
import threading
import time
import urllib.request
from urllib.parse import urlsplit

from PyQt6.QtCore import QObject, pyqtSignal

from .log import log
from .timeshift import Timeshift

BROWSER_ID = "3201907018784"     # app "Internet" das TVs Samsung (Tizen)
APP_NAME = base64.b64encode(b"Pudim").decode()
BUFFER_MINUTES = 5

PAGE = """<!doctype html><html><head><meta charset="utf-8"><title>Pudim</title>
<style>html,body{margin:0;height:100%;background:#000;overflow:hidden}
video{width:100%;height:100%;object-fit:contain;background:#000}
#t{position:fixed;top:28px;left:36px;color:#fff;font:600 34px sans-serif;text-shadow:0 2px 8px #000;
transition:opacity 1.5s}</style></head>
<body><video id="v" autoplay playsinline></video><div id="t"></div>
<script>
var v = document.getElementById("v"), src = null;
function ask(cb) {   /* canal escolhido no PC */
  var x = new XMLHttpRequest();
  x.onload = function () { try { cb(JSON.parse(x.responseText)); } catch (e) {} };
  x.open("GET", "/tv.json?" + Date.now()); x.send();
}
function start(d) {
  if (d.vod) {   /* filme ou episódio: o arquivo direto do servidor, continuando de onde estava no PC */
    v.src = src;
    v.addEventListener("loadedmetadata", function () { if (d.t) v.currentTime = d.t; }, {once: true});
    v.play(); return;
  }
  if (v.canPlayType("application/vnd.apple.mpegurl")) { v.src = src; v.play(); return; }
  var s = document.createElement("script");   /* TV sem HLS nativo: player em JavaScript */
  s.src = "https://cdn.jsdelivr.net/npm/hls.js@1/dist/hls.min.js";
  s.onload = function () {
    var h = new Hls({liveSyncDurationCount: 3});
    h.loadSource(src); h.attachMedia(v); v.play();
    h.on(Hls.Events.ERROR, function (e, d) { if (d.fatal) setTimeout(function () { location.reload(); }, 3000); });
  };
  document.head.appendChild(s);
}
v.onerror = function () { setTimeout(function () { location.reload(); }, 3000); };
ask(function (d) {
  src = d.src;
  document.getElementById("t").innerHTML = "&#127854; " + d.name;
  start(d);
  setTimeout(function () { document.getElementById("t").style.opacity = 0; }, 6000);
});
setInterval(function () { ask(function (d) { if (src && d.src !== src) location.reload(); }); }, 3000);
</script></body></html>"""


def hls_url(url):
    """O canal num formato que o navegador da TV toca (HLS), ou None."""
    parts = urlsplit(url)
    path = parts.path.split("?")[0]
    if path.endswith(".m3u8"):
        return url
    pieces = [p for p in path.split("/") if p]
    if pieces and pieces[0] == "live":
        pieces = pieces[1:]
    if len(pieces) == 3 and pieces[0].isdigit() and pieces[1].isdigit():   # conta Xtream: …/usuário/senha/id.ts
        stream_id = pieces[2].rsplit(".", 1)[0]
        return f"{parts.scheme}://{parts.netloc}/live/{pieces[0]}/{pieces[1]}/{stream_id}.m3u8"
    return None


def ip_towards(target):
    """IP deste PC na placa de rede que chega em target (não o das placas virtuais, como as do VMware)."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.connect((target, 8001))   # não envia nada: só escolhe a placa de rede
        return s.getsockname()[0]


def _tv_at(ip, timeout=1.5):
    try:
        with urllib.request.urlopen(f"http://{ip}:8001/api/v2/", timeout=timeout) as r:
            dev = json.load(r).get("device", {})
        return dev if "Samsung" in dev.get("type", "") + dev.get("name", "") else None
    except Exception:  # noqa: BLE001
        return None


def find_tv(last=""):
    """IP da TV Samsung na rede de casa (o último usado primeiro; senão procura na rede inteira)."""
    if last and _tv_at(last, timeout=8):   # a TV demora para responder enquanto recebe vídeo
        return last
    prefix = ip_towards("8.8.8.8").rsplit(".", 1)[0]   # a rede de casa: a da saída para a internet
    found = []

    def probe(ip):
        if _tv_at(ip):
            found.append(ip)
    threads = [threading.Thread(target=probe, args=(f"{prefix}.{i}",), daemon=True) for i in range(1, 255)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(3)
    return found[0] if found else None


class TvRemote:
    """Teclas e texto para a TV pelo websocket do controle remoto."""

    def __init__(self, ip, token):
        import websocket   # websocket-client
        url = f"wss://{ip}:8002/api/v2/channels/samsung.remote.control?name={APP_NAME}"
        if token:
            url += f"&token={token}"
        # na primeira vez a TV mostra "Permitir Pudim?": dá tempo para a pessoa aceitar
        self.ws = websocket.create_connection(url, sslopt={"cert_reqs": ssl.CERT_NONE}, timeout=40)
        for _ in range(8):   # antes da resposta algumas TVs mandam avisos (ms.remote.touchDisable…)
            msg = json.loads(self.ws.recv())
            if msg.get("event") in ("ms.channel.connect", "ms.channel.unauthorized", "ms.channel.timeOut"):
                break
        if msg.get("event") != "ms.channel.connect":
            raise OSError("a TV não permitiu o controle (" + str(msg.get("event")) + ")")
        self.token = (msg.get("data") or {}).get("token") or token

    def key(self, *keys, pause=0.8):
        for k in keys:
            self.ws.send(json.dumps({"method": "ms.remote.control", "params": {
                "Cmd": "Click", "DataOfCmd": k, "Option": "false", "TypeOfRemote": "SendRemoteKey"}}))
            time.sleep(pause)

    def text(self, text):
        self.ws.send(json.dumps({"method": "ms.remote.control", "params": {
            "Cmd": base64.b64encode(text.encode()).decode(), "DataOfCmd": "base64", "TypeOfRemote": "SendInputString"}}))
        time.sleep(0.4)
        self.ws.send(json.dumps({"method": "ms.remote.control", "params": {"TypeOfRemote": "SendInputEnd"}}))

    def close(self):
        try:
            self.ws.close()
        except Exception:  # noqa: BLE001
            pass


class TvCast(QObject):
    status = pyqtSignal(str, str)    # texto para a tela, cor ("ok", "warn", "bad"); "" = acabou
    _tv_done = pyqtSignal(str, str)  # resultado da tarefa que abre a página na TV (texto, cor)

    def __init__(self, cfg, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.ts = None
        self.sid = None
        self.channel = None
        self.page_open = False       # a página já está aberta na TV: trocar de canal não reabre o navegador
        self._tv_done.connect(self._on_tv_done)

    @property
    def active(self):
        return self.channel is not None

    def _server(self):
        if self.ts is None:
            self.ts = Timeshift(max_minutes=BUFFER_MINUTES, parent=self, conns=1, host="0.0.0.0")
            self.ts.ready.connect(self._on_ready)
            self.ts.failed.connect(self._on_failed)
        return self.ts

    def start_vod(self, item, url, position=0):
        """Filme ou episódio: a TV toca o arquivo direto do servidor, a partir de position (segundos)."""
        if not url.startswith(("http://", "https://")):
            return False
        ts = self._server()
        ts.close_all()
        self.channel, self.sid = item, None
        name = item.name.replace("<", "").replace("&", "e")
        ts.pages["/tv.html"] = PAGE
        ts.pages["/tv.json"] = json.dumps({"src": url, "name": name, "vod": True, "t": int(position)})
        log.info("TV: mandando o filme %s (a partir de %d s)", item.name, position)
        if self.page_open:
            self.status.emit(f"📺 Passando na TV: {item.name}", "ok")
            return True
        self.status.emit(f"📺 Abrindo “{item.name}” na TV…", "warn")
        threading.Thread(target=self._open_on_tv, args=(f":{ts.port}/tv.html", item.name), name="tv-abrir",
                         daemon=True).start()
        return True

    def start(self, ch, url, opts):
        """Começa a mandar o canal; False se o formato do canal não dá para a TV."""
        src = hls_url(url)
        if not src:
            return False
        self._server().close_all()
        self.channel = ch
        ua = [o for o in opts if str(o).startswith(":http-")] or [":http-user-agent=Mozilla/5.0"]
        self.sid = self.ts.open(src, ua)
        log.info("TV: preparando %s", ch.name)
        self.status.emit(f"📺 Preparando “{ch.name}” para a TV…", "warn")
        return True

    def stop(self):
        if self.ts:
            self.ts.close_all()
        name, self.channel, self.sid = (self.channel.name if self.channel else ""), None, None
        self.page_open = False
        if self.ts:
            self.ts.pages.clear()
        if name:
            log.info("TV: parou de mandar %s", name)
            threading.Thread(target=self._close_browser, name="tv-fechar", daemon=True).start()
        self.status.emit("", "")

    def _on_ready(self, sid, _local):
        if sid != self.sid or not self.channel:
            return
        name = self.channel.name.replace("<", "").replace("&", "e")
        self.ts.pages["/tv.html"] = PAGE
        self.ts.pages["/tv.json"] = json.dumps({"src": f"/{sid}/live.m3u8", "name": name})
        if self.page_open:   # a página da TV percebe o canal novo sozinha em até 3 s
            log.info("TV: trocou para %s", self.channel.name)
            return self.status.emit(f"📺 Passando na TV: {self.channel.name}", "ok")
        url = f":{self.ts.port}/tv.html"   # o IP do PC entra quando a TV for achada (o da mesma rede dela)
        self.status.emit(f"📺 Abrindo “{self.channel.name}” na TV…", "warn")
        threading.Thread(target=self._open_on_tv, args=(url, self.channel.name), name="tv-abrir", daemon=True).start()

    def _on_tv_done(self, text, color):
        if not self.channel:
            return   # parou enquanto abria
        self.page_open = color == "ok"
        if color == "bad":
            self.stop()
            return self.status.emit(text, color)
        self.status.emit(text, color)

    def _on_failed(self, sid):
        if sid == self.sid and self.channel:
            log.warning("TV: o canal %s não pôde ser preparado", self.channel.name)
            self.status.emit(f"✕ Não deu para mandar “{self.channel.name}” para a TV (o canal não respondeu)", "bad")
            self.channel = self.sid = None

    # ---- TV (em tarefas: a rede da TV é lenta)
    def _tv(self):
        ip = find_tv(self.cfg["tv_ip"])
        if not ip:
            raise OSError("não achei a TV na rede. Ela está ligada e no mesmo Wi-Fi?")
        self.cfg["tv_ip"] = ip
        return ip

    def _open_on_tv(self, url, name):
        try:
            ip = self._tv()
            url = ip_towards(ip) + url
            app = f"http://{ip}:8001/api/v2/applications/{BROWSER_ID}"
            try:   # fecha o navegador para começar sempre da página inicial dele
                urllib.request.urlopen(urllib.request.Request(app, method="DELETE"), timeout=12).read()
                time.sleep(3)
            except Exception:  # noqa: BLE001
                pass
            urllib.request.urlopen(urllib.request.Request(app, method="POST"), timeout=12).read()
            for _ in range(30):
                try:
                    with urllib.request.urlopen(app, timeout=12) as r:
                        if json.load(r).get("visible"):
                            break
                except Exception:  # noqa: BLE001
                    pass
                time.sleep(1)
            time.sleep(4)   # a página inicial do navegador carregar
            remote = TvRemote(ip, self.cfg["tv_token"])
            if remote.token and remote.token != self.cfg["tv_token"]:
                self.cfg["tv_token"] = remote.token
                self.cfg.save()
            page = url[url.index("/", url.index(":")):]
            asked = time.time()
            for attempt in (1, 2):
                # barra de endereço: sobe até ela, abre, digita e confirma
                remote.key("KEY_UP", "KEY_UP", "KEY_UP", "KEY_ENTER")
                time.sleep(2)
                remote.text(url)
                time.sleep(1)
                remote.key("KEY_ENTER")
                for _ in range(40):   # a TV pediu a página ao PC? (o navegador dela é lento)
                    if self.ts.page_seen.get(page, 0) >= asked:
                        break
                    time.sleep(0.5)
                else:
                    log.warning("TV: o navegador não abriu a página (tentativa %d)", attempt)
                    remote.key("KEY_RETURN", "KEY_RETURN")   # fecha o teclado/menu que tiver ficado aberto
                    continue
                break
            remote.close()
            if self.ts.page_seen.get(page, 0) < asked:
                raise OSError("o navegador da TV não abriu a página do Pudim. Confira se a TV e o PC estão na "
                              "mesma rede e se o firewall do Windows deixa o Pudim receber conexões")
            log.info("TV: %s aberto no navegador da TV (%s)", name, ip)
            self._tv_done.emit(f"📺 Passando na TV: {name}", "ok")
        except Exception as e:  # noqa: BLE001
            log.warning("TV: não abriu na TV: %s", e)
            self._tv_done.emit(f"✕ Não consegui abrir na TV: {e}", "bad")

    def _close_browser(self):
        ip = self.cfg["tv_ip"]
        if not ip:
            return
        try:
            req = urllib.request.Request(f"http://{ip}:8001/api/v2/applications/{BROWSER_ID}", method="DELETE")
            urllib.request.urlopen(req, timeout=12).read()
        except Exception:  # noqa: BLE001
            pass
