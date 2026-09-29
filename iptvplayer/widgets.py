"""Widgets: modelo de canais, desenho em lista/grade, área de vídeo e controles flutuantes."""
from PyQt6.QtCore import QAbstractListModel, QMargins, QModelIndex, QRect, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QPainter, QPainterPath
from PyQt6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QPushButton, QSlider, QStyle, QStyledItemDelegate, QVBoxLayout, QWidget,
)

from .theme import T, VIDEO_TEXT, icon


# ----------------------------------------------------------------------------- botões com ícone
def make_btn(icon_name=None, tip="", slot=None, kind="round", text="", checkable=False,
             icon_color="text", icon_size=None):
    b = QPushButton(text, objectName=kind)
    b.setToolTip(tip)
    b.setCheckable(checkable)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    if slot:
        b.clicked.connect(slot)
    if icon_name:
        set_btn_icon(b, icon_name, icon_color, icon_size)
    return b


def set_btn_icon(b, name, color=None, size=None):
    b.setProperty("icon_name", name)
    if color:
        b.setProperty("icon_color", color)
    if size:
        b.setProperty("icon_size", size)
    sz = b.property("icon_size") or 20
    b.setIcon(icon(name, b.property("icon_color") or "text", sz))
    b.setIconSize(QSize(sz, sz))


def refresh_icons(root):
    for b in root.findChildren(QPushButton):
        if b.property("icon_name"):
            set_btn_icon(b, b.property("icon_name"))


# ----------------------------------------------------------------------------- modelo
class ChannelModel(QAbstractListModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.items = []

    def set_channels(self, items):
        self.beginResetModel()
        self.items = list(items)
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.items)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        ch = self.items[index.row()]
        if role == Qt.ItemDataRole.UserRole:
            return ch
        if role == Qt.ItemDataRole.DisplayRole:
            return ch.name
        if role == Qt.ItemDataRole.ToolTipRole:
            return f"{ch.name}\n{ch.group}  {ch.quality}".strip()
        return None


# ----------------------------------------------------------------------------- desenho dos canais
class _BaseDelegate(QStyledItemDelegate):
    def __init__(self, win):
        super().__init__(win)
        self.win = win

    def _state(self, option, ch):
        return (bool(option.state & QStyle.StateFlag.State_Selected),
                bool(option.state & QStyle.StateFlag.State_MouseOver),
                self.win.current is not None and self.win.current.url == ch.url)

    def _logo(self, p, box, ch, font):
        bg = QPainterPath()
        bg.addRoundedRect(box.toRectF(), 7, 7)
        p.fillPath(bg, QColor(T["logo_bg"]))
        pm = self.win.logos.get(ch.logo)
        if pm:
            s = pm.size().scaled(box.size().shrunkBy(QMargins(5, 5, 5, 5)), Qt.AspectRatioMode.KeepAspectRatio)
            p.drawPixmap(QRect(box.center().x() - s.width() // 2 + 1, box.center().y() - s.height() // 2 + 1,
                               s.width(), s.height()), pm)
        else:
            f = QFont(font)
            f.setBold(True)
            f.setPointSizeF(font.pointSizeF() * (1.6 if box.height() > 60 else 1.0))
            p.setFont(f)
            p.setPen(QColor(T["muted"]))
            p.drawText(box, Qt.AlignmentFlag.AlignCenter, "".join(w[0] for w in ch.name.split()[:2]).upper() or "?")

    def _subtitle(self, ch):
        """Programa atual (EPG) ou grupo/qualidade."""
        cur, _ = self.win.epg.now_next(ch) if self.win.epg.has_data() else (None, None)
        if cur:
            return "▶ " + cur[2], T["accent"]
        parts = [ch.group, ch.quality, "não 24/7" if ch.not247 else "", "geo" if ch.geo else ""]
        return "  ·  ".join(x for x in parts if x), T["muted"]

    def _status_color(self, ch):
        return {"ok": T["ok"], "dead": T["bad"], "loading": T["warn"]}.get(self.win.status_of(ch.url))


class ListDelegate(_BaseDelegate):
    ROW_H = 60

    def sizeHint(self, option, index):
        return QSize(260, self.ROW_H)

    def paint(self, p, option, index):
        ch = index.data(Qt.ItemDataRole.UserRole)
        if ch is None:
            return
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = option.rect.adjusted(6, 3, -8, -3)
        selected, hover, playing = self._state(option, ch)
        if selected or hover or playing:
            path = QPainterPath()
            path.addRoundedRect(r.toRectF(), 10, 10)
            p.fillPath(path, QColor(T["surface2"] if selected else T["surface"]))
        if playing:
            p.fillRect(QRect(r.left(), r.top() + 11, 3, r.height() - 22), QColor(T["accent"]))

        # número do canal
        f = QFont(option.font)
        f.setPointSizeF(8.5)
        p.setFont(f)
        p.setPen(QColor(T["accent"] if playing else T["muted"]))
        num = self.win.numbers.get(ch.url, "")
        p.drawText(QRect(r.left() + 4, r.top(), 30, r.height()), Qt.AlignmentFlag.AlignCenter, str(num))

        box = QRect(r.left() + 36, r.top() + 7, 58, r.height() - 14)
        self._logo(p, box, ch, option.font)

        right_w = 44
        tx = box.right() + 12
        name_rect = QRect(tx, r.top() + 8, r.right() - tx - right_w, 20)
        f = QFont(option.font)
        f.setPointSizeF(10.5)
        f.setWeight(QFont.Weight.DemiBold if playing or selected else QFont.Weight.Medium)
        p.setFont(f)
        p.setPen(QColor(T["strong"] if playing else T["text"]))
        p.drawText(name_rect, Qt.AlignmentFlag.AlignVCenter,
                   p.fontMetrics().elidedText(ch.name, Qt.TextElideMode.ElideRight, name_rect.width()))

        sub, color = self._subtitle(ch)
        f2 = QFont(option.font)
        f2.setPointSizeF(8.5)
        p.setFont(f2)
        p.setPen(QColor(color))
        sub_rect = QRect(tx, r.top() + 30, name_rect.width(), 16)
        p.drawText(sub_rect, Qt.AlignmentFlag.AlignVCenter,
                   p.fontMetrics().elidedText(sub, Qt.TextElideMode.ElideRight, sub_rect.width()))

        color = self._status_color(ch)
        if color:
            p.setBrush(QColor(color))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(r.right() - 16, r.center().y() - 4, 8, 8)
        if self.win.is_fav(ch.url):
            p.drawPixmap(QRect(r.right() - 40, r.center().y() - 8, 16, 16),
                         icon("star_fill", "star", 16).pixmap(16, 16))
        p.restore()


class GridDelegate(_BaseDelegate):
    CARD = QSize(144, 150)

    def sizeHint(self, option, index):
        return self.CARD

    def paint(self, p, option, index):
        ch = index.data(Qt.ItemDataRole.UserRole)
        if ch is None:
            return
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = option.rect.adjusted(4, 4, -4, -4)
        selected, hover, playing = self._state(option, ch)
        card = QPainterPath()
        card.addRoundedRect(r.toRectF(), 12, 12)
        p.fillPath(card, QColor(T["surface2"] if selected or hover else T["surface"]))
        if playing:
            p.setPen(QColor(T["accent"]))
            p.drawPath(card)

        box = QRect(r.left() + 8, r.top() + 8, r.width() - 16, 66)
        self._logo(p, box, ch, option.font)

        f = QFont(option.font)
        f.setPointSizeF(8)
        f.setBold(True)
        p.setFont(f)
        num = str(self.win.numbers.get(ch.url, ""))
        badge = QRect(box.left() + 4, box.top() + 4, max(22, p.fontMetrics().horizontalAdvance(num) + 10), 17)
        bp = QPainterPath()
        bp.addRoundedRect(badge.toRectF(), 8, 8)
        p.fillPath(bp, QColor(T["accent"]) if playing else QColor(0, 0, 0, 160))
        p.setPen(QColor("white"))
        p.drawText(badge, Qt.AlignmentFlag.AlignCenter, num)

        color = self._status_color(ch)
        if color:
            p.setBrush(QColor(color))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(box.right() - 13, box.top() + 7, 9, 9)
        if self.win.is_fav(ch.url):
            p.drawPixmap(QRect(box.right() - 18, box.bottom() - 20, 16, 16),
                         icon("star_fill", "star", 16).pixmap(16, 16))

        name_rect = QRect(r.left() + 9, box.bottom() + 7, r.width() - 18, 20)
        f = QFont(option.font)
        f.setPointSizeF(9.5)
        f.setWeight(QFont.Weight.DemiBold)
        p.setFont(f)
        p.setPen(QColor(T["strong"] if playing else T["text"]))
        p.drawText(name_rect, Qt.AlignmentFlag.AlignVCenter,
                   p.fontMetrics().elidedText(ch.name, Qt.TextElideMode.ElideRight, name_rect.width()))
        sub, color = self._subtitle(ch)
        f2 = QFont(option.font)
        f2.setPointSizeF(8.5)
        p.setFont(f2)
        p.setPen(QColor(color))
        sub_rect = QRect(r.left() + 9, name_rect.bottom() + 1, r.width() - 18, 16)
        p.drawText(sub_rect, Qt.AlignmentFlag.AlignVCenter,
                   p.fontMetrics().elidedText(sub, Qt.TextElideMode.ElideRight, sub_rect.width()))
        p.restore()


# ----------------------------------------------------------------------------- vídeo
class VideoFrame(QFrame):
    doubleClicked = pyqtSignal()

    def __init__(self, min_size=(320, 180)):
        super().__init__()
        self.setMinimumSize(*min_size)
        self.setStyleSheet("background: #000;")
        self.setAttribute(Qt.WidgetAttribute.WA_NativeWindow)
        self.message = "Escolha um canal na lista"

    def mouseDoubleClickEvent(self, e):
        self.doubleClicked.emit()

    def set_message(self, text):
        self.message = text
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("#000"))
        if not self.message:
            return
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        s = 44 if self.height() > 200 else 28
        p.drawPixmap(QRect(self.width() // 2 - s // 2, self.height() // 2 - s - 6, s, s),
                     icon("tv", VIDEO_TEXT, s).pixmap(s, s))
        p.setPen(QColor(VIDEO_TEXT))
        p.setFont(QFont("Segoe UI", 13 if self.height() > 200 else 10))
        p.drawText(self.rect().adjusted(10, self.height() // 2 + 2, -10, 0),
                   Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap,
                   self.message)


# ----------------------------------------------------------------------------- controles da tela cheia
class FullscreenOverlay(QWidget):
    """Barra flutuante sobre o vídeo em tela cheia (janela própria, pois o vídeo do VLC é nativo)."""
    prev = pyqtSignal()
    next = pyqtSignal()
    play_pause = pyqtSignal()
    exit_fs = pyqtSignal()
    mute = pyqtSignal()
    zap_toggled = pyqtSignal(bool)
    captions_toggled = pyqtSignal(bool)
    volume_changed = pyqtSignal(int)

    def __init__(self):
        super().__init__(None, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(22, 14, 22, 14)
        lay.setSpacing(10)
        self.logo = QLabel()
        self.logo.setFixedSize(84, 52)
        self.logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self.logo)
        info = QVBoxLayout()
        info.setSpacing(1)
        self.title = QLabel(objectName="ovTitle")
        self.status = QLabel(objectName="ovMuted")
        self.epg = QLabel(objectName="ovMuted")
        for w in (self.title, self.status, self.epg):
            info.addWidget(w)
        lay.addLayout(info, 1)
        self.prev_btn = make_btn("prev", "Canal anterior", self.prev.emit, icon_color="white")
        self.play_btn = make_btn("pause", "Pausar / continuar", self.play_pause.emit, kind="play",
                                 icon_color="white", icon_size=22)
        self.next_btn = make_btn("next", "Próximo canal", self.next.emit, icon_color="white")
        self.zap_btn = make_btn("shuffle", "Zapping automático", kind="round", checkable=True, icon_color="white")
        self.zap_btn.toggled.connect(self.zap_toggled.emit)
        self.cc_btn = make_btn("cc", "Legendas traduzidas (Ctrl+T)", kind="round", checkable=True, icon_color="white")
        self.cc_btn.toggled.connect(self.captions_toggled.emit)
        self.mute_btn = make_btn("volume", "Mudo", self.mute.emit, icon_color="white")
        self.vol = QSlider(Qt.Orientation.Horizontal)
        self.vol.setRange(0, 125)
        self.vol.setFixedWidth(110)
        self.vol.valueChanged.connect(self.volume_changed.emit)
        self.exit_btn = make_btn("fs_exit", "Sair da tela cheia (Esc)", self.exit_fs.emit, icon_color="white")
        for w in (self.prev_btn, self.play_btn, self.next_btn):
            lay.addWidget(w)
        lay.addSpacing(10)
        for w in (self.cc_btn, self.zap_btn, self.mute_btn, self.vol, self.exit_btn):
            lay.addWidget(w)

    def apply_style(self):
        a1, a2 = T["accent"], T["accent2"]
        grad = f"qlineargradient(x1:0,y1:0,x2:1,y2:1, stop:0 {a1}, stop:1 {a2})"
        self.setStyleSheet(f"""
            QLabel {{ color: white; background: transparent; }}
            QLabel#ovTitle {{ font-size: 15pt; font-weight: 600; }}
            QLabel#ovMuted {{ color: #b8bfcf; font-size: 9.5pt; }}
            QPushButton {{ background: rgba(255,255,255,0.08); border: 1px solid rgba(255,255,255,0.12); }}
            QPushButton:hover {{ background: rgba(255,255,255,0.18); }}
            QPushButton#round {{ border-radius: 19px; min-width: 38px; max-width: 38px;
                min-height: 38px; max-height: 38px; padding: 0; }}
            QPushButton#play {{ border-radius: 24px; min-width: 48px; max-width: 48px; min-height: 48px;
                max-height: 48px; padding: 0; border: none; background: {grad}; }}
            QPushButton:checked {{ background: {grad}; border: none; }}
            QSlider::groove:horizontal {{ height: 4px; background: rgba(255,255,255,0.2); border-radius: 2px; }}
            QSlider::sub-page:horizontal {{ background: {a1}; border-radius: 2px; }}
            QSlider::handle:horizontal {{ background: white; width: 12px; margin: -5px 0; border-radius: 6px; }}
        """)
        refresh_icons(self)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(self.rect().adjusted(1, 1, -1, -1).toRectF(), 18, 18)
        p.fillPath(path, QColor(14, 16, 22, 230))
        p.setPen(QColor(255, 255, 255, 30))
        p.drawPath(path)

    def place(self, screen_geo):
        w = min(1000, screen_geo.width() - 80)
        h = 100
        self.setGeometry(screen_geo.x() + (screen_geo.width() - w) // 2, screen_geo.bottom() - h - 36, w, h)
