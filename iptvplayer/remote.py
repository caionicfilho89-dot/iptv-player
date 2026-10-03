"""Controle pelo celular: uma página na rede de casa (Wi-Fi) que comanda o programa.

O endereço leva uma chave aleatória (no QR code), para que só quem lê o QR controle o player.
Os comandos chegam numa tarefa separada e são entregues à janela pelo sinal `command`.
"""
import json
import secrets
import socket
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from PyQt6.QtCore import QObject, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QPixmap

from .log import log

PORT = 8765


def _rank(ip):
    """Prefere o endereço típico de rede de casa; VPNs costumam usar 10.x e ficam por último."""
    a, b = (int(x) for x in ip.split(".")[:2])
    if a == 192 and b == 168:
        return 0
    if a == 172 and 16 <= b <= 31:
        return 1
    if a == 10:
        return 2
    return 3


def lan_ip():
    """IP deste PC na rede de casa (o celular acessa por ele)."""
    ips = set()
    try:
        ips.update(socket.gethostbyname_ex(socket.gethostname())[2])
    except OSError:
        pass
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))  # não envia nada: só escolhe a placa de rede da saída padrão
        ips.add(s.getsockname()[0])
    except OSError:
        pass
    finally:
        s.close()
    ips = [ip for ip in ips if not ip.startswith(("127.", "169.254."))]
    return min(ips, key=_rank) if ips else "127.0.0.1"


def qr_pixmap(text, size=240):
    import segno
    m = segno.make(text, error="m").matrix
    n = len(m) + 8  # margem branca de 4 módulos
    pm = QPixmap(size, size)
    pm.fill(QColor("white"))
    p = QPainter(pm)
    cell = size / n
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor("black"))
    for y, row in enumerate(m):
        for x, v in enumerate(row):
            if v:
                p.drawRect(int((x + 4) * cell), int((y + 4) * cell), int(cell + 1), int(cell + 1))
    p.end()
    return pm


class RemoteServer(QObject):
    command = pyqtSignal(str, str)    # comando, argumento

    def __init__(self, key="", parent=None):
        super().__init__(parent)
        self.key = key or secrets.token_urlsafe(6)
        self.state = {}               # atualizado pela janela; lido pela página do celular
        self.server = None

    @property
    def running(self):
        return self.server is not None

    def url(self):
        return f"http://{lan_ip()}:{PORT}/?k={self.key}"

    def start(self):
        if self.server:
            return True
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_a):
                pass

            def _send(self, code, body, ctype="application/json; charset=utf-8"):
                data = body.encode() if isinstance(body, str) else body
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(data)

            def _ok_key(self, q):
                return secrets.compare_digest(q.get("k", [""])[0], owner.key)

            def do_GET(self):
                path, _, query = self.path.partition("?")
                q = urllib.parse.parse_qs(query)
                if not self._ok_key(q):
                    return self._send(403, "Leia o QR code no IPTV Player para abrir o controle.",
                                      "text/plain; charset=utf-8")
                if path == "/":
                    return self._send(200, PAGE, "text/html; charset=utf-8")
                if path == "/state":
                    return self._send(200, json.dumps(owner.state, ensure_ascii=False))
                if path == "/cmd":
                    owner.command.emit(q.get("c", [""])[0], q.get("a", [""])[0])
                    return self._send(200, '{"ok": true}')
                self._send(404, "{}")

        try:
            self.server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
        except OSError as e:
            log.warning("Controle pelo celular: porta %d ocupada (%s)", PORT, e)
            return False
        self.server.daemon_threads = True
        threading.Thread(target=self.server.serve_forever, name="controle-celular", daemon=True).start()
        log.info("Controle pelo celular ligado em %s", self.url().split("?")[0])
        return True

    def stop(self):
        if self.server:
            self.server.shutdown()
            self.server.server_close()
            self.server = None
            log.info("Controle pelo celular desligado")


PAGE = """<!doctype html><html lang="pt-br"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1">
<meta name="theme-color" content="#0e1016"><title>IPTV Player — controle</title>
<style>
:root{--bg:#0e1016;--p:#1b202b;--p2:#232a38;--t:#e7e9f0;--m:#8a93a8;--a:#7c5cff;--ok:#3ddc97;--w:#ffb84d}
*{box-sizing:border-box;-webkit-tap-highlight-color:transparent}
body{margin:0;background:var(--bg);color:var(--t);font:16px system-ui,Segoe UI,Roboto,sans-serif}
header{position:sticky;top:0;background:var(--bg);padding:14px 16px 8px;z-index:2}
#name{font-size:20px;font-weight:700;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
#status{color:var(--m);font-size:14px;margin-top:2px}
.grid{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;padding:8px 16px}
button{background:var(--p);color:var(--t);border:0;border-radius:14px;padding:14px 4px;font-size:15px;font-weight:600}
button:active{background:var(--p2)}button.big{background:var(--a);color:#fff}button.on{outline:2px solid var(--a)}
.wide{grid-column:span 2}
#vol{width:100%;accent-color:var(--a)}
.row{padding:4px 16px 8px}
select,input{width:100%;background:var(--p);color:var(--t);border:0;border-radius:12px;padding:12px;font-size:16px}
#list{padding:4px 12px 40px}
.ch{display:flex;gap:10px;align-items:center;padding:12px 8px;border-bottom:1px solid var(--p2)}
.ch b{color:var(--m);font-weight:600;min-width:34px;text-align:right}.ch.cur{color:var(--a);font-weight:700}
</style></head><body>
<header><div id="name">…</div><div id="status"></div></header>
<div class="grid">
<button onclick="c('prev')">▲ Canal</button><button class="big wide" id="pp" onclick="c('pause')">⏯</button><button onclick="c('next')">▼ Canal</button>
<button onclick="c('back')">⟲ 30s</button><button onclick="c('fwd')">30s ⟳</button><button onclick="c('live')">● Ao vivo</button><button id="mu" onclick="c('mute')">🔇</button>
<button id="cc" onclick="c('cc')">CC</button><button id="dub" onclick="c('dub')">🎙 Dublar</button><button class="wide" onclick="c('fullscreen')">⛶ Tela cheia</button>
</div>
<div class="row"><input id="vol" type="range" min="0" max="125" oninput="vq(this.value)"></div>
<div class="row"><select id="cat" onchange="c('cat',this.value)"></select></div>
<div class="row"><input id="q" placeholder="Buscar canal…" oninput="draw()"></div>
<div id="list"></div>
<script>
const K=new URLSearchParams(location.search).get('k');let S={},vt=null,dragging=false;
function c(cmd,a=''){fetch(`/cmd?k=${K}&c=${cmd}&a=${encodeURIComponent(a)}`).then(()=>setTimeout(poll,250))}
function vq(v){dragging=true;clearTimeout(vt);vt=setTimeout(()=>{c('vol',v);dragging=false},150)}
function esc(s){return s.replace(/[&<>"]/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[m]))}
function draw(){const q=document.getElementById('q').value.toLowerCase();let h='';
(S.channels||[]).forEach((ch,i)=>{if(q&&!ch.name.toLowerCase().includes(q))return;
h+=`<div class="ch${ch.url==S.url?' cur':''}" onclick="c('play','${i}')"><b>${ch.num||''}</b><span>${esc(ch.name)}</span></div>`});
document.getElementById('list').innerHTML=h||'<p style="color:#8a93a8">Nenhum canal.</p>'}
function poll(){fetch(`/state?k=${K}`).then(r=>r.json()).then(s=>{const old=S.cat_key+'|'+(S.channels||[]).length+'|'+S.url;S=s;
document.getElementById('name').textContent=s.name||'Nenhum canal';document.getElementById('status').textContent=s.status||'';
document.getElementById('pp').textContent=s.paused?'▶ Continuar':'❚❚ Pausar';
document.getElementById('mu').textContent=s.muted?'🔈':'🔇';
document.getElementById('cc').className=s.cc?'on':'';document.getElementById('dub').className=s.dub?'on':'';
if(!dragging)document.getElementById('vol').value=s.volume;
const sel=document.getElementById('cat');if(sel.options.length!=(s.cats||[]).length){sel.innerHTML=(s.cats||[]).map(x=>`<option value="${x[0]}">${esc(x[1])}</option>`).join('')}
sel.value=s.cat_key;if(old!=s.cat_key+'|'+(s.channels||[]).length+'|'+s.url)draw();
}).catch(()=>{document.getElementById('status').textContent='Sem conexão com o PC…'})}
poll();setInterval(poll,1500);
</script></body></html>"""
