"""Tela Início no estilo Netflix: destaque no topo e fileiras horizontais de canais por tema."""
from PyQt6.QtCore import QEvent, QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QComboBox, QFrame, QHBoxLayout, QLabel, QLineEdit, QListView, QScrollArea, QSizePolicy, QVBoxLayout, QWidget,
)

from .theme import icon
from .themes import REGIONS
from .widgets import CardDelegate, ChannelModel, make_btn

ROW_MAX = 40          # cartões por fileira (o resto fica no "Ver tudo")
SEARCH_MAX = 120


class ChannelRow(QWidget):
    """Título + fileira horizontal de cartões, com setas para rolar e "Ver tudo"."""
    picked = pyqtSignal(object, str)     # canal, chave da fileira
    see_all = pyqtSignal(str)

    def __init__(self, win, key, title, wrap=False):
        super().__init__()
        self.key = key
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(2)
        head = QHBoxLayout()
        head.setContentsMargins(4, 0, 0, 0)
        self.title = QLabel(title, objectName="rowTitle")
        head.addWidget(self.title)
        self.count = QLabel("", objectName="muted")
        head.addWidget(self.count)
        head.addStretch(1)
        self.all_btn = make_btn(None, "", lambda: self.see_all.emit(self.key), kind="link", text="Ver tudo  ›")
        head.addWidget(self.all_btn)
        self.left_btn = make_btn("chev_left", "Voltar", lambda: self._scroll(-1), kind="tool", icon_size=16)
        self.right_btn = make_btn("chev_right", "Mais", lambda: self._scroll(+1), kind="tool", icon_size=16)
        head.addWidget(self.left_btn)
        head.addWidget(self.right_btn)
        lay.addLayout(head)
        self.model = ChannelModel(win)
        self.view = QListView()
        self.view.setModel(self.model)
        self.view.setItemDelegate(CardDelegate(win))
        self.view.setViewMode(QListView.ViewMode.IconMode)
        self.view.setMovement(QListView.Movement.Static)
        self.view.setGridSize(CardDelegate.CARD)
        self.view.setUniformItemSizes(True)
        self.view.setMouseTracking(True)
        self.view.setSelectionMode(QListView.SelectionMode.NoSelection)
        self.view.setHorizontalScrollMode(QListView.ScrollMode.ScrollPerPixel)
        self.view.setVerticalScrollMode(QListView.ScrollMode.ScrollPerPixel)
        self.view.setCursor(Qt.CursorShape.PointingHandCursor)
        self.wrap = wrap
        if wrap:   # resultados da busca: grade que quebra linha
            self.view.setResizeMode(QListView.ResizeMode.Adjust)
            self.view.setWrapping(True)
            self.view.setFlow(QListView.Flow.LeftToRight)
            self.view.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            for b in (self.left_btn, self.right_btn, self.all_btn):
                b.hide()
        else:
            self.view.setFlow(QListView.Flow.LeftToRight)
            self.view.setWrapping(False)
            self.view.setFixedHeight(CardDelegate.CARD.height() + 8)
            self.view.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            self.view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.view.clicked.connect(lambda idx: self.picked.emit(idx.data(Qt.ItemDataRole.UserRole), self.key))
        self.view.viewport().installEventFilter(self)
        self.page_bar = None             # barra de rolagem da tela inteira (a rodinha rola a página)
        lay.addWidget(self.view)

    def eventFilter(self, obj, e):
        if e.type() == QEvent.Type.Wheel and self.page_bar and not e.angleDelta().x():
            self.page_bar.setValue(self.page_bar.value() - e.angleDelta().y())
            return True
        return False

    def set_channels(self, channels, total=None):
        self.model.set_channels(channels)
        total = len(channels) if total is None else total
        self.count.setText(f"  {total}" if total > len(channels) else "")
        self.all_btn.setVisible(not self.wrap and total > 0)
        if self.wrap:
            cols = max(1, self.view.viewport().width() // CardDelegate.CARD.width())
            rows = (len(channels) + cols - 1) // cols
            self.view.setFixedHeight(max(1, rows) * CardDelegate.CARD.height() + 12)
        self.setVisible(bool(channels))

    def _scroll(self, direction):
        bar = self.view.horizontalScrollBar()
        step = max(CardDelegate.CARD.width(), self.view.viewport().width() - CardDelegate.CARD.width())
        bar.setValue(bar.value() + direction * step)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if self.wrap and self.model.items:
            self.set_channels(self.model.items)


class HomePage(QWidget):
    """Página inicial. A janela principal fornece os dados (rows) e reage aos sinais."""
    play = pyqtSignal(object, str)       # canal, chave da fileira (vira a categoria para pular canais)
    open_category = pyqtSignal(str)
    region_changed = pyqtSignal(str)
    resume = pyqtSignal()                # voltar ao player
    surprise = pyqtSignal()

    def __init__(self, win):
        super().__init__(objectName="home")
        self.win = win
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        # --- barra de cima
        self.bar = QWidget(objectName="homeBar")
        bl = QHBoxLayout(self.bar)
        bl.setContentsMargins(22, 10, 18, 10)
        bl.setSpacing(10)
        self.brand_icon = QLabel()
        bl.addWidget(self.brand_icon)
        self.brand = QLabel(objectName="brand")
        self.brand.setStyleSheet("padding: 0 12px 0 0;")
        bl.addWidget(self.brand)
        self.search = QLineEdit(placeholderText="Buscar canal, tema ou programa…")
        self.search.setClearButtonEnabled(True)
        self.search.addAction(icon("search", "muted", 16), QLineEdit.ActionPosition.LeadingPosition)
        self.search.setMaximumWidth(420)
        self.search.setMinimumWidth(180)
        self._search_timer = QTimer(self, singleShot=True, interval=220, timeout=self.refresh)
        self.search.textChanged.connect(lambda: self._search_timer.start())
        bl.addWidget(self.search, 1)
        self.region = QComboBox()
        for key, label, _ in REGIONS:
            self.region.addItem(icon("flag", "muted", 16), label, key)
        self.region.setToolTip("De onde são os canais mostrados")
        self.region.currentIndexChanged.connect(lambda: self.region_changed.emit(self.region.currentData()))
        bl.addWidget(self.region)
        bl.addStretch(0)
        self.status = QLabel("", objectName="muted")
        bl.addWidget(self.status)
        self.extra_bar = QHBoxLayout()   # a janela põe aqui o aviso de atualização e os menus
        self.extra_bar.setSpacing(6)
        bl.addLayout(self.extra_bar)
        lay.addWidget(self.bar)

        # --- conteúdo rolável
        self.scroll = QScrollArea(objectName="homeScroll")
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.body = QWidget(objectName="homeBody")
        self.body_lay = QVBoxLayout(self.body)
        self.body_lay.setContentsMargins(26, 18, 26, 26)
        self.body_lay.setSpacing(22)
        self.scroll.setWidget(self.body)
        self.scroll.verticalScrollBar().setSingleStep(40)
        lay.addWidget(self.scroll, 1)

        self.hero = self._build_hero()
        self.body_lay.addWidget(self.hero)
        self.search_row = ChannelRow(win, "busca", "Resultados", wrap=True)
        self.search_row.picked.connect(self.play.emit)
        self.search_row.page_bar = self.scroll.verticalScrollBar()
        self.body_lay.addWidget(self.search_row)
        self.rows = {}
        self._rows_lay = QVBoxLayout()
        self._rows_lay.setSpacing(22)
        self.body_lay.addLayout(self._rows_lay)
        self.empty = QLabel("Carregando os canais…", objectName="heroSub")
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.body_lay.addWidget(self.empty)
        self.body_lay.addStretch(1)

    def _build_hero(self):
        hero = QWidget(objectName="hero")
        hero.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        hero.setMinimumHeight(190)
        hl = QHBoxLayout(hero)
        hl.setContentsMargins(30, 24, 30, 24)
        hl.setSpacing(26)
        self.hero_logo = QLabel()
        self.hero_logo.setFixedSize(220, 130)
        self.hero_logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hl.addWidget(self.hero_logo)
        txt = QVBoxLayout()
        txt.setSpacing(4)
        self.hero_kicker = QLabel("", objectName="heroKicker")
        self.hero_title = QLabel("", objectName="heroTitle")
        self.hero_sub = QLabel("", objectName="heroSub")
        self.hero_sub.setWordWrap(True)
        for w in (self.hero_kicker, self.hero_title, self.hero_sub):
            w.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            txt.addWidget(w)
        txt.addSpacing(10)
        btns = QHBoxLayout()
        btns.setSpacing(10)
        self.hero_play = make_btn("play", "", self._hero_clicked, kind="big", text="  Assistir",
                                  icon_color="white", icon_size=18)
        self.hero_surprise = make_btn("shuffle", "Toca um canal aleatório que está funcionando",
                                      self.surprise.emit, kind="ghost", text="  Surpreenda-me", icon_size=18)
        btns.addWidget(self.hero_play)
        btns.addWidget(self.hero_surprise)
        btns.addStretch(1)
        txt.addLayout(btns)
        hl.addLayout(txt, 1)
        self._hero_ch = None
        self._hero_playing = False
        return hero

    def _hero_clicked(self):
        if self._hero_playing:
            self.resume.emit()
        elif self._hero_ch:
            self.play.emit(self._hero_ch, "__recent")

    def set_hero(self, ch, playing, now_text):
        self._hero_ch, self._hero_playing = ch, playing
        if not ch:
            self.hero_kicker.setText("BEM-VINDO")
            self.hero_title.setText("O que vamos assistir hoje?")
            self.hero_sub.setText("Escolha um canal nas fileiras abaixo ou deixe a sorte escolher.")
            self.hero_logo.clear()
            self.hero_play.hide()
            return
        self.hero_kicker.setText("ASSISTINDO AGORA" if playing else "CONTINUAR ASSISTINDO")
        self.hero_title.setText(ch.name)
        self.hero_sub.setText(now_text or ch.group or "")
        self.hero_play.setText("  Voltar ao canal" if playing else "  Assistir")
        self.hero_play.show()
        pm = self.win.logos.get(ch.logo)
        if pm:
            self.hero_logo.setPixmap(pm.scaled(self.hero_logo.size() - QSize(10, 10),
                                               Qt.AspectRatioMode.KeepAspectRatio,
                                               Qt.TransformationMode.SmoothTransformation))
        else:
            self.hero_logo.setPixmap(icon("tv", "muted", 64).pixmap(64, 64))

    def set_rows(self, rows):
        """rows: [(chave, título, canais, total)] na ordem em que aparecem."""
        keys = [r[0] for r in rows]
        for key in list(self.rows):
            if key not in keys:
                self.rows.pop(key).deleteLater()
        for i, (key, title, chans, total) in enumerate(rows):
            row = self.rows.get(key)
            if row is None:
                row = ChannelRow(self.win, key, title)
                row.picked.connect(self.play.emit)
                row.see_all.connect(self.open_category.emit)
                row.page_bar = self.scroll.verticalScrollBar()
                self.rows[key] = row
            row.title.setText(title)
            self._rows_lay.insertWidget(i, row)
            row.set_channels(chans[:ROW_MAX], total)
        self.empty.setVisible(not any(r[2] for r in rows))

    def set_search_results(self, chans):
        searching = bool(self.search.text().strip())
        self.search_row.title.setText(f"Resultados para “{self.search.text().strip()}”")
        self.search_row.set_channels(chans[:SEARCH_MAX] if searching else [])
        self.search_row.setVisible(searching)
        if searching and not chans:
            self.search_row.setVisible(True)
            self.search_row.title.setText("Nada encontrado — tente outro nome")
        self.hero.setVisible(not searching)
        for row in self.rows.values():
            row.setVisible(not searching and bool(row.model.items))

    def refresh(self):
        self.win.refresh_home()

    def update_views(self):
        for row in list(self.rows.values()) + [self.search_row]:
            row.view.viewport().update()
