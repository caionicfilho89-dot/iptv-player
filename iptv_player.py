"""IPTV Player — player moderno para as playlists .m3u desta pasta.

Recursos:
  * Interface escura com logos dos canais, categorias, busca e filtro por grupo
  * Favoritos e recentes (salvos em player_config.json)
  * Pular canais offline automaticamente (detecta erro, timeout e travamento)
  * Zapping automático: troca de canal a cada N segundos (sequencial ou aleatório)
  * Testar lista: verifica em segundo plano quais canais estão no ar

Atalhos: PgUp/PgDn troca canal · F11 ou duplo clique tela cheia · Esc sai da tela cheia
         Ctrl+F busca · Ctrl+D favorito · Ctrl+M mudo · Ctrl+Z liga/desliga zapping
"""
import hashlib
import json
import os
import random
import re
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

VLC_DIR = r"C:\Program Files\VideoLAN\VLC"
if sys.platform == "win32" and os.path.isdir(VLC_DIR):
    os.add_dll_directory(VLC_DIR)
try:
    import vlc  # noqa: E402
    if getattr(vlc, "dll", None) is None:
        raise ImportError
except (ImportError, OSError, NotImplementedError):
    vlc = None  # main() avisa o usuário numa janela
VLC_MISSING_MSG = ("O VLC (64 bits) não foi encontrado neste computador.\n\n"
                   "Instale gratuitamente em https://www.videolan.org/vlc/ e abra o IPTV Player de novo.")

from PyQt6.QtCore import QMargins, QObject, QPointF, QRect, QSize, Qt, QTimer, QUrl, pyqtSignal  # noqa: E402
from PyQt6.QtGui import (  # noqa: E402
    QColor, QFont, QGuiApplication, QIcon, QKeySequence, QLinearGradient,
    QPainter, QPainterPath, QPixmap, QShortcut,
)
from PyQt6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest  # noqa: E402
from PyQt6.QtWidgets import (  # noqa: E402
    QApplication, QCheckBox, QComboBox, QFrame, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QMainWindow, QMenu, QProgressBar, QPushButton,
    QSlider, QSpinBox, QSplitter, QStyle, QStyledItemDelegate, QVBoxLayout, QWidget,
)

# no .exe (PyInstaller) as playlists ficam ao lado do executável
BASE = Path(sys.executable if getattr(sys, "frozen", False) else __file__).resolve().parent
LOGO_CACHE = BASE / ".cache" / "logos"
CONFIG_FILE = BASE / "player_config.json"
USER_AGENT = b"VLC/3.0.21 LibVLC/3.0.21"

DEAD_TTL = 6 * 3600          # canal marcado offline fica assim por 6h
CONNECT_TIMEOUT = 15         # segundos para o canal começar a tocar
STALL_TIMEOUT = 12           # segundos sem avançar = travado
MAX_CONSECUTIVE_FAILS = 25   # evita loop infinito pulando canais

CATEGORIES = [
    ("melhor_iptv", "Todos (melhores)", "📺"),
    ("português", "Português", "💬"),
    ("filmes", "Filmes", "🎬"),
    ("series", "Séries", "🎞️"),
    ("esportes", "Esportes", "⚽"),
    ("notícias", "Notícias", "📰"),
    ("documentários", "Documentários", "🌍"),
    ("música", "Música", "🎵"),
    ("infantil", "Infantil", "🧸"),
    ("religiosos", "Religiosos", "🙏"),
    ("gerais", "Gerais", "📡"),
    ("english", "English", "🗽"),
    ("español", "Español", "💃"),
]
FAV_KEY, RECENT_KEY = "__fav", "__recent"

C = {
    "bg": "#0e1016", "panel": "#141821", "surface": "#1b202b", "surface2": "#232a38",
    "border": "#262d3b", "text": "#e7e9f0", "muted": "#8a93a8", "accent": "#7c5cff",
    "accent2": "#22c1ee", "ok": "#3ddc97", "warn": "#ffb84d", "bad": "#ff5d6c",
}

STYLE = f"""
* {{ font-family: 'Segoe UI'; font-size: 10pt; color: {C['text']}; }}
QMainWindow, QWidget#root {{ background: {C['bg']}; }}
QWidget#sidebar {{ background: {C['panel']}; border-right: 1px solid {C['border']}; }}
QWidget#channels {{ background: {C['panel']}; }}
QWidget#controls {{ background: {C['panel']}; border-top: 1px solid {C['border']}; }}
QLabel#brand {{ font-size: 15pt; font-weight: 700; padding: 14px 16px 6px 16px; }}
QLabel#muted, QLabel#count {{ color: {C['muted']}; }}
QLabel#nowTitle {{ font-size: 13pt; font-weight: 600; }}
QListWidget {{ background: transparent; border: none; outline: 0; }}
QListWidget#nav::item {{ padding: 9px 14px; margin: 1px 8px; border-radius: 8px; }}
QListWidget#nav::item:hover {{ background: {C['surface']}; }}
QListWidget#nav::item:selected {{ background: {C['surface2']}; color: white; border-left: 3px solid {C['accent']}; }}
QLineEdit, QComboBox, QSpinBox {{
    background: {C['surface']}; border: 1px solid {C['border']}; border-radius: 8px; padding: 7px 10px;
}}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus {{ border: 1px solid {C['accent']}; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox QAbstractItemView {{ background: {C['surface']}; border: 1px solid {C['border']};
    selection-background-color: {C['accent']}; }}
QPushButton {{
    background: {C['surface']}; border: 1px solid {C['border']}; border-radius: 10px;
    padding: 7px 12px; min-height: 22px;
}}
QPushButton:hover {{ background: {C['surface2']}; border-color: #34405a; }}
QPushButton:pressed {{ background: {C['border']}; }}
QPushButton:checked {{ background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 {C['accent']}, stop:1 {C['accent2']});
    border: none; color: white; }}
QPushButton#round {{ border-radius: 19px; min-width: 38px; max-width: 38px; min-height: 38px; max-height: 38px;
    padding: 0; font-size: 13pt; }}
QPushButton#play {{ border-radius: 23px; min-width: 46px; max-width: 46px; min-height: 46px; max-height: 46px;
    padding: 0; font-size: 15pt; border: none;
    background: qlineargradient(x1:0,y1:0,x2:1,y2:1, stop:0 {C['accent']}, stop:1 {C['accent2']}); }}
QCheckBox {{ spacing: 6px; }}
QCheckBox::indicator {{ width: 16px; height: 16px; border-radius: 4px; border: 1px solid #3a4459; background: {C['surface']}; }}
QCheckBox::indicator:checked {{ background: {C['accent']}; border-color: {C['accent']}; }}
QSlider::groove:horizontal {{ height: 4px; background: {C['surface2']}; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {C['accent']}; border-radius: 2px; }}
QSlider::handle:horizontal {{ background: white; width: 12px; margin: -5px 0; border-radius: 6px; }}
QProgressBar {{ background: {C['bg']}; border: none; max-height: 3px; }}
QProgressBar::chunk {{ background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 {C['accent']}, stop:1 {C['accent2']}); }}
QScrollBar:vertical {{ background: transparent; width: 8px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: #2c3446; border-radius: 4px; min-height: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
QSplitter::handle {{ background: {C['border']}; width: 1px; }}
QMenu {{ background: {C['surface']}; border: 1px solid {C['border']}; padding: 4px; }}
QMenu::item {{ padding: 6px 18px; border-radius: 6px; }}
QMenu::item:selected {{ background: {C['accent']}; }}
QToolTip {{ background: {C['surface2']}; color: {C['text']}; border: 1px solid {C['border']}; }}
"""


# ----------------------------------------------------------------------------- dados
@dataclass
class Channel:
    name: str
    url: str
    logo: str = ""
    group: str = ""
    quality: str = ""
    not247: bool = False
    geo: bool = False
    opts: list = field(default_factory=list)


EXTINF_RE = re.compile(r'^#EXTINF:\s*-?\d+((?:\s+[\w-]+="[^"]*")*)\s*,(.*)$')
ATTR_RE = re.compile(r'([\w-]+)="([^"]*)"')
QUALITY_RE = re.compile(r"\((\d{3,4}p)\)")
TAG_RE = re.compile(r"\[[^\]]*\]|\(\d{3,4}p\)")


def parse_m3u(path):
    channels, seen = [], set()
    info, opts = None, []
    with open(path, encoding="utf-8", errors="replace") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#EXTM3U"):
                continue
            if line.startswith("#EXTINF"):
                m = EXTINF_RE.match(line)
                if m:
                    attrs, title = dict(ATTR_RE.findall(m.group(1))), m.group(2)
                else:
                    attrs, title = dict(ATTR_RE.findall(line)), line.rsplit(",", 1)[-1]
                info, opts = (attrs, title.strip()), []
            elif line.startswith("#EXTVLCOPT:"):
                opts.append(":" + line[len("#EXTVLCOPT:"):])
            elif line.startswith("#"):
                continue
            else:
                if line in seen:
                    info = None
                    continue
                seen.add(line)
                attrs, title = info or ({}, line)
                q = QUALITY_RE.search(title)
                channels.append(Channel(
                    name=re.sub(r"\s{2,}", " ", TAG_RE.sub("", title)).strip() or title,
                    url=line,
                    logo=attrs.get("tvg-logo", ""),
                    group=attrs.get("group-title", "").split(";")[0],
                    quality=q.group(1) if q else "",
                    not247="Not 24/7" in title,
                    geo="Geo-blocked" in title,
                    opts=opts,
                ))
                info, opts = None, []
    return channels


class Config:
    def __init__(self):
        self.data = {
            "favorites": [], "recents": [], "dead": {}, "volume": 80,
            "skip_dead": True, "hide_dead": False, "zap_interval": 30, "zap_random": False,
            "last_category": "melhor_iptv", "last_url": "",
        }
        try:
            self.data.update(json.loads(CONFIG_FILE.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            pass
        now = time.time()
        self.data["dead"] = {u: t for u, t in self.data["dead"].items() if now - t < DEAD_TTL}

    def __getitem__(self, k):
        return self.data[k]

    def __setitem__(self, k, v):
        self.data[k] = v

    def save(self):
        try:
            CONFIG_FILE.write_text(json.dumps(self.data, ensure_ascii=False, indent=1), encoding="utf-8")
        except OSError:
            pass


# ----------------------------------------------------------------------------- rede
def make_request(url, ch=None, timeout=8000):
    req = QNetworkRequest(QUrl(url))
    req.setTransferTimeout(timeout)
    req.setRawHeader(b"User-Agent", USER_AGENT)
    for o in (ch.opts if ch else []):
        if o.startswith(":http-user-agent="):
            req.setRawHeader(b"User-Agent", o.split("=", 1)[1].encode())
        elif o.startswith(":http-referrer="):
            req.setRawHeader(b"Referer", o.split("=", 1)[1].encode())
    return req


class LogoLoader(QObject):
    """Baixa logos sob demanda (só dos itens visíveis) com cache em disco."""
    loaded = pyqtSignal(str)
    MAX_ACTIVE = 8

    def __init__(self, parent=None):
        super().__init__(parent)
        LOGO_CACHE.mkdir(parents=True, exist_ok=True)
        self.nam = QNetworkAccessManager(self)
        self.pix, self.failed, self.pending, self.queue, self.active = {}, set(), set(), [], 0

    @staticmethod
    def _file(url):
        return LOGO_CACHE / (hashlib.md5(url.encode()).hexdigest() + ".img")

    def get(self, url):
        if not url or url in self.failed:
            return None
        if url in self.pix:
            return self.pix[url]
        f = self._file(url)
        if f.exists():
            pm = QPixmap(str(f))
            if not pm.isNull():
                self.pix[url] = pm.scaled(160, 100, Qt.AspectRatioMode.KeepAspectRatio,
                                          Qt.TransformationMode.SmoothTransformation)
                return self.pix[url]
            self.failed.add(url)
            return None
        if url not in self.pending:
            self.pending.add(url)
            self.queue.append(url)
            self._pump()
        return None

    def _pump(self):
        while self.active < self.MAX_ACTIVE and self.queue:
            url = self.queue.pop()  # mais recente primeiro = itens visíveis agora
            self.active += 1
            reply = self.nam.get(make_request(url))
            reply.finished.connect(lambda r=reply, u=url: self._done(r, u))

    def _done(self, reply, url):
        self.active -= 1
        self.pending.discard(url)
        data = bytes(reply.readAll()) if reply.error() == QNetworkReply.NetworkError.NoError else b""
        reply.deleteLater()
        pm = QPixmap()
        if data and pm.loadFromData(data):
            self._file(url).write_bytes(data)
            self.pix[url] = pm.scaled(160, 100, Qt.AspectRatioMode.KeepAspectRatio,
                                      Qt.TransformationMode.SmoothTransformation)
        else:
            self.failed.add(url)
        self.loaded.emit(url)
        self._pump()


class Scanner(QObject):
    """Testa em paralelo se os canais respondem (HTTP 2xx/3xx)."""
    result = pyqtSignal(str, bool)
    progress = pyqtSignal(int, int)
    finished = pyqtSignal()
    MAX_ACTIVE = 12

    def __init__(self, parent=None):
        super().__init__(parent)
        self.nam = QNetworkAccessManager(self)
        self.running = False
        self.replies = []

    def start(self, channels):
        self.queue, self.total, self.done = list(channels), len(channels), 0
        self.active, self.decided, self.running = 0, set(), True
        self._pump()

    def stop(self):
        self.running = False
        self.queue = []
        for r in list(self.replies):
            r.abort()

    def _pump(self):
        while self.running and self.active < self.MAX_ACTIVE and self.queue:
            ch = self.queue.pop(0)
            self.active += 1
            reply = self.nam.get(make_request(ch.url, ch, timeout=7000))
            self.replies.append(reply)
            reply.readyRead.connect(lambda r=reply, u=ch.url: self._ready(r, u))
            reply.finished.connect(lambda r=reply, u=ch.url: self._done(r, u))
        if self.active == 0 and not self.queue:
            self.running = False
            self.finished.emit()

    def _decide(self, url, ok):
        if url not in self.decided:
            self.decided.add(url)
            self.result.emit(url, ok)

    def _ready(self, reply, url):
        code = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
        if code and 200 <= int(code) < 400:
            self._decide(url, True)
            reply.abort()  # streams contínuos nunca terminam; basta o primeiro byte

    def _done(self, reply, url):
        code = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
        ok = reply.error() == QNetworkReply.NetworkError.NoError and code and 200 <= int(code) < 400
        if self.running or url in self.decided:
            self._decide(url, bool(ok))
        self.replies.remove(reply)
        reply.deleteLater()
        self.active -= 1
        self.done += 1
        self.progress.emit(self.done, self.total)
        self._pump()


# ----------------------------------------------------------------------------- widgets
class ChannelDelegate(QStyledItemDelegate):
    ROW_H = 58

    def __init__(self, win):
        super().__init__(win)
        self.win = win

    def sizeHint(self, option, index):
        return QSize(260, self.ROW_H)

    def paint(self, p, option, index):
        ch = index.data(Qt.ItemDataRole.UserRole)
        if ch is None:
            return
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = option.rect.adjusted(8, 3, -8, -3)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hover = bool(option.state & QStyle.StateFlag.State_MouseOver)
        playing = self.win.current is not None and self.win.current.url == ch.url

        if selected or hover or playing:
            path = QPainterPath()
            path.addRoundedRect(r.toRectF(), 10, 10)
            p.fillPath(path, QColor(C["surface2"] if selected else C["surface"]))
        if playing:
            p.fillRect(QRect(r.left(), r.top() + 10, 3, r.height() - 20), QColor(C["accent"]))

        # logo
        box = QRect(r.left() + 10, r.top() + 7, 58, r.height() - 14)
        bg = QPainterPath()
        bg.addRoundedRect(box.toRectF(), 7, 7)
        p.fillPath(bg, QColor("#0a0c11"))
        pm = self.win.logos.get(ch.logo)
        if pm:
            s = pm.size().scaled(box.size().shrunkBy(QMargins(4, 4, 4, 4)),
                                 Qt.AspectRatioMode.KeepAspectRatio)
            p.drawPixmap(QRect(box.center().x() - s.width() // 2 + 1, box.center().y() - s.height() // 2 + 1,
                               s.width(), s.height()), pm)
        else:
            p.setPen(QColor(C["muted"]))
            f = QFont(option.font)
            f.setBold(True)
            p.setFont(f)
            initials = "".join(w[0] for w in ch.name.split()[:2]).upper() or "?"
            p.drawText(box, Qt.AlignmentFlag.AlignCenter, initials)

        # textos
        right_w = 46
        tx = box.right() + 12
        name_rect = QRect(tx, r.top() + 8, r.right() - tx - right_w, 20)
        f = QFont(option.font)
        f.setPointSizeF(10.5)
        f.setWeight(QFont.Weight.DemiBold if playing or selected else QFont.Weight.Medium)
        p.setFont(f)
        p.setPen(QColor("white" if playing else C["text"]))
        p.drawText(name_rect, Qt.AlignmentFlag.AlignVCenter,
                   p.fontMetrics().elidedText(ch.name, Qt.TextElideMode.ElideRight, name_rect.width()))

        sub = [x for x in (ch.group, ch.quality, "não 24/7" if ch.not247 else "", "geo" if ch.geo else "") if x]
        f2 = QFont(option.font)
        f2.setPointSizeF(8.5)
        p.setFont(f2)
        p.setPen(QColor(C["muted"]))
        sub_rect = QRect(tx, r.top() + 29, name_rect.width(), 16)
        p.drawText(sub_rect, Qt.AlignmentFlag.AlignVCenter,
                   p.fontMetrics().elidedText("  ·  ".join(sub), Qt.TextElideMode.ElideRight, sub_rect.width()))

        # status + favorito
        st = self.win.status_of(ch.url)
        color = {"ok": C["ok"], "dead": C["bad"], "loading": C["warn"]}.get(st)
        if color:
            p.setBrush(QColor(color))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(r.right() - 16, r.center().y() - 4, 8, 8)
        if self.win.is_fav(ch.url):
            p.setPen(QColor("#ffd166"))
            p.setFont(QFont(option.font.family(), 11))
            p.drawText(QRect(r.right() - 42, r.top(), 20, r.height()), Qt.AlignmentFlag.AlignCenter, "★")
        p.restore()


class VideoFrame(QFrame):
    doubleClicked = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.setMinimumSize(480, 270)
        self.setStyleSheet("background: #000;")
        self.setAttribute(Qt.WidgetAttribute.WA_NativeWindow)
        self.message = "Escolha um canal na lista"

    def mouseDoubleClickEvent(self, e):
        self.doubleClicked.emit()

    def paintEvent(self, e):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("#000"))
        p.setPen(QColor("#4b5468"))
        f = QFont("Segoe UI", 14)
        p.setFont(f)
        p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "📺\n" + self.message)


def app_icon(size=256):
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.scale(size / 64, size / 64)  # desenho definido numa grade de 64x64
    g = QLinearGradient(0, 0, 64, 64)
    g.setColorAt(0, QColor(C["accent"]))
    g.setColorAt(1, QColor(C["accent2"]))
    path = QPainterPath()
    path.addRoundedRect(4, 8, 56, 42, 10, 10)
    p.fillPath(path, g)
    p.setBrush(QColor("white"))
    p.setPen(Qt.PenStyle.NoPen)
    p.drawPolygon([QPointF(26, 19), QPointF(26, 39), QPointF(42, 29)])
    p.fillRect(22, 53, 20, 4, g)
    p.end()
    return QIcon(pm)


# ----------------------------------------------------------------------------- janela
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("IPTV Player")
        self.setWindowIcon(app_icon())
        self.resize(1400, 820)
        self.cfg = Config()
        self.logos = LogoLoader(self)
        self.logos.loaded.connect(self._on_logo)
        self.scanner = Scanner(self)
        self.scanner.result.connect(self._on_scan_result)
        self.scanner.progress.connect(lambda d, t: self.count_lbl.setText(f"Testando… {d}/{t}"))
        self.scanner.finished.connect(self._on_scan_finished)

        self.playlists = {}          # cache de arquivos já lidos
        self.category = None
        self.all_channels = []       # canais da categoria atual
        self.visible = []            # após busca/filtros
        self.current = None
        self.session_status = {}     # url -> ok/loading/dead
        self.fail_streak = 0
        self.fullscreen = False

        self.instance = vlc.Instance(["--no-video-title-show", "--network-caching=1500", "--quiet",
                                      "--sub-source=marq"])
        self.player = self.instance.media_player_new()
        self._build_ui()
        self.player.set_hwnd(int(self.video.winId()))
        self.player.video_set_mouse_input(False)
        self.player.video_set_key_input(False)
        self.player.audio_set_volume(self.cfg["volume"])

        self.monitor = QTimer(self, interval=500, timeout=self._monitor_tick)
        self.monitor.start()
        self.zap_timer = QTimer(self, interval=1000, timeout=self._zap_tick)
        self.zap_left = 0
        self.search_debounce = QTimer(self, singleShot=True, interval=180, timeout=self._apply_filter)

        self._shortcuts()
        self._select_category(self.cfg["last_category"])
        last = next((c for c in self.all_channels if c.url == self.cfg["last_url"]), None)
        if last:
            QTimer.singleShot(400, lambda: self.play(last))

    # ---------------------------------------------------------------- UI
    def _build_ui(self):
        root = QWidget(objectName="root")
        lay = QHBoxLayout(root)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        # barra lateral de categorias
        self.sidebar = QWidget(objectName="sidebar")
        self.sidebar.setFixedWidth(210)
        sl = QVBoxLayout(self.sidebar)
        sl.setContentsMargins(0, 0, 0, 10)
        brand = QLabel("▶ IPTV <span style='color:%s'>Player</span>" % C["accent"], objectName="brand")
        sl.addWidget(brand)
        sub = QLabel("   suas playlists", objectName="muted")
        sl.addWidget(sub)
        sl.addSpacing(8)
        self.nav = QListWidget(objectName="nav")
        self.nav.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        entries = [(FAV_KEY, "Favoritos", "⭐"), (RECENT_KEY, "Recentes", "🕘"), None]
        known = {k for k, _, _ in CATEGORIES}
        for key, label, icon in CATEGORIES:
            if (BASE / f"{key}.m3u").exists():
                entries.append((key, label, icon))
        for f in sorted(BASE.glob("*.m3u")):
            if f.stem not in known:
                entries.append((f.stem, f.stem.capitalize(), "📁"))
        for e in entries:
            if e is None:
                sep = QListWidgetItem("")
                sep.setFlags(Qt.ItemFlag.NoItemFlags)
                sep.setSizeHint(QSize(10, 10))
                self.nav.addItem(sep)
                continue
            it = QListWidgetItem(f"{e[2]}   {e[1]}")
            it.setData(Qt.ItemDataRole.UserRole, e[0])
            self.nav.addItem(it)
        self.nav.currentItemChanged.connect(
            lambda it, _: it and it.data(Qt.ItemDataRole.UserRole) and self._select_category(
                it.data(Qt.ItemDataRole.UserRole), from_nav=True))
        sl.addWidget(self.nav, 1)

        # lista de canais
        self.chan_panel = QWidget(objectName="channels")
        self.chan_panel.setMinimumWidth(300)
        cl = QVBoxLayout(self.chan_panel)
        cl.setContentsMargins(10, 14, 6, 10)
        cl.setSpacing(8)
        self.cat_title = QLabel("", objectName="nowTitle")
        cl.addWidget(self.cat_title)
        self.search = QLineEdit(placeholderText="🔍  Buscar canal…  (Ctrl+F)")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(lambda: self.search_debounce.start())
        cl.addWidget(self.search)
        row = QHBoxLayout()
        self.group_box = QComboBox()
        self.group_box.currentIndexChanged.connect(lambda: self._apply_filter())
        row.addWidget(self.group_box, 1)
        self.hide_dead_cb = QCheckBox("Ocultar offline")
        self.hide_dead_cb.setChecked(self.cfg["hide_dead"])
        self.hide_dead_cb.toggled.connect(lambda v: (self.cfg.__setitem__("hide_dead", v), self._apply_filter()))
        row.addWidget(self.hide_dead_cb)
        cl.addLayout(row)
        self.list = QListWidget()
        self.list.setMouseTracking(True)
        self.list.setUniformItemSizes(True)
        self.list.setItemDelegate(ChannelDelegate(self))
        self.list.setVerticalScrollMode(QListWidget.ScrollMode.ScrollPerPixel)
        self.list.itemClicked.connect(lambda it: self.play(it.data(Qt.ItemDataRole.UserRole)))
        self.list.itemActivated.connect(lambda it: self.play(it.data(Qt.ItemDataRole.UserRole)))
        self.list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._context_menu)
        cl.addWidget(self.list, 1)
        bottom = QHBoxLayout()
        self.count_lbl = QLabel("", objectName="count")
        bottom.addWidget(self.count_lbl, 1)
        self.scan_btn = QPushButton("🩺 Testar lista")
        self.scan_btn.setToolTip("Verifica em segundo plano quais canais desta lista estão no ar")
        self.scan_btn.clicked.connect(self._toggle_scan)
        bottom.addWidget(self.scan_btn)
        cl.addLayout(bottom)

        # player
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(0)
        self.video = VideoFrame()
        self.video.doubleClicked.connect(self.toggle_fullscreen)
        rl.addWidget(self.video, 1)
        self.zap_bar = QProgressBar(textVisible=False)
        self.zap_bar.setVisible(False)
        rl.addWidget(self.zap_bar)
        self.controls = self._build_controls()
        rl.addWidget(self.controls)

        self.splitter = QSplitter()
        self.splitter.addWidget(self.chan_panel)
        self.splitter.addWidget(right)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setSizes([380, 1000])
        self.splitter.setChildrenCollapsible(False)
        lay.addWidget(self.sidebar)
        lay.addWidget(self.splitter, 1)
        self.setCentralWidget(root)

    def _build_controls(self):
        w = QWidget(objectName="controls")
        v = QVBoxLayout(w)
        v.setContentsMargins(16, 10, 16, 12)
        v.setSpacing(8)

        # linha 1: canal atual + transporte + volume
        top = QHBoxLayout()
        self.now_logo = QLabel()
        self.now_logo.setFixedSize(72, 46)
        self.now_logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.now_logo.setStyleSheet(f"background:#0a0c11;border-radius:8px;")
        top.addWidget(self.now_logo)
        info = QVBoxLayout()
        info.setSpacing(0)
        self.now_title = QLabel("Nenhum canal", objectName="nowTitle")
        self.now_status = QLabel("", objectName="muted")
        info.addWidget(self.now_title)
        info.addWidget(self.now_status)
        top.addLayout(info, 1)

        def btn(text, tip, slot, name="round", checkable=False):
            b = QPushButton(text, objectName=name)
            b.setToolTip(tip)
            b.setCheckable(checkable)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(slot)
            return b

        self.fav_btn = btn("☆", "Favoritar (Ctrl+D)", self.toggle_fav_current)
        top.addWidget(self.fav_btn)
        top.addSpacing(8)
        top.addWidget(btn("⏮", "Canal anterior (PgUp)", lambda: self.step(-1)))
        self.play_btn = btn("⏸", "Pausar / continuar", self.toggle_pause, name="play")
        top.addWidget(self.play_btn)
        top.addWidget(btn("⏭", "Próximo canal (PgDn)", lambda: self.step(+1)))
        top.addWidget(btn("⏹", "Parar", self.stop))
        top.addSpacing(14)
        self.mute_btn = btn("🔊", "Mudo (Ctrl+M)", self.toggle_mute)
        top.addWidget(self.mute_btn)
        self.vol = QSlider(Qt.Orientation.Horizontal)
        self.vol.setRange(0, 125)
        self.vol.setFixedWidth(110)
        self.vol.setValue(self.cfg["volume"])
        self.vol.valueChanged.connect(self._set_volume)
        top.addWidget(self.vol)
        top.addSpacing(6)
        top.addWidget(btn("⛶", "Tela cheia (F11 / duplo clique)", self.toggle_fullscreen))
        v.addLayout(top)

        # linha 2: troca automática
        auto = QHBoxLayout()
        auto.setSpacing(10)
        self.skip_cb = QCheckBox("Pular canais offline automaticamente")
        self.skip_cb.setChecked(self.cfg["skip_dead"])
        self.skip_cb.setToolTip("Se o canal não abrir em %ds ou travar, vai para o próximo sozinho" % CONNECT_TIMEOUT)
        self.skip_cb.toggled.connect(lambda val: self.cfg.__setitem__("skip_dead", val))
        auto.addWidget(self.skip_cb)
        auto.addStretch(1)
        self.zap_btn = QPushButton("🔀  Zapping automático")
        self.zap_btn.setCheckable(True)
        self.zap_btn.setToolTip("Troca de canal sozinho a cada intervalo (Ctrl+Z)")
        self.zap_btn.toggled.connect(self._toggle_zap)
        auto.addWidget(self.zap_btn)
        auto.addWidget(QLabel("a cada", objectName="muted"))
        self.zap_spin = QSpinBox()
        self.zap_spin.setRange(5, 3600)
        self.zap_spin.setSuffix(" s")
        self.zap_spin.setValue(self.cfg["zap_interval"])
        self.zap_spin.valueChanged.connect(lambda val: self.cfg.__setitem__("zap_interval", val))
        auto.addWidget(self.zap_spin)
        self.zap_mode = QComboBox()
        self.zap_mode.addItems(["Sequencial", "Aleatório"])
        self.zap_mode.setCurrentIndex(1 if self.cfg["zap_random"] else 0)
        self.zap_mode.currentIndexChanged.connect(lambda i: self.cfg.__setitem__("zap_random", i == 1))
        auto.addWidget(self.zap_mode)
        self.zap_lbl = QLabel("", objectName="muted")
        self.zap_lbl.setMinimumWidth(40)
        auto.addWidget(self.zap_lbl)
        v.addLayout(auto)
        return w

    def _shortcuts(self):
        def sc(keys, fn):
            QShortcut(QKeySequence(keys), self, activated=fn,
                      context=Qt.ShortcutContext.ApplicationShortcut)
        sc("PgDown", lambda: self.step(+1))
        sc("PgUp", lambda: self.step(-1))
        sc("F11", self.toggle_fullscreen)
        sc("Esc", lambda: self.fullscreen and self.toggle_fullscreen())
        sc("Ctrl+F", lambda: (self.search.setFocus(), self.search.selectAll()))
        sc("Ctrl+D", self.toggle_fav_current)
        sc("Ctrl+M", self.toggle_mute)
        sc("Ctrl+Z", self.zap_btn.toggle)

    def keyPressEvent(self, e):
        # em tela cheia a lista está oculta: setas também trocam de canal
        if self.fullscreen and e.key() in (Qt.Key.Key_Up, Qt.Key.Key_Down, Qt.Key.Key_Right, Qt.Key.Key_Left):
            self.step(-1 if e.key() in (Qt.Key.Key_Up, Qt.Key.Key_Left) else +1)
        elif self.fullscreen and e.key() == Qt.Key.Key_Space:
            self.toggle_pause()
        else:
            super().keyPressEvent(e)

    # ---------------------------------------------------------------- estado
    def status_of(self, url):
        if url in self.session_status:
            return self.session_status[url]
        return "dead" if url in self.cfg["dead"] else None

    def _mark(self, url, st):
        self.session_status[url] = st
        if st == "dead":
            self.cfg["dead"][url] = time.time()
        elif st == "ok":
            self.cfg["dead"].pop(url, None)

    def is_fav(self, url):
        return any(f["url"] == url for f in self.cfg["favorites"])

    # ---------------------------------------------------------------- categorias / filtro
    def _select_category(self, key, from_nav=False):
        if not from_nav:
            for i in range(self.nav.count()):
                if self.nav.item(i).data(Qt.ItemDataRole.UserRole) == key:
                    self.nav.blockSignals(True)
                    self.nav.setCurrentRow(i)
                    self.nav.blockSignals(False)
                    break
        self.category = key
        if key == FAV_KEY:
            self.all_channels = [Channel(**f) for f in self.cfg["favorites"]]
        elif key == RECENT_KEY:
            self.all_channels = [Channel(**f) for f in self.cfg["recents"]]
        else:
            if key not in self.playlists:
                path = BASE / f"{key}.m3u"
                self.playlists[key] = parse_m3u(path) if path.exists() else []
            self.all_channels = self.playlists[key]
        if key not in (FAV_KEY, RECENT_KEY):
            self.cfg["last_category"] = key
        label = self.nav.currentItem().text().split("   ", 1)[-1] if self.nav.currentItem() else key
        self.cat_title.setText(label)
        groups = sorted({c.group for c in self.all_channels if c.group})
        self.group_box.blockSignals(True)
        self.group_box.clear()
        self.group_box.addItem("Todos os grupos")
        self.group_box.addItems(groups)
        self.group_box.blockSignals(False)
        self._apply_filter()

    def _apply_filter(self):
        q = self.search.text().strip().lower()
        grp = self.group_box.currentText() if self.group_box.currentIndex() > 0 else None
        hide = self.hide_dead_cb.isChecked()
        self.visible = [c for c in self.all_channels
                        if (not q or q in c.name.lower() or q in c.group.lower())
                        and (not grp or c.group == grp)
                        and not (hide and self.status_of(c.url) == "dead")]
        self.list.setUpdatesEnabled(False)
        self.list.clear()
        for c in self.visible:
            it = QListWidgetItem()
            it.setData(Qt.ItemDataRole.UserRole, c)
            it.setToolTip(f"{c.name}\n{c.group}  {c.quality}")
            self.list.addItem(it)
        self.list.setUpdatesEnabled(True)
        self._select_current_in_list()
        self._update_count()

    def _update_count(self):
        dead = sum(1 for c in self.visible if self.status_of(c.url) == "dead")
        txt = f"{len(self.visible)} canais"
        if dead:
            txt += f"  ·  {dead} offline"
        self.count_lbl.setText(txt)

    def _select_current_in_list(self):
        if not self.current:
            return
        for i, c in enumerate(self.visible):
            if c.url == self.current.url:
                self.list.setCurrentRow(i)
                self.list.scrollToItem(self.list.item(i), QListWidget.ScrollHint.EnsureVisible)
                return

    def _on_logo(self, url):
        self.list.viewport().update()
        if self.current and self.current.logo == url:
            self._update_now_logo()

    # ---------------------------------------------------------------- reprodução
    def play(self, ch, auto=False):
        if ch is None:
            return
        if not auto:
            self.fail_streak = 0
        self.current = ch
        self.retried = False
        media = self.instance.media_new(ch.url)
        for o in ch.opts:
            media.add_option(o)
        self.player.set_media(media)
        self.player.play()
        self.started_at = time.monotonic()
        self.confirmed = False
        self.last_time, self.last_progress = -1, time.monotonic()
        self._mark(ch.url, "loading")
        self.video.message = "Conectando…"
        self.video.update()
        self.cfg["last_url"] = ch.url
        self._add_recent(ch)
        self.now_title.setText(ch.name)
        self._set_status("◌ Conectando…", C["warn"])
        self.fav_btn.setText("★" if self.is_fav(ch.url) else "☆")
        self.play_btn.setText("⏸")
        self._update_now_logo()
        self._select_current_in_list()
        self.list.viewport().update()
        self.zap_left = self.zap_spin.value()

    def _update_now_logo(self):
        pm = self.logos.get(self.current.logo) if self.current else None
        if pm:
            self.now_logo.setPixmap(pm.scaled(66, 40, Qt.AspectRatioMode.KeepAspectRatio,
                                              Qt.TransformationMode.SmoothTransformation))
        else:
            self.now_logo.setPixmap(QPixmap())
            self.now_logo.setText("📺")

    def _set_status(self, text, color):
        extra = []
        if self.current:
            extra = [x for x in (self.current.group, self.current.quality) if x]
        self.now_status.setText(f"<span style='color:{color}'>{text}</span>"
                                + (f"  <span style='color:{C['muted']}'>·  {'  ·  '.join(extra)}</span>" if extra else ""))

    def _show_marquee(self, text):
        p = self.player
        M = vlc.VideoMarqueeOption
        p.video_set_marquee_int(M.Enable, 1)
        p.video_set_marquee_int(M.Size, 30)
        p.video_set_marquee_int(M.Position, 5)  # topo-esquerda
        p.video_set_marquee_int(M.X, 24)
        p.video_set_marquee_int(M.Y, 20)
        p.video_set_marquee_int(M.Opacity, 230)
        p.video_set_marquee_int(M.Timeout, 4000)
        p.video_set_marquee_string(M.Text, text)

    def _monitor_tick(self):
        if not self.current:
            return
        st = self.player.get_state()
        now = time.monotonic()
        if st in (vlc.State.Error, vlc.State.Ended):
            return self._on_fail("sem sinal" if st == vlc.State.Ended else "erro ao abrir")
        if st == vlc.State.Paused:
            self.last_progress = now
            return
        t = self.player.get_time()
        if not self.confirmed:
            if st == vlc.State.Playing and (self.player.has_vout() or t > 800):
                self.confirmed = True
                self.fail_streak = 0
                self.last_time, self.last_progress = t, now
                self._mark(self.current.url, "ok")
                self._set_status("● Ao vivo", C["ok"])
                self.video.message = ""
                self.player.audio_set_volume(self.vol.value())
                idx = next((i for i, c in enumerate(self.visible) if c.url == self.current.url), None)
                self._show_marquee(f"{idx + 1}  {self.current.name}" if idx is not None else self.current.name)
                self.list.viewport().update()
                self._update_count()
            elif now - self.started_at > CONNECT_TIMEOUT:
                self._on_fail("não respondeu")
            return
        if t != self.last_time:
            self.last_time, self.last_progress = t, now
            if self.now_status.text().find("Reconectando") >= 0:
                self._set_status("● Ao vivo", C["ok"])
        elif now - self.last_progress > STALL_TIMEOUT:
            if not self.retried:  # uma tentativa de reconexão antes de desistir
                self.retried = True
                self._set_status("↻ Reconectando…", C["warn"])
                self.player.stop()
                self.player.play()
                self.started_at = now
                self.confirmed = False
            else:
                self._on_fail("travou")

    def _on_fail(self, reason):
        ch = self.current
        self._mark(ch.url, "dead")
        self.list.viewport().update()
        self._update_count()
        self.player.stop()
        self.fail_streak += 1
        if self.skip_cb.isChecked() and self.fail_streak < MAX_CONSECUTIVE_FAILS and len(self.visible) > 1:
            self._set_status(f"✕ Offline ({reason}) — pulando…", C["bad"])
            self.video.message = f"{ch.name} está offline — indo para o próximo…"
            self.video.update()
            self.confirmed = True  # evita reprocessar a falha enquanto espera o pulo
            self.last_progress = float("inf")
            QTimer.singleShot(350, lambda: self.current is ch and self.step(+1, auto=True))
        else:
            if self.fail_streak >= MAX_CONSECUTIVE_FAILS:
                reason += f"; {self.fail_streak} canais seguidos falharam, parei de pular"
            self._set_status(f"✕ Offline ({reason})", C["bad"])
            self.video.message = f"{ch.name} está offline"
            self.video.update()
            self.current = None
            self.list.viewport().update()

    def step(self, delta, auto=False, random_pick=False):
        if not self.visible:
            return
        skip = self.skip_cb.isChecked()
        candidates = [c for c in self.visible if not (skip and self.status_of(c.url) == "dead")
                      or (self.current and c.url == self.current.url)]
        if not candidates:
            self._set_status("✕ Nenhum canal disponível nesta lista", C["bad"])
            return
        if random_pick:
            pool = [c for c in candidates if not self.current or c.url != self.current.url] or candidates
            return self.play(random.choice(pool), auto=auto)
        cur_url = self.current.url if self.current else None
        urls = [c.url for c in self.visible]
        idx = urls.index(cur_url) if cur_url in urls else (-1 if delta > 0 else 0)
        n = len(self.visible)
        for k in range(1, n + 1):
            c = self.visible[(idx + delta * k) % n]
            if c in candidates and c.url != cur_url:
                return self.play(c, auto=auto)

    def toggle_pause(self):
        if not self.current:
            return self.step(+1)
        if self.player.get_state() in (vlc.State.Stopped, vlc.State.Error, vlc.State.Ended, vlc.State.NothingSpecial):
            return self.play(self.current)
        self.player.pause()
        paused = self.player.get_state() != vlc.State.Paused  # estado muda de forma assíncrona
        self.play_btn.setText("▶" if paused else "⏸")

    def stop(self):
        self.player.stop()
        if self.current and self.session_status.get(self.current.url) == "loading":
            self.session_status.pop(self.current.url)
        self.current = None
        self.zap_btn.setChecked(False)
        self.now_title.setText("Nenhum canal")
        self.now_status.setText("")
        self.play_btn.setText("▶")
        self.video.message = "Escolha um canal na lista"
        self.video.update()
        self.list.viewport().update()

    def _set_volume(self, v):
        self.cfg["volume"] = v
        self.player.audio_set_volume(v)
        if v and self.player.audio_get_mute():
            self.player.audio_set_mute(False)
        self.mute_btn.setText("🔇" if v == 0 else "🔊")

    def toggle_mute(self):
        m = not self.player.audio_get_mute()
        self.player.audio_set_mute(m)
        self.mute_btn.setText("🔇" if m else "🔊")

    # ---------------------------------------------------------------- zapping
    def _toggle_zap(self, on):
        self.zap_bar.setVisible(on)
        if on:
            self.zap_left = self.zap_spin.value()
            self.zap_timer.start()
            if not self.current:
                self.step(+1, auto=True, random_pick=self.zap_mode.currentIndex() == 1)
        else:
            self.zap_timer.stop()
            self.zap_lbl.setText("")
        self._zap_render()

    def _zap_tick(self):
        if not self.current or self.player.get_state() == vlc.State.Paused:
            return
        if not self.confirmed:  # só conta tempo enquanto o canal está realmente tocando
            return
        self.zap_left -= 1
        if self.zap_left <= 0:
            self.step(+1, auto=True, random_pick=self.zap_mode.currentIndex() == 1)
        self._zap_render()

    def _zap_render(self):
        total = self.zap_spin.value()
        self.zap_bar.setRange(0, total)
        self.zap_bar.setValue(max(0, total - self.zap_left))
        if self.zap_timer.isActive():
            self.zap_lbl.setText(f"{max(0, self.zap_left)}s")

    # ---------------------------------------------------------------- favoritos / recentes
    def toggle_fav_current(self):
        if self.current:
            self.toggle_fav(self.current)

    def toggle_fav(self, ch):
        favs = self.cfg["favorites"]
        if self.is_fav(ch.url):
            self.cfg["favorites"] = [f for f in favs if f["url"] != ch.url]
        else:
            favs.append(asdict(ch))
        if self.current and self.current.url == ch.url:
            self.fav_btn.setText("★" if self.is_fav(ch.url) else "☆")
        if self.category == FAV_KEY:
            self._select_category(FAV_KEY)
        self.list.viewport().update()
        self.cfg.save()

    def _add_recent(self, ch):
        rec = [r for r in self.cfg["recents"] if r["url"] != ch.url]
        rec.insert(0, asdict(ch))
        self.cfg["recents"] = rec[:40]

    def _context_menu(self, pos):
        it = self.list.itemAt(pos)
        if not it:
            return
        ch = it.data(Qt.ItemDataRole.UserRole)
        m = QMenu(self)
        m.addAction("▶  Assistir", lambda: self.play(ch))
        m.addAction("☆  Remover dos favoritos" if self.is_fav(ch.url) else "★  Adicionar aos favoritos",
                    lambda: self.toggle_fav(ch))
        m.addSeparator()
        m.addAction("📋  Copiar link", lambda: QGuiApplication.clipboard().setText(ch.url))
        if self.status_of(ch.url) == "dead":
            m.addAction("♻  Desmarcar como offline", lambda: (
                self.session_status.pop(ch.url, None), self.cfg["dead"].pop(ch.url, None),
                self.list.viewport().update(), self._update_count()))
        m.exec(self.list.viewport().mapToGlobal(pos))

    # ---------------------------------------------------------------- teste de lista
    def _toggle_scan(self):
        if self.scanner.running:
            self.scanner.stop()
            return
        targets = [c for c in self.visible if self.status_of(c.url) != "ok"]
        if not targets:
            return
        self.scan_btn.setText("⏹ Parar teste")
        self.scanner.start(targets)

    def _on_scan_result(self, url, ok):
        if self.current and self.current.url == url:
            return
        self._mark(url, "ok" if ok else "dead")
        self.list.viewport().update()

    def _on_scan_finished(self):
        self.scan_btn.setText("🩺 Testar lista")
        if self.hide_dead_cb.isChecked():
            self._apply_filter()
        else:
            self._update_count()
        self.cfg.save()

    # ---------------------------------------------------------------- tela cheia
    def toggle_fullscreen(self):
        self.fullscreen = not self.fullscreen
        for w in (self.sidebar, self.chan_panel, self.controls):
            w.setVisible(not self.fullscreen)
        if self.fullscreen:
            self.showFullScreen()
            self.setFocus()
            if self.current:
                self._show_marquee(self.current.name)
        else:
            self.showNormal()

    def closeEvent(self, e):
        self.scanner.stop()
        self.player.stop()
        self.cfg.save()
        super().closeEvent(e)


def main():
    if sys.platform == "win32":
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("iptvplayer.app")
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setStyleSheet(STYLE)
    if vlc is None:
        from PyQt6.QtWidgets import QMessageBox
        QMessageBox.critical(None, "IPTV Player", VLC_MISSING_MSG)
        sys.exit(1)
    w = MainWindow()
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
