"""Widgets: modelo de canais, desenho em lista/grade, área de vídeo e controles flutuantes."""
from PyQt6.QtCore import QAbstractListModel, QMargins, QModelIndex, QRect, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QPainter, QPainterPath
from PyQt6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QSlider, QStyle, QStyledItemDelegate, QVBoxLayout, QWidget,
)

from .theme import T, VIDEO_TEXT, icon


# ----------------------------------------------------------------------------- botões com ícone
class SeekSlider(QSlider):
    """Barra de tempo: clicar em qualquer ponto pula direto para ele (o QSlider pularia aos poucos)."""

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and self.maximum() > self.minimum():
            x = e.position().x() / max(1, self.width())
            self.setValue(self.minimum() + round(x * (self.maximum() - self.minimum())))
            self.setSliderDown(True)  # emite sliderPressed
            e.accept()
            return
        super().mousePressEvent(e)

    def mouseReleaseEvent(self, e):
        super().mouseReleaseEvent(e)
        if self.isSliderDown():
            self.setSliderDown(False)  # emite sliderReleased


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
            tip = f"{ch.name}\n{ch.group}  {ch.quality}".strip()
            health = getattr(self.parent(), "health", None)
            extra = health.describe(ch.url) if health else ""
            return tip + (f"\n{extra}" if extra else "")
        return None


# ----------------------------------------------------------------------------- desenho dos canais
_dark_cache = {}


def _is_dark(url, pm):
    """Logo quase todo preto (A&E, AMC…): some no fundo escuro, então ganha fundo claro."""
    v = _dark_cache.get(url)
    if v is None:
        img = pm.toImage().scaled(16, 16)
        lum = n = 0
        for y in range(img.height()):
            for x in range(img.width()):
                c = img.pixelColor(x, y)
                if c.alpha() > 100:
                    lum += 0.3 * c.red() + 0.59 * c.green() + 0.11 * c.blue()
                    n += 1
        v = _dark_cache[url] = n > 20 and lum / n < 55
    return v
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
        pm = self.win.logos.get(ch.logo)
        dark_logo = pm is not None and T["mode"] == "dark" and _is_dark(ch.logo, pm)
        p.fillPath(bg, QColor("#d9dde6" if dark_logo else T["logo_bg"]))
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
        """Programa encontrado pela busca, programa atual (EPG) ou grupo/qualidade."""
        hit = self.win.epg_hits.get(ch.url)
        if hit:
            return hit, T["accent"]
        cur, _ = self.win.epg.now_next(ch) if self.win.epg.has_data() else (None, None)
        if cur:
            return "▶ " + cur[2], T["accent"]
        parts = [ch.group, ch.quality, "não 24/7" if ch.not247 else "", "geo" if ch.geo else ""]
        return "  ·  ".join(x for x in parts if x), T["muted"]

    def _new_badge(self, p, x, y, font, right=False):
        """Selo "NOVO"; devolve a largura ocupada."""
        f = QFont(font)
        f.setPointSizeF(7)
        f.setBold(True)
        p.setFont(f)
        w = p.fontMetrics().horizontalAdvance("NOVO") + 10
        r = QRect(x - w if right else x, y, w, 15)
        path = QPainterPath()
        path.addRoundedRect(r.toRectF(), 7, 7)
        p.fillPath(path, QColor(T["accent"]))
        p.setPen(QColor("white"))
        p.drawText(r, Qt.AlignmentFlag.AlignCenter, "NOVO")
        return w

    def _signal(self, p, x, y, ch):
        """Barrinhas de sinal: quanto o canal costuma funcionar (nada quando ainda não há dados)."""
        level = self.win.health.level(ch.url)
        if not level:
            return
        color = QColor({3: T["ok"], 2: T["warn"], 1: T["bad"]}[level])
        dim = QColor(T["border_hover"])
        p.setPen(Qt.PenStyle.NoPen)
        for i in range(3):
            h = 4 + 3 * i
            p.setBrush(color if i < level else dim)
            p.drawRoundedRect(x + i * 4, y + 10 - h, 3, h, 1, 1)

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
        if self.win.is_new(ch.url):
            right_w += self._new_badge(p, r.right() - 46, r.top() + 10, option.font, right=True) + 6
        if self.win.health.level(ch.url):
            self._signal(p, r.right() - right_w - 10, r.top() + 11, ch)
            right_w += 18
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
        if self.win.is_new(ch.url):
            self._new_badge(p, box.left() + 4, box.bottom() - 19, option.font)

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


class CardDelegate(_BaseDelegate):
    """Cartão grande das fileiras da tela Início: logo em destaque, nome e o que está passando agora."""
    CARD = QSize(208, 176)

    def sizeHint(self, option, index):
        return self.CARD

    def paint(self, p, option, index):
        ch = index.data(Qt.ItemDataRole.UserRole)
        if ch is None:
            return
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = option.rect.adjusted(6, 6, -6, -6)
        _selected, hover, playing = self._state(option, ch)
        thumb = QRect(r.left(), r.top(), r.width(), 112)
        path = QPainterPath()
        path.addRoundedRect(thumb.toRectF(), 12, 12)
        p.fillPath(path, QColor(T["surface2"] if hover else T["surface"]))
        self._logo(p, thumb.adjusted(22, 14, -22, -14), ch, option.font)
        if hover or playing:
            pen = QColor(T["accent"])
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPath(path)
        if playing:
            self._new_badge_text(p, thumb.left() + 8, thumb.top() + 8, option.font, "AO VIVO")
        elif self.win.is_new(ch.url):
            self._new_badge(p, thumb.left() + 8, thumb.top() + 8, option.font)
        if self.win.is_fav(ch.url):
            p.drawPixmap(QRect(thumb.right() - 24, thumb.top() + 8, 16, 16),
                         icon("star_fill", "star", 16).pixmap(16, 16))
        self._signal(p, thumb.right() - 20, thumb.bottom() - 18, ch)

        name_rect = QRect(r.left() + 2, thumb.bottom() + 8, r.width() - 4, 20)
        f = QFont(option.font)
        f.setPointSizeF(10)
        f.setWeight(QFont.Weight.DemiBold)
        p.setFont(f)
        p.setPen(QColor(T["strong"] if hover or playing else T["text"]))
        p.drawText(name_rect, Qt.AlignmentFlag.AlignVCenter,
                   p.fontMetrics().elidedText(ch.name, Qt.TextElideMode.ElideRight, name_rect.width()))
        sub, color = self._subtitle(ch)
        f2 = QFont(option.font)
        f2.setPointSizeF(8.5)
        p.setFont(f2)
        p.setPen(QColor(color))
        sub_rect = QRect(r.left() + 2, name_rect.bottom() + 1, r.width() - 4, 16)
        p.drawText(sub_rect, Qt.AlignmentFlag.AlignVCenter,
                   p.fontMetrics().elidedText(sub, Qt.TextElideMode.ElideRight, sub_rect.width()))
        p.restore()

    def _new_badge_text(self, p, x, y, font, text):
        f = QFont(font)
        f.setPointSizeF(7)
        f.setBold(True)
        p.setFont(f)
        w = p.fontMetrics().horizontalAdvance(text) + 12
        rect = QRect(x, y, w, 16)
        path = QPainterPath()
        path.addRoundedRect(rect.toRectF(), 8, 8)
        p.fillPath(path, QColor(T["bad"]))
        p.setPen(QColor("white"))
        p.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)


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
    back = pyqtSignal()
    forward = pyqtSignal()

    def __init__(self):
        super().__init__(None, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(22, 12, 22, 14)
        outer.setSpacing(6)
        # barra de tempo (filmes, episódios e canais com "voltar"): só aparece quando o player também tem
        self.seek_row = QWidget()
        sr = QHBoxLayout(self.seek_row)
        sr.setContentsMargins(0, 0, 0, 0)
        self.seek_pos = QLabel(objectName="ovMuted")
        self.seek = SeekSlider(Qt.Orientation.Horizontal)
        self.seek.setObjectName("ovSeek")
        self.seek_end = QLabel(objectName="ovMuted")
        sr.addWidget(self.seek_pos)
        sr.addWidget(self.seek, 1)
        sr.addWidget(self.seek_end)
        self.seek_row.hide()
        outer.addWidget(self.seek_row)
        lay = QHBoxLayout()
        lay.setSpacing(10)
        outer.addLayout(lay)
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
            w.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)  # texto longo é cortado,
            info.addWidget(w)                                                           # não estica a barra
        lay.addLayout(info, 1)
        self.prev_btn = make_btn("prev", "Canal anterior", self.prev.emit, icon_color="white")
        self.play_btn = make_btn("pause", "Pausar / continuar", self.play_pause.emit, kind="play",
                                 icon_color="white", icon_size=22)
        self.next_btn = make_btn("next", "Próximo canal", self.next.emit, icon_color="white")
        self.back_btn = make_btn("back", "Voltar 30 segundos", self.back.emit, icon_color="white")
        self.fwd_btn = make_btn("forward", "Avançar 30 segundos", self.forward.emit, icon_color="white")
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
        for w in (self.prev_btn, self.back_btn, self.play_btn, self.fwd_btn, self.next_btn):
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

    def set_seek(self, visible, lo, hi, value, pos_text, end_text):
        self.seek_row.setVisible(visible)
        self.back_btn.setVisible(visible)
        self.fwd_btn.setVisible(visible)
        if visible:
            self.seek.blockSignals(True)
            self.seek.setRange(lo, hi)
            self.seek.setValue(value)
            self.seek.blockSignals(False)
            self.seek_pos.setText(pos_text)
            self.seek_end.setText(end_text)

    def place(self, screen_geo):
        w = min(1100, screen_geo.width() - 80)
        h = 140 if self.seek_row.isVisibleTo(self) else 100
        self.setGeometry(screen_geo.x() + (screen_geo.width() - w) // 2, screen_geo.bottom() - h - 36, w, h)
