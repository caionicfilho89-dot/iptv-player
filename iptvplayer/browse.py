"""Tela principal do PudimFlix: barra lateral com ícones, busca no topo, destaque com capa e fileiras de pôsteres.

Tudo vem do catálogo (catalog.py). A janela principal fornece o catálogo e toca o que for escolhido."""
import random

from PyQt6.QtCore import QAbstractListModel, QEvent, QModelIndex, QObject, QPointF, QRect, QRectF, QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QFontMetrics, QLinearGradient, QPainter, QPainterPath, QPixmap
from PyQt6.QtWidgets import (
    QButtonGroup, QFrame, QHBoxLayout, QLabel, QLineEdit, QListView, QPushButton, QScrollArea, QSizePolicy,
    QStackedWidget, QStyle, QStyledItemDelegate, QVBoxLayout, QWidget,
)

from . import catalog as cat
from .catalog import CatalogNode, display_title, media_is_video
from .net import LogoLoader
from .theme import T, icon, icon_pixmap
from .widgets import make_btn, set_btn_icon

ROW_MAX = 30          # cartões por fileira (o resto fica no "Ver tudo")
HERO_SLIDES = 6
HERO_EVERY_MS = 9000

# seções da barra lateral: chave, rótulo, ícone, caminho no catálogo
SECTIONS = [
    ("home", "Início", "home", None),
    ("live", "Ao vivo", "live", (cat.LIVE,)),
    ("movies", "Filmes", "film", (cat.MOVIES,)),
    ("series", "Séries", "layers", (cat.SERIES,)),
    ("shows", "Programas", "tv", (cat.VIDEOS,)),
    ("radios", "Rádios", "music", (cat.RADIOS,)),
    ("mylist", "Minha lista", "heart", None),
    ("adult", "Adultos", "flag", (cat.ADULT,)),
]
SECTION_TITLE = {cat.LIVE: "Ao vivo", cat.MOVIES: "Filmes", cat.SERIES: "Séries", cat.VIDEOS: "Programas e vídeos",
                 cat.RADIOS: "Rádios", cat.ADULT: "Adultos"}


def _rgba(hex_color, alpha):
    c = QColor(hex_color)
    c.setAlpha(alpha)
    return c


def _fmt_count(n):
    return f"{n:,}".replace(",", ".")


# ----------------------------------------------------------------------------- capas
class PosterLoader(LogoLoader):
    """Capas de filmes e séries: maiores que os logos e com limite de memória (as mais antigas saem)."""
    MAX_ACTIVE = 10
    KEEP = 700

    @staticmethod
    def _scale(pm):
        if pm.width() <= 320 and pm.height() <= 480:
            return pm
        return pm.scaled(320, 480, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)

    def get(self, url):
        url = cat.poster_url(url)
        pm = self.pix.pop(url, None)
        if pm is not None:
            self.pix[url] = pm   # volta para o fim: usada agora
            return pm
        pm = super().get(url)
        while len(self.pix) > self.KEEP:
            self.pix.pop(next(iter(self.pix)))
        return pm


def draw_cover(p, rect, pm, top_bias=0.35):
    """Desenha a imagem cobrindo o retângulo inteiro (corta as sobras, como object-fit: cover)."""
    sw, sh = pm.width(), pm.height()
    if not sw or not sh:
        return
    tr = rect.width() / max(1, rect.height())
    if sw / sh > tr:
        nw = sh * tr
        src = QRectF((sw - nw) / 2, 0, nw, sh)
    else:
        nh = sw / tr
        src = QRectF(0, (sh - nh) * top_bias, sw, nh)
    p.drawPixmap(QRectF(rect), pm, src)


def draw_contain(p, rect, pm, pad=10):
    box = rect.adjusted(pad, pad, -pad, -pad)
    s = pm.size().scaled(box.size(), Qt.AspectRatioMode.KeepAspectRatio)
    p.drawPixmap(QRect(box.center().x() - s.width() // 2 + 1, box.center().y() - s.height() // 2 + 1,
                       s.width(), s.height()), pm)


def blurred(pm, w, h):
    """Desfoque barato para o fundo do destaque: reduz muito e amplia de volta, suavizando."""
    small = pm.scaled(18, 27, Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation)
    return small.scaled(w, h, Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation)


def draw_placeholder(p, r, title, font):
    """Capa desenhada para quem não tem imagem: uma cor viva por título, com o nome em destaque."""
    hue = sum(ord(c) * (i + 1) for i, c in enumerate(title)) % 360
    g = QLinearGradient(QPointF(r.topLeft()), QPointF(r.bottomRight()))
    g.setColorAt(0, QColor.fromHsl(hue, 170, 70))
    g.setColorAt(1, QColor.fromHsl((hue + 50) % 360, 200, 30))
    p.fillRect(r, g)
    shine = QLinearGradient(0, r.top(), 0, r.bottom())
    shine.setColorAt(0, QColor(255, 255, 255, 28))
    shine.setColorAt(0.5, QColor(255, 255, 255, 0))
    shine.setColorAt(1, QColor(0, 0, 0, 110))
    p.fillRect(r, shine)
    p.setOpacity(0.35)
    p.drawPixmap(r.left() + 12, r.top() + 12, icon_pixmap("film", "#ffffff", 20))
    p.setOpacity(1.0)
    f = QFont(font)
    f.setPointSizeF(12 if len(title) < 22 else 10.5)
    f.setBold(True)
    p.setFont(f)
    p.setPen(QColor("white"))
    p.drawText(r.adjusted(12, 12, -12, -16),
               Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom | Qt.TextFlag.TextWordWrap, title)


# ----------------------------------------------------------------------------- modelo dos cartões
class EntryModel(QAbstractListModel):
    """Itens dos cartões: Channel (filme, episódio, canal) ou CatalogNode (uma série/programa inteiro)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.items = []

    def set_items(self, items):
        self.beginResetModel()
        self.items = list(items)
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.items)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        e = self.items[index.row()]
        if role == Qt.ItemDataRole.UserRole:
            return e
        if role == Qt.ItemDataRole.ToolTipRole:
            return e.label if isinstance(e, CatalogNode) else e.name
        return None


class CardDelegate(QStyledItemDelegate):
    """Cartão com capa: pôster (2:3) para filmes e séries, retângulo largo (16:9) para canais ao vivo."""
    POSTER = (136, 204)
    WIDE = (200, 112)
    TEXT_H = 46
    PAD = 7

    def __init__(self, page, wide=False):
        super().__init__(page)
        self.page = page
        self.wide = wide
        self.episodes = False   # dentro de uma série: "Episódio 3" em vez do nome da série

    @classmethod
    def cell(cls, wide):
        w, h = cls.WIDE if wide else cls.POSTER
        return QSize(w + 2 * cls.PAD, h + cls.TEXT_H + 2 * cls.PAD)

    def sizeHint(self, option, index):
        return self.cell(self.wide)

    def paint(self, p, option, index):
        e = index.data(Qt.ItemDataRole.UserRole)
        if e is None:
            return
        page, win = self.page, self.page.win
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        w, h = self.WIDE if self.wide else self.POSTER
        hover = bool(option.state & QStyle.StateFlag.State_MouseOver)
        lift = 3 if hover else 0
        r = QRect(option.rect.x() + (option.rect.width() - w) // 2, option.rect.y() + self.PAD - lift, w, h)
        path = QPainterPath()
        path.addRoundedRect(QRectF(r), 10, 10)
        if hover:  # brilho na cor de destaque
            for grow, alpha in ((7, 22), (4, 45), (2, 80)):
                sh = QPainterPath()
                sh.addRoundedRect(QRectF(r.adjusted(-grow, -grow + 3, grow, grow + 3)), 10 + grow, 10 + grow)
                p.fillPath(sh, _rgba(T["accent"], alpha))

        node = e if isinstance(e, CatalogNode) else None
        ch = page.catalog.first(node) if node else e
        video = node is not None or media_is_video(ch)
        logo = page.catalog.logo_of(e)
        p.setClipPath(path)
        if video and not self.wide:
            pm = page.posters.get(logo)
            if pm:
                draw_cover(p, r, pm)
            else:
                draw_placeholder(p, r, display_title(node.label if node else ch.name), option.font)
        else:
            pm = win.logos.get(logo) if not video else page.posters.get(logo)
            g = QLinearGradient(QPointF(r.topLeft()), QPointF(r.bottomRight()))
            g.setColorAt(0, QColor(T["surface2"]))
            g.setColorAt(1, QColor(T["surface"]))
            p.fillRect(r, g)
            if pm and video:
                draw_cover(p, r, pm, 0.2)
            elif pm:
                draw_contain(p, r, pm, 16 if self.wide else 22)
            elif video:
                path = page.catalog.path_of(ch)
                draw_placeholder(p, r, display_title(path[2] if len(path) > 2 and node is None and
                                                     cat.is_title_level(path[:2]) else ch.name), option.font)
            else:
                self._initials(p, r, ch.name, option.font)
        # selos e progresso
        if not node and not video and self.wide:
            self._badge(p, r.left() + 7, r.top() + 7, "● AO VIVO", option.font, "#e5253b")
        if not node:
            x = r.left() + 7
            for b in cat.badges(ch):
                x += self._badge(p, x, r.top() + 7, b, option.font) + 4
            frac = page.progress_of(ch)
            if frac:
                bar = QRect(r.left(), r.bottom() - 4, w, 5)
                p.fillRect(bar, QColor(0, 0, 0, 150))
                p.fillRect(QRect(bar.left(), bar.top(), int(w * frac), 5), QColor(T["accent"]))
            if win.current is not None and win.current.url == ch.url:
                self._badge(p, r.right() - 70, r.top() + 7, "▶ TOCANDO", option.font, T["accent"])
        if hover:
            p.fillRect(r, QColor(0, 0, 0, 70))
            c = QPointF(r.center())
            circ = QPainterPath()
            circ.addEllipse(c, 21, 21)
            p.fillPath(circ, QColor(T["accent"]))
            tri = QPainterPath()
            tri.moveTo(c.x() - 6, c.y() - 9)
            tri.lineTo(c.x() + 10, c.y())
            tri.lineTo(c.x() - 6, c.y() + 9)
            tri.closeSubpath()
            p.fillPath(tri, QColor("white"))
        p.setClipping(False)
        if hover:
            p.setPen(QColor(T["accent"]))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(QRectF(r).adjusted(0.5, 0.5, -0.5, -0.5), 10, 10)

        # título e linha de baixo
        title, sub = page.episode_text(e) if self.episodes else page.card_text(e)
        f = QFont(option.font)
        f.setPointSizeF(9.2)
        f.setBold(True)
        p.setFont(f)
        p.setPen(QColor(T["strong"] if hover else T["text"]))
        tr = QRect(r.left() + 1, r.bottom() + 6 + lift, w - 2, 18)
        p.drawText(tr, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   QFontMetrics(f).elidedText(title, Qt.TextElideMode.ElideRight, tr.width()))
        if sub:
            f2 = QFont(option.font)
            f2.setPointSizeF(8.3)
            p.setFont(f2)
            p.setPen(QColor(T["accent"] if sub.startswith("▶") else T["muted"]))
            sr = QRect(tr.left(), tr.bottom() + 1, tr.width(), 16)
            p.drawText(sr, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                       QFontMetrics(f2).elidedText(sub, Qt.TextElideMode.ElideRight, sr.width()))
        p.restore()

    def _initials(self, p, r, name, font):
        f = QFont(font)
        f.setPointSizeF(15)
        f.setBold(True)
        p.setFont(f)
        p.setPen(QColor(T["muted"]))
        p.drawText(r, Qt.AlignmentFlag.AlignCenter, "".join(w[0] for w in name.split()[:2]).upper() or "?")

    def _badge(self, p, x, y, text, font, color=None):
        f = QFont(font)
        f.setPointSizeF(7)
        f.setBold(True)
        p.setFont(f)
        w = QFontMetrics(f).horizontalAdvance(text) + 10
        rr = QRectF(x, y, w, 16)
        bg = QPainterPath()
        bg.addRoundedRect(rr, 5, 5)
        p.fillPath(bg, QColor(color) if color else QColor(0, 0, 0, 170))
        p.setPen(QColor("white"))
        p.drawText(rr, Qt.AlignmentFlag.AlignCenter, text)
        return w


class _GridFit(QObject):
    """Estica as colunas da grade para ocupar a largura toda (sem sobra vazia à direita)."""

    def __init__(self, view, base):
        super().__init__(view)
        self.view, self.base = view, base
        view.viewport().installEventFilter(self)

    def eventFilter(self, obj, e):
        if e.type() == QEvent.Type.Resize:
            vw = self.view.viewport().width() - 14   # o Qt quebra a linha antes da borda (e da barra de rolagem)
            cols = max(1, vw // self.base.width())
            size = QSize(vw // cols, self.base.height())
            if size != self.view.gridSize():
                self.view.setGridSize(size)
        return False


def _card_view(page, wide, wrap):
    v = QListView()
    model = EntryModel(v)
    v.setModel(model)
    v.setItemDelegate(CardDelegate(page, wide))
    v.setViewMode(QListView.ViewMode.IconMode)
    v.setMovement(QListView.Movement.Static)
    v.setGridSize(CardDelegate.cell(wide))
    v.setUniformItemSizes(True)
    v.setMouseTracking(True)
    v.setSelectionMode(QListView.SelectionMode.NoSelection)
    v.setHorizontalScrollMode(QListView.ScrollMode.ScrollPerPixel)
    v.setVerticalScrollMode(QListView.ScrollMode.ScrollPerPixel)
    v.setCursor(Qt.CursorShape.PointingHandCursor)
    v.setFrameShape(QFrame.Shape.NoFrame)
    v.setFocusPolicy(Qt.FocusPolicy.NoFocus)   # o foco faria a página rolar sozinha até a fileira
    v.setFlow(QListView.Flow.LeftToRight)
    v.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    if wrap:
        v.setWrapping(True)
        v.setResizeMode(QListView.ResizeMode.Adjust)
        v.setLayoutMode(QListView.LayoutMode.Batched)
        v.setBatchSize(400)
        v._fit = _GridFit(v, CardDelegate.cell(wide))
        v.verticalScrollBar().setSingleStep(36)
    else:
        v.setWrapping(False)
        v.setFixedHeight(CardDelegate.cell(wide).height() + 6)
        v.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    v.clicked.connect(lambda idx: page.activate(idx.data(Qt.ItemDataRole.UserRole), v.property("ctx")))
    return v, model


# ----------------------------------------------------------------------------- fileira
class CardRow(QWidget):
    """Título + fileira horizontal de cartões, com setas e "Ver tudo"."""

    def __init__(self, page, title, wide=False):
        super().__init__()
        self.page = page
        self.wide = wide
        self.see_all_path = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        head = QHBoxLayout()
        head.setContentsMargins(6, 0, 4, 0)
        self.title = QLabel(title, objectName="rowTitle")
        head.addWidget(self.title)
        head.addStretch(1)
        self.all_btn = make_btn(None, "", self._see_all, kind="link", text="Ver tudo  ›")
        head.addWidget(self.all_btn)
        self.left_btn = make_btn("chev_left", "Voltar", lambda: self._scroll(-1), kind="tool", icon_size=16)
        self.right_btn = make_btn("chev_right", "Mais", lambda: self._scroll(+1), kind="tool", icon_size=16)
        head.addWidget(self.left_btn)
        head.addWidget(self.right_btn)
        for b in (self.all_btn, self.left_btn, self.right_btn):
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)   # foco faria a página rolar até a fileira
        lay.addLayout(head)
        self.view, self.model = _card_view(page, wide, wrap=False)
        self.view.viewport().installEventFilter(self)
        lay.addWidget(self.view)
        self.page_bar = None

    def eventFilter(self, obj, e):
        if e.type() == QEvent.Type.Wheel and self.page_bar and not e.angleDelta().x():
            self.page_bar.setValue(self.page_bar.value() - e.angleDelta().y())  # a rodinha rola a página
            return True
        return False

    def set_items(self, items, see_all=None, ctx=None):
        self.model.set_items(items[:ROW_MAX])
        self.view.setProperty("ctx", ctx)
        self.see_all_path = see_all
        self.all_btn.setVisible(see_all is not None)
        self.view.horizontalScrollBar().setValue(0)
        self.setVisible(bool(items))
        self._arrows()

    def _arrows(self):
        fits = self.model.rowCount() * CardDelegate.cell(self.wide).width() <= self.view.viewport().width()
        self.left_btn.setVisible(not fits)
        self.right_btn.setVisible(not fits)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._arrows()

    def _see_all(self):
        if self.see_all_path is not None:
            self.page.open_path(self.see_all_path)

    def _scroll(self, direction):
        bar = self.view.horizontalScrollBar()
        step = max(200, self.view.viewport().width() - CardDelegate.cell(self.wide).width())
        bar.setValue(bar.value() + direction * step)


# ----------------------------------------------------------------------------- destaque
class Hero(QWidget):
    """Banner grande no topo do Início: capa desfocada ao fundo, pôster nítido e botões."""
    HEIGHT = 270

    def __init__(self, page):
        super().__init__()
        self.page = page
        self.setFixedHeight(self.HEIGHT)
        self.slides = []      # [(Channel ou CatalogNode, rótulo de cima)]
        self.index = 0
        self._bg = (None, None)  # (chave, pixmap desfocado)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(40, 30, 40, 28)
        txt = QVBoxLayout()
        txt.setSpacing(6)
        self.kicker = QLabel("", objectName="heroKicker")
        self.title = QLabel("", objectName="heroTitle")
        self.title.setWordWrap(True)
        self.meta = QLabel("", objectName="heroMeta")
        for w in (self.kicker, self.title, self.meta):
            w.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            txt.addWidget(w)
        txt.addStretch(1)
        btns = QHBoxLayout()
        btns.setSpacing(10)
        self.play_btn = make_btn("play", "", self._play, kind="big", text="  Assistir", icon_color="white",
                                 icon_size=18)
        self.more_btn = make_btn("layers", "", self._more, kind="ghost", text="  Ver mais", icon_size=18)
        btns.addWidget(self.play_btn)
        btns.addWidget(self.more_btn)
        btns.addStretch(1)
        txt.addLayout(btns)
        for b in (self.play_btn, self.more_btn):
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.dots = QHBoxLayout()
        self.dots.setSpacing(6)
        txt.addSpacing(8)
        txt.addLayout(self.dots)
        lay.addLayout(txt, 1)
        lay.addSpacing(200)   # lugar do pôster (desenhado no paintEvent)
        self.timer = QTimer(self, interval=HERO_EVERY_MS, timeout=lambda: self.show_slide(self.index + 1))

    def set_slides(self, slides):
        self.slides = slides[:HERO_SLIDES]
        while self.dots.count():
            w = self.dots.takeAt(0).widget()
            if w:
                w.hide()
                w.deleteLater()
        if len(self.slides) > 1:
            for i in range(len(self.slides)):
                d = QPushButton(objectName="dot")
                d.setCheckable(True)
                d.setFocusPolicy(Qt.FocusPolicy.NoFocus)
                d.setCursor(Qt.CursorShape.PointingHandCursor)
                d.clicked.connect(lambda _=False, k=i: (self.show_slide(k), self.timer.start()))
                self.dots.addWidget(d)
            self.dots.addStretch(1)
        self.show_slide(0)
        self.timer.start() if len(self.slides) > 1 else self.timer.stop()

    def current(self):
        return self.slides[self.index] if self.slides else (None, "")

    def show_slide(self, i):
        if not self.slides:
            self.kicker.setText("BEM-VINDO AO PUDIMFLIX")
            self.title.setText("Carregando o catálogo…")
            self.meta.setText("")
            self.play_btn.hide()
            self.more_btn.hide()
            self.update()
            return
        self.index = i % len(self.slides)
        e, kicker = self.slides[self.index]
        page = self.page
        node = e if isinstance(e, CatalogNode) else None
        ch = page.catalog.first(node) if node else e
        self.kicker.setText(kicker.upper())
        self.title.setText(display_title(node.label if node else ch.name))
        self.meta.setText(page.hero_meta(e))
        resume = not node and page.progress_of(ch)
        self.play_btn.setText("  Continuar" if resume else ("  Ver episódios" if node else "  Assistir"))
        self.play_btn.show()
        self.more_btn.setVisible(bool(page.more_path(e)))
        for k in range(self.dots.count()):
            d = self.dots.itemAt(k).widget()
            if d:
                d.setChecked(k == self.index)
        self.update()

    def _play(self):
        e, _ = self.current()
        if e is not None:
            self.page.activate(e, None)

    def _more(self):
        e, _ = self.current()
        path = self.page.more_path(e) if e is not None else None
        if path:
            self.page.open_path(path)

    def poster_rect(self):
        h = self.height() - 40
        w = int(h * 2 / 3)
        return QRect(self.width() - w - 44, 20, w, h)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        clip = QPainterPath()
        clip.addRoundedRect(r, 20, 20)
        p.setClipPath(clip)
        base = QLinearGradient(0, 0, self.width(), self.height())
        base.setColorAt(0, _rgba(T["accent"], 150))
        base.setColorAt(1, _rgba(T["accent2"], 120))
        p.fillRect(self.rect(), base)
        e, _ = self.current()
        pm = None
        if e is not None:
            ch = self.page.catalog.first(e) if isinstance(e, CatalogNode) else e
            logo = self.page.catalog.logo_of(e)
            pm = (self.page.posters.get(logo) if media_is_video(ch) or isinstance(e, CatalogNode)
                  else self.page.win.logos.get(logo))
            if pm is not None:
                key = (logo, self.width(), self.height())
                if self._bg[0] != key:
                    self._bg = (key, blurred(pm, self.width(), self.height()))
                draw_cover(p, self.rect(), self._bg[1], 0.3)
        # escurece da esquerda (onde fica o texto) para a direita
        fade = QLinearGradient(0, 0, self.width(), 0)
        fade.setColorAt(0, _rgba(T["bg"], 238))
        fade.setColorAt(0.42, _rgba(T["bg"], 165))
        fade.setColorAt(1, _rgba(T["bg"], 25))
        p.fillRect(self.rect(), fade)
        bottom = QLinearGradient(0, self.height() * 0.55, 0, self.height())
        bottom.setColorAt(0, _rgba(T["bg"], 0))
        bottom.setColorAt(1, _rgba(T["bg"], 150))
        p.fillRect(self.rect(), bottom)
        glow = QLinearGradient(0, self.height(), self.width() * 0.6, 0)
        glow.setColorAt(0, _rgba(T["accent"], 70))
        glow.setColorAt(1, _rgba(T["accent"], 0))
        p.fillRect(self.rect(), glow)
        if pm is not None:
            pr = self.poster_rect()
            sh = QPainterPath()
            sh.addRoundedRect(QRectF(pr.adjusted(-3, 6, 3, 10)), 14, 14)
            p.fillPath(sh, QColor(0, 0, 0, 120))
            pp = QPainterPath()
            pp.addRoundedRect(QRectF(pr), 12, 12)
            p.save()
            p.setClipPath(pp)
            if isinstance(e, CatalogNode) or media_is_video(e):
                draw_cover(p, pr, pm)
            else:
                p.fillRect(pr, QColor(T["logo_bg"]))
                draw_contain(p, pr, pm, 18)
            p.restore()
        p.setClipping(False)
        p.setPen(QColor(T["border"]))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(r, 20, 20)

    def showEvent(self, e):
        super().showEvent(e)
        if len(self.slides) > 1:
            self.timer.start()

    def hideEvent(self, e):
        super().hideEvent(e)
        self.timer.stop()


# ----------------------------------------------------------------------------- chips (filtros)
class ChipBar(QScrollArea):
    """Fileira de filtros (gêneros, plataformas, países) que rola de lado."""
    chosen = pyqtSignal(object)   # caminho

    def __init__(self):
        super().__init__()
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setFixedHeight(42)
        self.inner = QWidget(objectName="chips")
        self.lay = QHBoxLayout(self.inner)
        self.lay.setContentsMargins(2, 4, 2, 4)
        self.lay.setSpacing(8)
        self.setWidget(self.inner)
        self.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        self.viewport().setAutoFillBackground(False)
        self.inner.setAutoFillBackground(False)
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)

    def set_chips(self, chips, selected):
        """chips: [(rótulo, caminho)]."""
        for b in self.group.buttons():
            self.group.removeButton(b)
            b.hide()
            b.deleteLater()
        while self.lay.count():
            self.lay.takeAt(0)
        for label, path in chips:
            b = QPushButton(label, objectName="chip")
            b.setCheckable(True)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setChecked(tuple(path) == tuple(selected))
            b.clicked.connect(lambda _=False, pth=path: self.chosen.emit(tuple(pth)))
            self.group.addButton(b)
            self.lay.addWidget(b)
        self.lay.addStretch(1)
        self.horizontalScrollBar().setValue(0)

    def wheelEvent(self, e):
        bar = self.horizontalScrollBar()
        bar.setValue(bar.value() - (e.angleDelta().y() or e.angleDelta().x()))


# ----------------------------------------------------------------------------- telas
class SectionView(QWidget):
    """Uma seção (Filmes, Séries, Ao vivo…): filtros em chips e grade de capas com rolagem contínua."""

    def __init__(self, page):
        super().__init__()
        self.page = page
        self.path = ()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 8, 12, 0)
        lay.setSpacing(8)
        head = QHBoxLayout()
        self.title = QLabel("", objectName="h1")
        head.addWidget(self.title)
        self.count = QLabel("", objectName="muted")
        head.addWidget(self.count, 0, Qt.AlignmentFlag.AlignBottom)
        head.addStretch(1)
        lay.addLayout(head)
        self.chips1 = ChipBar()
        self.chips2 = ChipBar()
        for c in (self.chips1, self.chips2):
            c.chosen.connect(self.page.open_path)
            lay.addWidget(c)
        self.grid, self.model = _card_view(page, False, wrap=True)
        self.grid_wide, self.model_wide = _card_view(page, True, wrap=True)
        lay.addWidget(self.grid, 1)
        lay.addWidget(self.grid_wide, 1)

    def show_path(self, path):
        page, catalog = self.page, self.page.catalog
        self.path = path = tuple(path)
        root = catalog.node(path[:1])
        self.title.setText(SECTION_TITLE.get(path[0], path[0]))
        # chips: 1º nível abaixo da seção e, se houver, o 2º (subcategorias, nunca títulos de série)
        level1 = path[:2] if len(path) >= 2 else None
        self._fill(self.chips1, root, level1, "Tudo")
        n1 = catalog.node(level1) if level1 else None
        if n1 and n1.children and not cat.is_title_level(n1.path) and len(n1.children) > 1:
            self._fill(self.chips2, n1, path[:3] if len(path) >= 3 else None, "Tudo")
        else:
            self.chips2.hide()
        node = catalog.node(path)
        if cat.is_title_level(node.path) or path == (cat.SERIES,):
            items = catalog.title_nodes(node)
            noun = "séries" if path[0] == cat.SERIES else "programas"
        else:
            items = [catalog.channels[i] for i in node.indices]
            noun = "canais" if path[0] in (cat.LIVE, cat.RADIOS) else "títulos"
        items = page.allowed(items)
        wide = path[0] in (cat.LIVE, cat.RADIOS) or (path[0] == cat.ADULT and path[1:2] == ("Canais ao vivo",))
        grid, model = (self.grid_wide, self.model_wide) if wide else (self.grid, self.model)
        other = self.grid if wide else self.grid_wide
        other.hide()
        other.model().set_items([])
        grid.show()
        grid.setProperty("ctx", path)
        model.set_items(items)
        grid.verticalScrollBar().setValue(0)
        self.count.setText(f"   {_fmt_count(len(items))} {noun}")

    def _fill(self, bar, node, selected, all_label):
        page, catalog = self.page, self.page.catalog
        if node is None or not node.children:
            bar.hide()
            return
        kids = [k for k in catalog.ordered_children(node, "size") if page.path_allowed(k.path)]
        kids.sort(key=lambda k: k.label != "Brasil")   # Brasil primeiro
        if cat.is_title_level(node.path) or len(kids) < 2:
            bar.hide()
            return
        chips = [(all_label, node.path)] + [(k.label, k.path) for k in kids]
        bar.set_chips(chips, selected if selected else node.path)
        bar.show()


class SeriesView(QWidget):
    """Uma série ou programa: capa, temporadas e episódios."""

    def __init__(self, page):
        super().__init__()
        self.page = page
        self.node = None
        self.season = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 8, 12, 0)
        lay.setSpacing(12)
        top = QHBoxLayout()
        top.setSpacing(24)
        self.poster = QLabel()
        self.poster.setFixedSize(150, 225)
        top.addWidget(self.poster, 0, Qt.AlignmentFlag.AlignTop)
        info = QVBoxLayout()
        info.setSpacing(6)
        self.kicker = QLabel("", objectName="heroKicker")
        self.title = QLabel("", objectName="h1")
        self.title.setWordWrap(True)
        self.meta = QLabel("", objectName="heroMeta")
        for w in (self.kicker, self.title, self.meta):
            info.addWidget(w)
        info.addSpacing(8)
        btns = QHBoxLayout()
        self.play_btn = make_btn("play", "", self._play_main, kind="big", text="  Assistir", icon_color="white",
                                 icon_size=18)
        self.fav_btn = make_btn("heart", "Guardar na Minha lista", self._toggle_fav, kind="ghost",
                                text="  Minha lista", icon_size=18)
        btns.addWidget(self.play_btn)
        btns.addWidget(self.fav_btn)
        btns.addStretch(1)
        info.addLayout(btns)
        info.addStretch(1)
        top.addLayout(info, 1)
        lay.addLayout(top)
        self.seasons = ChipBar()
        self.seasons.chosen.connect(lambda path: self._show_season(path))
        lay.addWidget(self.seasons)
        self.episodes, self.ep_model = _card_view(page, True, wrap=True)
        self.episodes.itemDelegate().episodes = True
        self.episodes.clicked.disconnect()
        self.episodes.clicked.connect(lambda idx: self._play_episode(idx.data(Qt.ItemDataRole.UserRole)))
        lay.addWidget(self.episodes, 1)

    def show_node(self, node):
        page = self.page
        self.node = node
        first = page.catalog.first(node)
        provider = node.path[1] if node.path[0] == cat.SERIES else "Programa de TV"
        seasons = page.catalog.ordered_children(node, "name")
        self.kicker.setText(provider.upper())
        self.title.setText(display_title(node.label))
        n_seasons = sum(1 for s in seasons if s.label.startswith("Temporada"))
        parts = [f"{n_seasons} temporada{'s' if n_seasons != 1 else ''}" if n_seasons else "",
                 f"{node.count} episódio{'s' if node.count != 1 else ''}"] + cat.badges(first)
        self.meta.setText("   ·   ".join(p for p in parts if p))
        self._paint_poster()
        nxt = page.next_episode(node)
        self.play_btn.setText("  Continuar " + self._short(nxt) if nxt and page.progress_of(nxt) else
                              "  Assistir " + self._short(nxt) if nxt else "  Assistir")
        self._fav_state()
        chips = [(s.label.replace("Temporada 0", "Temporada "), s.path) for s in seasons]
        start = page.catalog.path_of(nxt)[:len(node.path) + 1] if nxt else seasons[0].path
        if len(chips) > 1:
            self.seasons.set_chips(chips, start)
            self.seasons.show()
        else:
            self.seasons.hide()
        self._show_season(start)

    def _short(self, ch):
        m = cat.EPISODE_RE.search(ch.name)
        return f"T{int(m.group(1))}:E{int(m.group(2))}" if m else ""

    def _paint_poster(self):
        if self.node is None:
            return
        pm = self.page.posters.get(self.page.catalog.logo_of(self.node))
        out = QPixmap(self.poster.size() * 2)
        out.setDevicePixelRatio(2)
        out.fill(Qt.GlobalColor.transparent)
        p = QPainter(out)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        r = QRect(0, 0, self.poster.width(), self.poster.height())
        path = QPainterPath()
        path.addRoundedRect(QRectF(r), 12, 12)
        p.setClipPath(path)
        if pm:
            draw_cover(p, r, pm)
        else:
            draw_placeholder(p, r, display_title(self.node.label), self.font())
        p.end()
        self.poster.setPixmap(out)

    def _show_season(self, path):
        self.season = tuple(path)
        eps = sorted(self.page.catalog.items(path), key=cat.episode_order)
        self.ep_model.set_items(eps)
        self.episodes.setProperty("ctx", self.season)
        self.episodes.verticalScrollBar().setValue(0)

    def _play_main(self):
        nxt = self.page.next_episode(self.node)
        if nxt is not None:
            self._play_episode(nxt)

    def _play_episode(self, ch):
        self.page.play_episode(self.node, ch)

    def _toggle_fav(self):
        self.page.win.toggle_fav(self.page.catalog.first(self.node))
        self._fav_state()

    def _fav_state(self):
        fav = self.page.win.is_fav(self.page.catalog.first(self.node).url)
        set_btn_icon(self.fav_btn, "heart", "accent" if fav else "text")
        self.fav_btn.setText("  Na Minha lista" if fav else "  Minha lista")


class SearchView(QScrollArea):
    def __init__(self, page):
        super().__init__(objectName="homeScroll")
        self.page = page
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body = QWidget(objectName="homeBody")
        lay = QVBoxLayout(body)
        lay.setContentsMargins(28, 8, 28, 28)
        lay.setSpacing(22)
        self.title = QLabel("", objectName="h1")
        lay.addWidget(self.title)
        self.rows = {}
        for key, label, wide in (("titles", "Séries e programas", False), ("movies", "Filmes", False),
                                 ("live", "Canais ao vivo", True), ("videos", "Vídeos", False)):
            row = CardRow(page, label, wide)
            row.page_bar = self.verticalScrollBar()
            self.rows[key] = row
            lay.addWidget(row)
        self.empty = QLabel("Nada encontrado. Tente outro nome.", objectName="heroSub")
        lay.addWidget(self.empty)
        lay.addStretch(1)
        self.setWidget(body)

    def show_results(self, query, found):
        self.title.setText(f"Resultados para “{query}”")
        total = 0
        for key, row in self.rows.items():
            items = self.page.allowed(found.get(key, []))
            total += len(items)
            row.set_items(items, ctx="busca")
            row.count_text = len(items)
        self.empty.setVisible(total == 0)
        self.verticalScrollBar().setValue(0)


# ----------------------------------------------------------------------------- página
class BrowsePage(QWidget):
    def __init__(self, win):
        super().__init__(objectName="home")
        self.win = win
        self.catalog = None
        self.posters = PosterLoader(self)
        self._repaint = QTimer(self, singleShot=True, interval=80, timeout=self.update_views)
        self.posters.loaded.connect(lambda _u: self._repaint.start())
        self.history = []        # telas anteriores, para o botão Voltar
        self.state = ("home",)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        # --- barra lateral
        self.side = QWidget(objectName="browseSide")
        self.side.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        sl = QVBoxLayout(self.side)
        sl.setContentsMargins(12, 16, 12, 14)
        sl.setSpacing(4)
        brand_row = QHBoxLayout()
        brand_row.setContentsMargins(6, 0, 0, 0)
        self.brand_icon = QLabel()
        brand_row.addWidget(self.brand_icon)
        self.brand = QLabel(objectName="brandBig")
        brand_row.addWidget(self.brand, 1)
        sl.addLayout(brand_row)
        sl.addSpacing(18)
        self.nav_group = QButtonGroup(self)
        self.nav_group.setExclusive(True)
        self.nav_btns = {}
        for key, label, ic, _path in SECTIONS:
            b = make_btn(ic, "", lambda _=False, k=key: self.open_section(k), kind="navBtn",
                         text="   " + label, checkable=True, icon_color="muted", icon_size=19)
            b.setProperty("label", label)
            self.nav_group.addButton(b)
            self.nav_btns[key] = b
            sl.addWidget(b)
        sl.addStretch(1)
        self.now_btn = make_btn("play", "Voltar ao que está tocando", self.win.show_player, kind="nowBtn",
                                text="   Assistindo agora", icon_color="white", icon_size=17)
        self.now_btn.hide()
        sl.addWidget(self.now_btn)
        self.settings_btn = make_btn("settings", "", self.win.open_settings, kind="navBtn",
                                     text="   Configurações", icon_color="muted", icon_size=19)
        self.settings_btn.setProperty("label", "Configurações")
        sl.addWidget(self.settings_btn)
        lay.addWidget(self.side)

        # --- área principal
        main = QWidget()
        ml = QVBoxLayout(main)
        ml.setContentsMargins(0, 0, 0, 0)
        ml.setSpacing(0)
        self.bar = QWidget(objectName="browseBar")
        bl = QHBoxLayout(self.bar)
        bl.setContentsMargins(24, 12, 18, 10)
        bl.setSpacing(10)
        self.back_btn = make_btn("chev_left", "Voltar (Esc)", self.back, kind="tool", icon_size=18)
        self.back_btn.hide()
        bl.addWidget(self.back_btn)
        self.search = QLineEdit(placeholderText="Buscar filmes, séries, canais…", objectName="bigSearch")
        self.search.setClearButtonEnabled(True)
        self.search.addAction(icon("search", "muted", 18), QLineEdit.ActionPosition.LeadingPosition)
        self.search.setMaximumWidth(560)
        self.search.setMinimumWidth(220)
        self._search_timer = QTimer(self, singleShot=True, interval=260, timeout=self._run_search)
        self.search.textChanged.connect(lambda: self._search_timer.start())
        bl.addWidget(self.search, 1)
        bl.addStretch(0)
        self.status = QLabel("", objectName="muted")
        bl.addWidget(self.status)
        self.extra_bar = QHBoxLayout()   # a janela põe aqui o aviso de atualização e o menu
        self.extra_bar.setSpacing(6)
        bl.addLayout(self.extra_bar)
        ml.addWidget(self.bar)

        self.stack = QStackedWidget()
        # Início
        self.home_scroll = QScrollArea(objectName="homeScroll")
        self.home_scroll.setWidgetResizable(True)
        self.home_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.home_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.home_scroll.verticalScrollBar().setSingleStep(40)
        body = QWidget(objectName="homeBody")
        self.home_lay = QVBoxLayout(body)
        self.home_lay.setContentsMargins(24, 6, 24, 30)
        self.home_lay.setSpacing(24)
        self.hero = Hero(self)
        self.home_lay.addWidget(self.hero)
        self.rows_lay = QVBoxLayout()
        self.rows_lay.setSpacing(24)
        self.home_lay.addLayout(self.rows_lay)
        self.home_lay.addStretch(1)
        self.home_scroll.setWidget(body)
        self.rows = []
        self.stack.addWidget(self.home_scroll)
        self.section = SectionView(self)
        self.stack.addWidget(self.section)
        self.series = SeriesView(self)
        self.stack.addWidget(self.series)
        self.results = SearchView(self)
        self.stack.addWidget(self.results)
        ml.addWidget(self.stack, 1)
        lay.addWidget(main, 1)
        self.nav_btns["home"].setChecked(True)
        self.set_compact(False)

    # --- aparência
    def set_compact(self, compact):
        """Janela estreita: barra lateral só com os ícones."""
        self.side.setFixedWidth(76 if compact else 216)
        self.brand.setVisible(not compact)
        for b in list(self.nav_btns.values()) + [self.settings_btn]:
            label = b.property("label")
            b.setText("" if compact else "   " + label)
            b.setToolTip(label if compact else "")
        self.now_btn.setText("" if compact else "   Assistindo agora")

    def update_views(self):
        for v in self.findChildren(QListView):
            if v.isVisible():
                v.viewport().update()
        self.hero.update()
        if self.stack.currentWidget() is self.series:
            self.series._paint_poster()

    # --- dados
    def set_catalog(self, catalog):
        first = self.catalog is None
        self.catalog = catalog
        kids = self.win.cfg["kids"]
        for key, _label, _ic, path in SECTIONS:
            n = catalog.node(path) if (catalog and path) else None
            visible = key in ("home", "mylist") or bool(n and n.count)
            if key == "adult" or (kids and key in ("radios", "live")):
                visible = visible and not kids
            self.nav_btns[key].setVisible(visible)
        self.refresh()
        if first:
            self.home_scroll.verticalScrollBar().setValue(0)

    def allowed(self, items):
        """No modo infantil só passa o que é infantil/família."""
        if not self.win.cfg["kids"]:
            return items
        out = []
        for e in items:
            path = e.path if isinstance(e, CatalogNode) else self.catalog.path_of(e)
            if cat.is_kids(path) and path[0] != cat.ADULT:
                out.append(e)
        return out

    def path_allowed(self, path):
        if not self.win.cfg["kids"]:
            return path[:1] != (cat.UNSTABLE,)
        return path[0] != cat.ADULT and (cat.is_kids(path) or path[0] in (cat.MOVIES, cat.SERIES))

    def progress_of(self, ch):
        """Quanto já foi assistido (0 a 1) do filme/episódio, para a barrinha do cartão."""
        pos = self.win.cfg["resume"].get(ch.url)
        if not pos or pos[0] <= 0 or not pos[1]:
            return 0
        return min(1.0, max(0.03, pos[0] / pos[1]))

    def card_text(self, e):
        if isinstance(e, CatalogNode):
            seasons = sum(1 for k in e.children if k.startswith("Temporada"))
            sub = f"{seasons} temporada{'s' if seasons != 1 else ''}" if seasons > 1 else \
                f"{e.count} episódio{'s' if e.count != 1 else ''}"
            return display_title(e.label), sub
        ch = e
        path = self.catalog.path_of(ch)
        if path[0] == cat.SERIES or path[:2] == cat.TV_SHOWS:
            title = display_title(path[2]) if len(path) > 2 else ch.name
            m = cat.EPISODE_RE.search(ch.name)
            sub = f"T{int(m.group(1))} · E{int(m.group(2))}" if m else ""
            return title, self._left(ch) or sub
        if not media_is_video(ch):
            epg = self.win.epg
            cur = epg.now_next(ch)[0] if epg.has_data() else None
            return ch.name, ("▶ " + cur[2]) if cur else (path[-1] if len(path) > 1 else "")
        year = cat.year_of(ch.name)
        genre = path[1] if len(path) > 1 and path[0] == cat.MOVIES else ""
        return display_title(ch.name), self._left(ch) or "  ·  ".join(str(x) for x in (year or "", genre) if x)

    def episode_text(self, ch):
        m = cat.EPISODE_RE.search(ch.name)
        return cat.episode_label(ch), self._left(ch) or (f"Temporada {int(m.group(1))}" if m else "")

    def _left(self, ch):
        pos = self.win.cfg["resume"].get(ch.url)
        if pos and pos[0] > 0 and pos[1]:
            mins = int((pos[1] - pos[0]) // 60)
            return f"Faltam {mins} min" if mins > 0 else "Quase no fim"
        return ""

    def hero_meta(self, e):
        if isinstance(e, CatalogNode):
            return self.card_text(e)[1] + "   ·   " + (e.path[1] if e.path[0] == cat.SERIES else "Programa de TV")
        path = self.catalog.path_of(e)
        bits = []
        if media_is_video(e):
            year = cat.year_of(e.name)
            bits += [str(year) if year else "", path[1] if len(path) > 1 and path[0] == cat.MOVIES else ""]
            bits += cat.badges(e)
            left = self._left(e)
            if left:
                bits.append(left)
        else:
            bits.append("Ao vivo")
            epg = self.win.epg
            cur = epg.now_next(e)[0] if epg.has_data() else None
            if cur:
                bits.append("Agora: " + cur[2])
        return "   ·   ".join(b for b in bits if b)

    def more_path(self, e):
        if isinstance(e, CatalogNode):
            return e.path[:2]
        path = self.catalog.path_of(e)
        return path[:2] if len(path) >= 2 else None

    def next_episode(self, node):
        """Episódio para continuar: o que está no meio, ou o seguinte ao último assistido, ou o primeiro."""
        eps = sorted(self.catalog.items(node.path), key=cat.episode_order)
        resume = self.win.cfg["resume"]
        best, best_t = None, 0
        for i, ch in enumerate(eps):
            pos = resume.get(ch.url)
            if pos and pos[2] > best_t:
                best_t = pos[2]
                best = ch if pos[0] > 0 else (eps[i + 1] if i + 1 < len(eps) else ch)
        return best or (eps[0] if eps else None)

    # --- Início
    def _home_rows(self):
        catalog, win = self.catalog, self.win
        rows = []   # (título, itens, ver tudo, largo)
        resume = win.cfg["resume"]
        cont, seen_titles = [], set()
        for url, pos in sorted(resume.items(), key=lambda kv: -kv[1][2]):
            i = catalog.by_url.get(url)
            if i is None or pos[0] <= 0:
                continue
            ch = catalog.channels[i]
            path = catalog.path_of(ch)
            if len(path) >= 3 and (path[0] == cat.SERIES or path[:2] == cat.TV_SHOWS):
                if path[:3] in seen_titles:
                    continue
                seen_titles.add(path[:3])
            cont.append(ch)
        rows.append(("Continuar assistindo", cont, None, False))
        favs = [catalog.channels[catalog.by_url[f["url"]]] for f in win.cfg["favorites"] if f.get("url") in catalog.by_url]
        rows.append(("Minha lista", favs, ("__mylist",), False))
        recent_live = []
        for f in win.cfg["recents"]:
            i = catalog.by_url.get(f.get("url"))
            if i is not None and not media_is_video(catalog.channels[i]):
                recent_live.append(catalog.channels[i])
        rows.append(("Canais que você viu", recent_live, None, True))
        br = catalog.node((cat.LIVE, "Brasil"))
        if br:
            rows.append(("Canais do Brasil", [catalog.channels[i] for i in br.indices[:ROW_MAX]], br.path, True))
        movies = catalog.node((cat.MOVIES,))
        if movies:
            launches = catalog.node((cat.MOVIES, "Lançamentos"))
            src_node = launches or movies
            fresh = [catalog.channels[i] for i in src_node.indices[:ROW_MAX * 3] if catalog.channels[i].logo]
            rows.append(("Lançamentos" if launches else "Destaques em filmes", fresh, src_node.path, False))
        series = catalog.node((cat.SERIES,))
        providers = catalog.ordered_children(series, "size")[:7] if series else []
        genres = [g for g in catalog.ordered_children(movies, "size")
                  if g.label not in ("Legendados", "Lançamentos")][:7] if movies else []
        # intercala séries por plataforma e filmes por gênero
        for k in range(max(len(providers), len(genres))):
            if k < len(providers):
                p = providers[k]
                rows.append((f"Séries · {p.label}", catalog.ordered_children(p, "list")[:ROW_MAX], p.path, False))
            if k < len(genres):
                g = genres[k]
                rows.append((f"Filmes · {g.label}", [catalog.channels[i] for i in g.indices[:ROW_MAX]], g.path, False))
        shows = catalog.node(cat.TV_SHOWS)
        if shows:
            rows.append(("Programas de TV", catalog.ordered_children(shows, "list")[:ROW_MAX], shows.path, False))
        for label in ("Esportes gravados", "Documentários", "Shows", "Stand-up"):
            n = catalog.node((cat.VIDEOS, label))
            if n:
                rows.append((label, [catalog.channels[i] for i in n.indices[:ROW_MAX]], n.path, False))
        out = []
        for title, items, see_all, wide in rows:
            # o Início nunca mostra conteúdo adulto (ele fica só na seção Adultos)
            items = [e for e in self.allowed(items)
                     if (e.path if isinstance(e, CatalogNode) else catalog.path_of(e))[0] not in (cat.ADULT, cat.UNSTABLE)]
            if items:
                out.append((title, items, see_all, wide))
        return out

    def _hero_slides(self, rows):
        slides = []
        for title, items, _see, wide in rows:
            if title == "Continuar assistindo" and items:
                slides.append((items[0], "Continuar assistindo"))
        movies = next((r[1] for r in rows if r[0] in ("Lançamentos", "Destaques em filmes")), [])
        pool = [c for c in movies[:60] if self.catalog.logo_of(c)]
        random.shuffle(pool)
        slides += [(c, "Destaque em filmes") for c in pool[:3]]
        series_rows = [r for r in rows if r[0].startswith("Séries · ")]
        random.shuffle(series_rows)
        for title, items, _see, _w in series_rows[:2]:
            if items:
                slides.append((items[random.randrange(min(8, len(items)))], title.replace("Séries · ", "Série · ")))
        return slides

    def refresh(self):
        """Remonta o Início (fileiras e destaque) com o catálogo e o que você assistiu."""
        self.now_btn.setVisible(self.win.current is not None)
        if self.catalog is None:
            self.hero.set_slides([])
            return
        rows = self._home_rows()
        while len(self.rows) < len(rows):
            r = CardRow(self, "")
            r.page_bar = self.home_scroll.verticalScrollBar()
            self.rows.append(r)
            self.rows_lay.addWidget(r)
        for row, (title, items, see_all, wide) in zip(self.rows, rows):
            if row.wide != wide:
                new = CardRow(self, title, wide)
                new.page_bar = self.home_scroll.verticalScrollBar()
                self.rows_lay.replaceWidget(row, new)
                row.deleteLater()
                self.rows[self.rows.index(row)] = row = new
            row.title.setText(title)
            row.set_items(items, see_all=see_all, ctx=see_all if see_all and see_all[0] != "__mylist" else title)
        for row in self.rows[len(rows):]:
            row.hide()
        if self.hero.isVisible() or not self.hero.slides:
            self.hero.set_slides(self._hero_slides(rows))

    def refresh_light(self):
        """Só o que muda ao voltar do player (continuar assistindo, tocando agora)."""
        self.now_btn.setVisible(self.win.current is not None)
        if self.catalog is not None and self.state == ("home",):
            self.refresh()

    # --- navegação
    def _go(self, state, remember=True):
        if remember and state != self.state:
            self.history.append(self.state)
            del self.history[:-30]
        self.state = state
        kind = state[0]
        if kind == "home":
            self.stack.setCurrentWidget(self.home_scroll)
            self.nav_btns["home"].setChecked(True)
        elif kind == "path":
            self.section.show_path(state[1])
            self.stack.setCurrentWidget(self.section)
            self._check_nav(state[1])
        elif kind == "mylist":
            self._show_mylist()
        elif kind == "series":
            self.series.show_node(state[1])
            self.stack.setCurrentWidget(self.series)
            self._check_nav(state[1].path)
        elif kind == "search":
            self.stack.setCurrentWidget(self.results)
        self.back_btn.setVisible(bool(self.history) and kind != "home")

    def _check_nav(self, path):
        for key, _l, _i, p in SECTIONS:
            if p and tuple(path[:1]) == p:
                self.nav_btns[key].setChecked(True)

    def open_section(self, key):
        self._clear_search()
        if key == "home":
            self.history.clear()
            self._go(("home",), remember=False)
            self.home_scroll.verticalScrollBar().setValue(0)
            self.refresh()
            return
        if key == "mylist":
            return self._go(("mylist",))
        path = dict((k, p) for k, _l, _i, p in SECTIONS)[key]
        self._go(("path", path))

    def open_path(self, path):
        if self.catalog is None:
            return
        path = tuple(path)
        if path == ("__mylist",):
            return self._go(("mylist",))
        node = self.catalog.node(path)
        if node is None:
            return
        self._clear_search()
        if len(path) >= 3 and cat.is_title_level(path[:2]):
            return self._go(("series", self.catalog.node(path[:3])))
        self._go(("path", path))

    def _show_mylist(self):
        favs = [f for f in self.win.cfg["favorites"]]
        items = [self.catalog.channels[self.catalog.by_url[f["url"]]] for f in favs
                 if self.catalog and f.get("url") in self.catalog.by_url]
        sec = self.section
        sec.title.setText("Minha lista")
        sec.count.setText(f"   {len(items)} itens" if items else "   Use o coração ♥ para guardar aqui")
        sec.chips1.hide()
        sec.chips2.hide()
        sec.grid_wide.hide()
        sec.grid.show()
        sec.grid.setProperty("ctx", "Minha lista")
        sec.model.set_items(self.allowed(items))
        self.stack.setCurrentWidget(sec)
        self.nav_btns["mylist"].setChecked(True)

    def back(self):
        if not self.history:
            return False
        self._go(self.history.pop(), remember=False)
        if self.state == ("home",):
            self.refresh()
        return True

    def _clear_search(self):
        if self.search.text():
            self.search.blockSignals(True)
            self.search.clear()
            self.search.blockSignals(False)

    def _run_search(self):
        q = self.search.text().strip()
        if not q:
            if self.state[0] == "search":
                self.back()
            return
        if self.catalog is None:
            return
        self.results.show_results(q, self.catalog.search(q))
        self._go(("search", q), remember=self.state[0] != "search")

    # --- tocar
    def activate(self, e, ctx):
        """Cartão clicado: série abre os episódios; filme/canal toca com a pasta dele como lista do player."""
        if e is None or self.catalog is None:
            return
        if isinstance(e, CatalogNode):
            return self._go(("series", e))
        path = self.catalog.path_of(e)
        if len(path) >= 3 and (path[0] == cat.SERIES or path[:2] == cat.TV_SHOWS):
            node = self.catalog.node(path[:3])
            if ctx in ("Continuar assistindo",):
                return self.play_episode(node, e)
            return self._go(("series", node))
        folder = ctx if isinstance(ctx, tuple) and self.catalog.node(ctx) else path
        self.win.play_catalog(e, folder)

    def play_episode(self, node, ch):
        eps = sorted(self.catalog.items(node.path), key=cat.episode_order)
        self.win.play_episode(ch, eps, display_title(node.label))

    def key_back(self):
        """Esc no Início: limpa a busca ou volta uma tela; devolve True se fez algo."""
        if self.search.text():
            self.search.clear()
            return True
        return self.back()
