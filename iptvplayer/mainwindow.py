"""Janela principal do IPTV Player."""
import json
import math
import random
import re
import time

from PyQt6.QtCore import QEvent, QPoint, QSize, Qt, QTimer, QUrl
from PyQt6.QtGui import QColor, QCursor, QDesktopServices, QFont, QGuiApplication, QKeySequence, QShortcut
from PyQt6.QtNetwork import QNetworkAccessManager, QNetworkReply
from PyQt6.QtWidgets import (
    QAbstractSpinBox, QApplication, QCheckBox, QComboBox, QHBoxLayout, QLabel, QLineEdit, QListView,
    QListWidget, QListWidgetItem, QMainWindow, QMenu, QMessageBox, QPlainTextEdit, QProgressBar, QSizePolicy,
    QSlider, QSpinBox, QSplitter, QVBoxLayout, QWidget,
)

from . import APP_NAME, APP_VERSION, REPO
from . import sources as src
from .config import Config
from .dialogs import AddListDialog, GuideDialog, SettingsDialog
from .epg import EpgManager
from .m3u import Channel, header_epg_urls, parse_m3u
from .mosaic import MosaicWindow
from .net import ListDownloader, LogoLoader, Scanner, make_request
from .pip import PipWindow
from .recorder import Recorder, snapshot_path
from .theme import T, app_icon, apply_theme, build_style, icon
from .vlcload import vlc
from .widgets import (
    ChannelModel, FullscreenOverlay, GridDelegate, ListDelegate, VideoFrame, make_btn, refresh_icons,
    set_btn_icon,
)

CONNECT_TIMEOUT = 15         # segundos para o canal começar a tocar
STALL_TIMEOUT = 12           # segundos sem avançar = travado
MAX_CONSECUTIVE_FAILS = 25   # evita loop infinito pulando canais
LIST_MAX_AGE = 7 * 86400     # atualização automática das listas
UPDATE_CHECK_EVERY = 12 * 3600


def hm(ts):
    return time.strftime("%H:%M", time.localtime(ts))


def version_tuple(s):
    return tuple(int(x) for x in re.findall(r"\d+", s or "")[:3]) or (0,)


class MainWindow(QMainWindow):
    def __init__(self, cfg=None):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(app_icon())
        self.resize(1440, 860)
        self.cfg = cfg or Config()

        self.logos = LogoLoader(self)
        self.logos.loaded.connect(self._on_logo)
        self.scanner = Scanner(self)
        self.scanner.result.connect(self._on_scan_result)
        self.scanner.progress.connect(lambda d, t: self.count_lbl.setText(f"Testando… {d}/{t}"))
        self.scanner.finished.connect(self._on_scan_finished)
        self.downloader = ListDownloader(self)
        self.downloader.one_done.connect(self._on_list_done)
        self.downloader.progress.connect(lambda d, t: self._side_status(f"Atualizando listas… {d}/{t}"))
        self.downloader.finished.connect(self._on_lists_finished)
        self.epg = EpgManager(self)
        self.epg.updated.connect(self._on_epg_updated)
        self.epg.status.connect(self._side_status)
        self.nam = QNetworkAccessManager(self)

        self.playlists = {}          # chave -> (canais, cabeçalho)
        self.category = None
        self.all_channels, self.visible, self.numbers = [], [], {}
        self.current = None
        self.session_status = {}     # url -> ok/loading/dead
        self.fail_streak = 0
        self.fullscreen = False
        self.confirmed = False
        self.started_at = 0.0
        self._status = ("", "muted")
        self._epg_urls_loaded = set()
        self._dl_all = False
        self.pip = None
        self.mosaic = None
        self._mosaic_prev = None
        self.num_buffer = ""
        self.sleep_deadline = None
        self._sleep_warned = False
        self._last_mouse = QPoint()
        self._overlay_until = 0.0

        self.instance = vlc.Instance(["--no-video-title-show", "--network-caching=1500", "--quiet",
                                      "--sub-source=marq"])
        self.player = self.instance.media_player_new()
        self.recorder = Recorder(self.instance)
        self._build_ui()
        self.overlay = FullscreenOverlay()
        self._wire_overlay()
        self.player.set_hwnd(int(self.video.winId()))
        self.player.video_set_mouse_input(False)
        self.player.video_set_key_input(False)
        self.player.audio_set_volume(self.cfg["volume"])

        self.monitor = QTimer(self, interval=500, timeout=self._monitor_tick)
        self.monitor.start()
        self.clock = QTimer(self, interval=1000, timeout=self._clock_tick)
        self.clock.start()
        self.fs_timer = QTimer(self, interval=200, timeout=self._fs_mouse_tick)
        self.num_timer = QTimer(self, singleShot=True, interval=1600, timeout=self._commit_number)
        self.search_debounce = QTimer(self, singleShot=True, interval=180, timeout=self._apply_filter)
        self.zap_left = 0

        self._shortcuts()
        QApplication.instance().installEventFilter(self)
        self._set_view_mode(self.cfg["view_mode"])
        self._select_category(self.cfg["last_category"])
        last = next((c for c in self.all_channels if c.url == self.cfg["last_url"]), None)
        if last:
            QTimer.singleShot(400, lambda: self.play(last))
        QTimer.singleShot(1500, lambda: self._reload_epg())
        QTimer.singleShot(4000, self._auto_update_lists)
        QTimer.singleShot(3000, self._check_updates)

    # ================================================================ interface
    def _build_ui(self):
        root = QWidget(objectName="root")
        lay = QHBoxLayout(root)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        # --- barra lateral
        self.sidebar = QWidget(objectName="sidebar")
        self.sidebar.setFixedWidth(224)
        sl = QVBoxLayout(self.sidebar)
        sl.setContentsMargins(0, 0, 0, 10)
        sl.setSpacing(2)
        self.brand = QLabel(objectName="brand")
        sl.addWidget(self.brand)
        sl.addSpacing(6)
        self.nav = QListWidget(objectName="nav")
        self.nav.setIconSize(QSize(18, 18))
        self.nav.currentItemChanged.connect(self._nav_changed)
        self.nav.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.nav.customContextMenuRequested.connect(self._nav_menu)
        sl.addWidget(self.nav, 1)
        self.update_banner = make_btn("download", "", self._open_update_page, kind="banner", icon_color="white",
                                      icon_size=16)
        self.update_banner.hide()
        sl.addWidget(self.update_banner)
        for name, text, slot in (("plus", "Adicionar lista", self.add_list),
                                 ("refresh", "Atualizar canais", lambda: self.update_lists()),
                                 ("settings", "Configurações", self.open_settings)):
            b = make_btn(name, "", slot, kind="flat", text="  " + text, icon_color="muted", icon_size=17)
            wrap = QHBoxLayout()
            wrap.setContentsMargins(8, 0, 8, 0)
            wrap.addWidget(b)
            sl.addLayout(wrap)
            if name == "refresh":
                self.upd_btn = b
        self.side_lbl = QLabel("", objectName="muted")
        self.side_lbl.setWordWrap(True)
        self.side_lbl.setContentsMargins(18, 2, 12, 0)
        sl.addWidget(self.side_lbl)

        # --- lista de canais
        self.chan_panel = QWidget(objectName="channels")
        self.chan_panel.setMinimumWidth(320)
        cl = QVBoxLayout(self.chan_panel)
        cl.setContentsMargins(10, 14, 6, 10)
        cl.setSpacing(8)
        head = QHBoxLayout()
        self.cat_title = QLabel("", objectName="nowTitle")
        head.addWidget(self.cat_title, 1)
        self.view_btn = make_btn("grid", "Ver em grade", self._toggle_view_mode, kind="tool", icon_size=18)
        head.addWidget(self.view_btn)
        cl.addLayout(head)
        self.search = QLineEdit(placeholderText="Buscar canal…  (Ctrl+F)")
        self.search.setClearButtonEnabled(True)
        self._search_action = self.search.addAction(icon("search", "muted", 16),
                                                    QLineEdit.ActionPosition.LeadingPosition)
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
        self.model = ChannelModel(self)
        self.view = QListView()
        self.view.setModel(self.model)
        self.view.setMouseTracking(True)
        self.view.setUniformItemSizes(True)
        self.view.setVerticalScrollMode(QListView.ScrollMode.ScrollPerPixel)
        self.view.verticalScrollBar().setSingleStep(24)
        self.list_delegate = ListDelegate(self)
        self.grid_delegate = GridDelegate(self)
        self.view.clicked.connect(lambda idx: self.play(idx.data(Qt.ItemDataRole.UserRole)))
        self.view.activated.connect(lambda idx: self.play(idx.data(Qt.ItemDataRole.UserRole)))
        self.view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.view.customContextMenuRequested.connect(self._context_menu)
        cl.addWidget(self.view, 1)
        bottom = QHBoxLayout()
        self.count_lbl = QLabel("", objectName="count")
        bottom.addWidget(self.count_lbl, 1)
        self.scan_btn = make_btn("check", "Verifica em segundo plano quais canais desta lista estão no ar",
                                 self._toggle_scan, kind="", text="  Testar lista", icon_size=16)
        bottom.addWidget(self.scan_btn)
        cl.addLayout(bottom)

        # --- player
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
        self.splitter.setChildrenCollapsible(False)
        lay.addWidget(self.sidebar)
        lay.addWidget(self.splitter, 1)
        self.setCentralWidget(root)
        self._paint_brand()
        self._build_nav()

    def _build_controls(self):
        w = QWidget(objectName="controls")
        v = QVBoxLayout(w)
        v.setContentsMargins(16, 10, 16, 12)
        v.setSpacing(8)

        top = QHBoxLayout()
        self.now_logo = QLabel()
        self.now_logo.setFixedSize(76, 48)
        self.now_logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        top.addWidget(self.now_logo)
        info = QVBoxLayout()
        info.setSpacing(1)
        self.now_title = QLabel("Nenhum canal", objectName="nowTitle")
        self.now_status = QLabel("", objectName="muted")
        self.now_epg = QLabel("", objectName="epg")
        for lbl in (self.now_title, self.now_status, self.now_epg):
            lbl.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            info.addWidget(lbl)
        self.epg_bar = QProgressBar(objectName="epgbar", textVisible=False)
        self.epg_bar.setRange(0, 1000)
        self.epg_bar.setFixedHeight(3)
        self.epg_bar.hide()
        info.addWidget(self.epg_bar)
        top.addLayout(info, 1)

        self.fav_btn = make_btn("star", "Favoritar (Ctrl+D)", self.toggle_fav_current)
        top.addWidget(self.fav_btn)
        top.addSpacing(8)
        top.addWidget(make_btn("prev", "Canal anterior (PgUp)", lambda: self.step(-1)))
        self.play_btn = make_btn("pause", "Pausar / continuar", self.toggle_pause, kind="play",
                                 icon_color="white", icon_size=22)
        top.addWidget(self.play_btn)
        top.addWidget(make_btn("next", "Próximo canal (PgDn)", lambda: self.step(+1)))
        top.addWidget(make_btn("stop", "Parar", self.stop))
        top.addSpacing(14)
        self.mute_btn = make_btn("volume", "Mudo (Ctrl+M)", self.toggle_mute)
        top.addWidget(self.mute_btn)
        self.vol = QSlider(Qt.Orientation.Horizontal)
        self.vol.setRange(0, 125)
        self.vol.setFixedWidth(104)
        self.vol.setValue(self.cfg["volume"])
        self.vol.valueChanged.connect(self._set_volume)
        top.addWidget(self.vol)
        top.addSpacing(6)
        top.addWidget(make_btn("fullscreen", "Tela cheia (F11 / duplo clique)", self.toggle_fullscreen))
        v.addLayout(top)

        auto = QHBoxLayout()
        auto.setSpacing(8)
        self.skip_cb = QCheckBox("Pular offline")
        self.skip_cb.setChecked(self.cfg["skip_dead"])
        self.skip_cb.setToolTip(f"Se o canal não abrir em {CONNECT_TIMEOUT}s ou travar, vai para o próximo sozinho")
        self.skip_cb.toggled.connect(lambda val: self.cfg.__setitem__("skip_dead", val))
        auto.addWidget(self.skip_cb)
        auto.addSpacing(6)
        self.zap_btn = make_btn("shuffle", "Troca de canal sozinho a cada intervalo (Ctrl+Z)", kind="",
                                text="  Zapping", checkable=True, icon_size=17)
        self.zap_btn.toggled.connect(self._toggle_zap)
        auto.addWidget(self.zap_btn)
        self.zap_spin = QSpinBox()
        self.zap_spin.setRange(5, 3600)
        self.zap_spin.setSuffix(" s")
        self.zap_spin.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        self.zap_spin.setFixedWidth(64)
        self.zap_spin.setToolTip("Intervalo do zapping (role o mouse ou digite)")
        self.zap_spin.setValue(self.cfg["zap_interval"])
        self.zap_spin.valueChanged.connect(lambda val: self.cfg.__setitem__("zap_interval", val))
        auto.addWidget(self.zap_spin)
        self.zap_mode = QComboBox()
        self.zap_mode.addItems(["Sequencial", "Aleatório"])
        self.zap_mode.setFixedWidth(112)
        self.zap_mode.setCurrentIndex(1 if self.cfg["zap_random"] else 0)
        self.zap_mode.currentIndexChanged.connect(lambda i: self.cfg.__setitem__("zap_random", i == 1))
        auto.addWidget(self.zap_mode)
        self.zap_favs_cb = QCheckBox("Só favoritos")
        self.zap_favs_cb.setChecked(self.cfg["zap_favs"])
        self.zap_favs_cb.setToolTip("O zapping passa apenas pelos seus canais favoritos")
        self.zap_favs_cb.toggled.connect(lambda val: self.cfg.__setitem__("zap_favs", val))
        auto.addWidget(self.zap_favs_cb)
        self.zap_lbl = QLabel("", objectName="muted")
        self.zap_lbl.setMinimumWidth(30)
        auto.addWidget(self.zap_lbl)
        self.info_lbl = QLabel("", objectName="muted")
        self.info_lbl.setOpenExternalLinks(True)
        self.info_lbl.setTextFormat(Qt.TextFormat.RichText)
        self.info_lbl.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.info_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        auto.addWidget(self.info_lbl, 1)
        self.guide_btn = make_btn("guide", "Guia de programação (Ctrl+G)", self.open_guide, kind="tool", icon_size=18)
        self.snap_btn = make_btn("camera", "Tirar foto da tela (Ctrl+S)", self.snapshot, kind="tool", icon_size=18)
        self.rec_btn = make_btn("record", "Gravar canal (Ctrl+R)", self.toggle_record, kind="tool",
                                checkable=True, icon_size=18)
        self.pip_btn = make_btn("pip", "Janela flutuante (Ctrl+P)", self.enter_pip, kind="tool", icon_size=18)
        self.mosaic_btn = make_btn("mosaic", "Mosaico: vários canais ao mesmo tempo", self.open_mosaic,
                                   kind="tool", icon_size=18)
        self.sleep_btn = make_btn("timer", "Timer para desligar", self._sleep_menu, kind="tool", icon_size=18)
        for b in (self.guide_btn, self.snap_btn, self.rec_btn, self.pip_btn, self.mosaic_btn, self.sleep_btn):
            auto.addWidget(b)
        self.sleep_lbl = QLabel("", objectName="muted")
        auto.addWidget(self.sleep_lbl)
        v.addLayout(auto)
        return w

    def _paint_brand(self):
        self.brand.setText(f"▶ IPTV <span style='color:{T['accent']}'>Player</span>")

    def _shortcuts(self):
        def sc(keys, fn):
            QShortcut(QKeySequence(keys), self, activated=fn, context=Qt.ShortcutContext.ApplicationShortcut)
        sc("PgDown", lambda: self.step(+1))
        sc("PgUp", lambda: self.step(-1))
        sc("F11", self.toggle_fullscreen)
        sc("Esc", self._escape)
        sc("Ctrl+F", lambda: (self.search.setFocus(), self.search.selectAll()))
        sc("Ctrl+D", self.toggle_fav_current)
        sc("Ctrl+M", self.toggle_mute)
        sc("Ctrl+Z", self.zap_btn.toggle)
        sc("Ctrl+G", self.open_guide)
        sc("Ctrl+S", self.snapshot)
        sc("Ctrl+R", lambda: self.rec_btn.click())
        sc("Ctrl+P", self.enter_pip)
        sc("Ctrl+L", self._toggle_view_mode)

    def _escape(self):
        if self.num_buffer:
            self.num_buffer = ""
            self.num_timer.stop()
            self._update_now_info()
        elif self.fullscreen:
            self.toggle_fullscreen()

    def keyPressEvent(self, e):
        # em tela cheia a lista está oculta: setas também trocam de canal
        if self.fullscreen and e.key() in (Qt.Key.Key_Up, Qt.Key.Key_Down, Qt.Key.Key_Right, Qt.Key.Key_Left):
            self.step(-1 if e.key() in (Qt.Key.Key_Up, Qt.Key.Key_Left) else +1)
        elif self.fullscreen and e.key() == Qt.Key.Key_Space:
            self.toggle_pause()
        else:
            super().keyPressEvent(e)

    # ================================================================ tema
    def apply_theme(self, mode, accent):
        apply_theme(mode, accent)
        QApplication.instance().setStyleSheet(build_style())
        refresh_icons(self)
        self._search_action.setIcon(icon("search", "muted", 16))
        self._paint_brand()
        self._build_nav()
        self.overlay.apply_style()
        if self.pip:
            self.pip.apply_style()
        if self.mosaic:
            self.mosaic.apply_style()
        self._update_now_info()
        self.view.viewport().update()

    # ================================================================ navegação / categorias
    def _build_nav(self):
        self.nav.blockSignals(True)
        self.nav.clear()

        def add(key, label, ic):
            it = QListWidgetItem(icon(ic, "muted", 18), "  " + label)
            it.setData(Qt.ItemDataRole.UserRole, key)
            self.nav.addItem(it)

        def section(title):
            it = QListWidgetItem(title.upper())
            it.setFlags(Qt.ItemFlag.NoItemFlags)
            f = QFont()
            f.setPointSizeF(7.5)
            f.setBold(True)
            f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 0.8)
            it.setFont(f)
            it.setForeground(QColor(T["muted"]))
            it.setSizeHint(QSize(10, 30))
            self.nav.addItem(it)

        add(src.FAV_KEY, "Favoritos", "star")
        add(src.RECENT_KEY, "Recentes", "clock")
        section("Canais")
        for key, label, ic in src.builtin_entries():
            add(key, label, ic)
        if self.cfg["custom_sources"]:
            section("Minhas listas")
            for s in self.cfg["custom_sources"]:
                add(src.custom_key(s), s["name"], "link" if src.is_remote(s["url"]) else "folder")
        for i in range(self.nav.count()):
            if self.nav.item(i).data(Qt.ItemDataRole.UserRole) == self.category:
                self.nav.setCurrentRow(i)
        self.nav.blockSignals(False)

    def _nav_changed(self, it, _prev=None):
        key = it.data(Qt.ItemDataRole.UserRole) if it else None
        if key:
            self._select_category(key, from_nav=True)

    def _nav_menu(self, pos):
        it = self.nav.itemAt(pos)
        key = it.data(Qt.ItemDataRole.UserRole) if it else None
        if not key or key in (src.FAV_KEY, src.RECENT_KEY):
            return
        m = QMenu(self)
        custom = self.cfg["custom_sources"]
        if src.download_url(key, custom):
            m.addAction(icon("refresh", "text", 16), "Atualizar esta lista", lambda: self.update_lists([key]))
        if key.startswith(src.CUSTOM_PREFIX):
            m.addAction(icon("settings", "text", 16), "Editar…", lambda: self.edit_list(key))
            m.addAction(icon("trash", "bad", 16), "Remover lista", lambda: self.remove_list(key))
        if not m.isEmpty():
            m.exec(self.nav.viewport().mapToGlobal(pos))

    def _label_for(self, key):
        for i in range(self.nav.count()):
            it = self.nav.item(i)
            if it.data(Qt.ItemDataRole.UserRole) == key:
                return it.text().strip()
        return key

    def _load_category(self, key):
        """Canais da categoria; dispara o download se ainda não houver arquivo local."""
        if key == src.FAV_KEY:
            return [Channel.from_dict(f) for f in self.cfg["favorites"]]
        if key == src.RECENT_KEY:
            return [Channel.from_dict(f) for f in self.cfg["recents"]]
        if key not in self.playlists:
            path = src.file_for(key, self.cfg["custom_sources"])
            if path is None:
                if src.download_url(key, self.cfg["custom_sources"]):
                    self.update_lists([key])
                    self.count_lbl.setText("Baixando lista…")
                return []
            try:
                self.playlists[key] = parse_m3u(path)
            except OSError:
                self.playlists[key] = ([], {})
            if set(header_epg_urls(self.playlists[key][1])) - self._epg_urls_loaded:
                QTimer.singleShot(0, self._reload_epg)
        return self.playlists[key][0]

    def _select_category(self, key, from_nav=False):
        if not from_nav:
            for i in range(self.nav.count()):
                if self.nav.item(i).data(Qt.ItemDataRole.UserRole) == key:
                    self.nav.blockSignals(True)
                    self.nav.setCurrentRow(i)
                    self.nav.blockSignals(False)
                    break
        self.category = key
        self.all_channels = self._load_category(key)
        self.numbers = {c.url: i + 1 for i, c in enumerate(self.all_channels)}
        if key not in (src.FAV_KEY, src.RECENT_KEY):
            self.cfg["last_category"] = key
        self.cat_title.setText(self._label_for(key))
        groups = sorted({c.group for c in self.all_channels if c.group})
        prev_group = self.group_box.currentText()
        self.group_box.blockSignals(True)
        self.group_box.clear()
        self.group_box.addItem("Todos os grupos")
        self.group_box.addItems(groups)
        if prev_group in groups and from_nav is None:
            self.group_box.setCurrentText(prev_group)
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
        self.model.set_channels(self.visible)
        self._select_current_in_view()
        self._update_count()

    def _update_count(self):
        if self.downloader.running and not self.all_channels:
            return
        dead = sum(1 for c in self.visible if self.status_of(c.url) == "dead")
        txt = f"{len(self.visible)} canais"
        if dead:
            txt += f"  ·  {dead} offline"
        if not self.all_channels and self.category == src.FAV_KEY:
            txt = "Sem favoritos ainda — use a estrela ★"
        self.count_lbl.setText(txt)

    def _select_current_in_view(self):
        if not self.current:
            return
        for i, c in enumerate(self.visible):
            if c.url == self.current.url:
                idx = self.model.index(i)
                self.view.setCurrentIndex(idx)
                self.view.scrollTo(idx)
                return

    def _set_view_mode(self, mode):
        grid = mode == "grid"
        self.cfg["view_mode"] = mode
        if grid:
            self.view.setViewMode(QListView.ViewMode.IconMode)
            self.view.setItemDelegate(self.grid_delegate)
            self.view.setGridSize(GridDelegate.CARD)
            self.view.setResizeMode(QListView.ResizeMode.Adjust)
            self.view.setMovement(QListView.Movement.Static)
            self.view.setWrapping(True)
        else:
            self.view.setViewMode(QListView.ViewMode.ListMode)
            self.view.setItemDelegate(self.list_delegate)
            self.view.setGridSize(QSize())
            self.view.setWrapping(False)
        self.view.setUniformItemSizes(True)
        # na grade o painel precisa caber pelo menos 2 colunas de cartões
        self.chan_panel.setMinimumWidth(2 * GridDelegate.CARD.width() + 52 if grid else 320)
        set_btn_icon(self.view_btn, "list" if grid else "grid")
        self.view_btn.setToolTip("Ver em lista (Ctrl+L)" if grid else "Ver em grade (Ctrl+L)")
        total = max(self.splitter.width(), 1200)
        left = min(760, int(total * 0.5)) if grid else 400
        self.splitter.setSizes([left, total - left])
        self._select_current_in_view()

    def _toggle_view_mode(self):
        self._set_view_mode("list" if self.cfg["view_mode"] == "grid" else "grid")

    def _on_logo(self, url):
        self.view.viewport().update()
        if self.current and self.current.logo == url:
            self._update_now_info()

    # ================================================================ listas (iptv-org e do usuário)
    def _side_status(self, text):
        self.side_lbl.setText(text)

    def _auto_update_lists(self):
        if self.cfg["auto_update_lists"] and time.time() - self.cfg["lists_updated"] > LIST_MAX_AGE:
            self.update_lists()

    def update_lists(self, keys=None):
        if self.downloader.running:
            return
        custom = self.cfg["custom_sources"]
        if keys is None:
            jobs = src.all_update_jobs(custom)
        else:
            jobs = [(k, src.download_url(k, custom), src.cache_path(k, custom))
                    for k in keys if src.download_url(k, custom)]
        self._dl_all = keys is None
        if jobs:
            self._side_status(f"Atualizando listas… 0/{len(jobs)}")
            self.upd_btn.setEnabled(False)
            self.downloader.start(jobs)

    def _on_list_done(self, key, ok):
        if not ok:
            return
        self.playlists.pop(key, None)
        if key == self.category:
            self._select_category(key, from_nav=None)
        if key not in {k for k, *_ in src.builtin_entries()} and not key.startswith(src.CUSTOM_PREFIX):
            self._build_nav()

    def _on_lists_finished(self, ok, fail):
        self.upd_btn.setEnabled(True)
        if self._dl_all and ok:
            self.cfg["lists_updated"] = time.time()
            self.cfg.save()
        msg = f"{ok} lista(s) atualizada(s)" if ok else "Não foi possível atualizar (sem internet?)"
        if ok and fail:
            msg += f", {fail} com erro"
        self._side_status(msg)
        QTimer.singleShot(8000, lambda: self.side_lbl.text() == msg and self._side_status(""))
        self._reload_epg()

    def add_list(self):
        dlg = AddListDialog(self)
        if dlg.exec():
            name, url = dlg.values()
            s = src.new_custom(name, url)
            self.cfg["custom_sources"].append(s)
            self.cfg.save()
            self._build_nav()
            self._select_category(src.custom_key(s))

    def edit_list(self, key):
        s = next((x for x in self.cfg["custom_sources"] if src.custom_key(x) == key), None)
        if not s:
            return
        dlg = AddListDialog(self, s["name"], s["url"])
        dlg.setWindowTitle("Editar lista")
        if dlg.exec():
            s["name"], new_url = dlg.values()
            if new_url != s["url"]:
                s["url"] = new_url
                src.custom_cache(s).unlink(missing_ok=True)
                self.playlists.pop(key, None)
            self.cfg.save()
            self._build_nav()
            self._select_category(key)

    def remove_list(self, key):
        s = next((x for x in self.cfg["custom_sources"] if src.custom_key(x) == key), None)
        if not s or QMessageBox.question(self, "Remover lista", f"Remover a lista “{s['name']}”?") \
                != QMessageBox.StandardButton.Yes:
            return
        self.cfg["custom_sources"] = [x for x in self.cfg["custom_sources"] if x is not s]
        src.custom_cache(s).unlink(missing_ok=True)
        self.playlists.pop(key, None)
        self.cfg.save()
        self._build_nav()
        if self.category == key:
            self._select_category("melhor_iptv")

    # ================================================================ EPG
    def _epg_sources(self):
        if not self.cfg["epg_enabled"]:
            return []
        urls = list(self.cfg["epg_urls"])
        for _, header in self.playlists.values():
            urls += header_epg_urls(header)
        return list(dict.fromkeys(urls))

    def _reload_epg(self, force=False):
        urls = self._epg_sources()
        self._epg_urls_loaded = set(urls)
        self.epg.load(urls, force)

    def _on_epg_updated(self):
        self.view.viewport().update()
        self._update_now_info()
        QTimer.singleShot(6000, lambda: self.side_lbl.text().startswith("Guia:") and self._side_status(""))

    def open_guide(self):
        if not self.current:
            return self._info("Escolha um canal para ver o guia.")
        GuideDialog(self.current, self.epg.schedule(self.current), self).exec()

    # ================================================================ estado
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
        self.view.viewport().update()

    def is_fav(self, url):
        return any(f["url"] == url for f in self.cfg["favorites"])

    def _info(self, html, seconds=6):
        self.info_lbl.setText(html)
        QTimer.singleShot(seconds * 1000, lambda: self.info_lbl.text() == html and self.info_lbl.setText(""))

    # ================================================================ reprodução
    def play(self, ch, auto=False, force=False):
        if ch is None:
            return
        if (not force and self.current and ch.url == self.current.url
                and time.monotonic() - self.started_at < 1.5):
            return  # clique duplo não reinicia o canal
        if self.current and self.session_status.get(self.current.url) == "loading":
            self.session_status.pop(self.current.url)
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
        self.video.set_message("Conectando…")
        self.cfg["last_url"] = ch.url
        self._add_recent(ch)
        self._set_status("◌ Conectando…", "warn")
        set_btn_icon(self.fav_btn, "star_fill" if self.is_fav(ch.url) else "star",
                     "star" if self.is_fav(ch.url) else "text")
        set_btn_icon(self.play_btn, "pause")
        set_btn_icon(self.overlay.play_btn, "pause")
        self._select_current_in_view()
        self.zap_left = self.zap_spin.value()

    def _replay(self):
        if self.current:
            self.play(self.current, auto=True, force=True)

    def _update_now_info(self):
        ch = self.current
        text, color = self._status
        extra = [x for x in ((ch.group, ch.quality) if ch else ()) if x]
        status_html = (f"<span style='color:{T.get(color, color)}'>{text}</span>"
                       + (f"  <span style='color:{T['muted']}'>·  {'  ·  '.join(extra)}</span>" if extra else ""))
        if self.num_buffer:
            title = f"Canal {self.num_buffer}_"
        else:
            title = ch.name if ch else ("Mosaico aberto" if self.mosaic else "Nenhum canal")
        self.now_title.setText(title)
        self.now_status.setText(status_html if ch else "")
        epg_txt, frac = "", None
        if ch and self.cfg["epg_enabled"]:
            cur, nxt = self.epg.now_next(ch)
            if cur:
                epg_txt = f"Agora: <b>{cur[2]}</b>  ({hm(cur[0])}–{hm(cur[1])})"
                frac = (time.time() - cur[0]) / max(1, cur[1] - cur[0])
            if nxt:
                epg_txt += ("   ·   " if epg_txt else "") + f"Depois: {nxt[2]} ({hm(nxt[0])})"
        self.now_epg.setText(epg_txt)
        self.now_epg.setVisible(bool(epg_txt))
        self.epg_bar.setVisible(frac is not None)
        if frac is not None:
            self.epg_bar.setValue(int(min(1, max(0, frac)) * 1000))
        pm = self.logos.get(ch.logo) if ch else None
        if pm:
            self.now_logo.setPixmap(pm.scaled(70, 42, Qt.AspectRatioMode.KeepAspectRatio,
                                              Qt.TransformationMode.SmoothTransformation))
        else:
            self.now_logo.setPixmap(icon("tv", "muted", 26).pixmap(26, 26))
        self.now_logo.setStyleSheet(f"background:{T['logo_bg']};border-radius:8px;")
        # controles da tela cheia e janela flutuante
        self.overlay.title.setText(title)
        self.overlay.status.setText(status_html if ch else "")
        self.overlay.epg.setText(epg_txt)
        self.overlay.epg.setVisible(bool(epg_txt))
        self.overlay.logo.setPixmap(pm.scaled(80, 50, Qt.AspectRatioMode.KeepAspectRatio,
                                              Qt.TransformationMode.SmoothTransformation) if pm else
                                    icon("tv", "#b8bfcf", 30).pixmap(30, 30))
        if self.pip:
            self.pip.set_title(title)

    def _set_status(self, text, color):
        self._status = (text, color)
        self._update_now_info()

    def _show_marquee(self, text, size=30, position=5, timeout=4000):
        p = self.player
        M = vlc.VideoMarqueeOption
        p.video_set_marquee_int(M.Enable, 1)
        p.video_set_marquee_int(M.Size, size)
        p.video_set_marquee_int(M.Position, position)  # 5 = topo-esquerda, 6 = topo-direita
        p.video_set_marquee_int(M.X, 24)
        p.video_set_marquee_int(M.Y, 20)
        p.video_set_marquee_int(M.Opacity, 235)
        p.video_set_marquee_int(M.Timeout, timeout)
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
                self._set_status("● Ao vivo", "ok")
                self.video.set_message("")
                self.player.audio_set_volume(self.vol.value())
                num = self.numbers.get(self.current.url)
                self._show_marquee(f"{num}  {self.current.name}" if num else self.current.name)
                self._update_count()
            elif now - self.started_at > CONNECT_TIMEOUT:
                self._on_fail("não respondeu")
            return
        if t != self.last_time:
            self.last_time, self.last_progress = t, now
            if self._status[0].startswith("↻"):
                self._set_status("● Ao vivo", "ok")
        elif now - self.last_progress > STALL_TIMEOUT:
            if not self.retried:  # uma tentativa de reconexão antes de desistir
                self.retried = True
                self._set_status("↻ Reconectando…", "warn")
                self.player.stop()
                self.player.play()
                self.started_at = now
                self.confirmed = False
            else:
                self._on_fail("travou")

    def _on_fail(self, reason):
        ch = self.current
        self._mark(ch.url, "dead")
        self._update_count()
        self.player.stop()
        self.fail_streak += 1
        pool = self._zap_pool() if self.zap_btn.isChecked() else None
        if self.skip_cb.isChecked() and self.fail_streak < MAX_CONSECUTIVE_FAILS and len(pool or self.visible) > 1:
            self._set_status(f"✕ Offline ({reason}) — pulando…", "bad")
            self.video.set_message(f"{ch.name} está offline — indo para o próximo…")
            self.confirmed = True  # evita reprocessar a falha enquanto espera o pulo
            self.last_progress = float("inf")
            QTimer.singleShot(350, lambda: self.current is ch and self.step(+1, auto=True, pool=pool))
        else:
            if self.fail_streak >= MAX_CONSECUTIVE_FAILS:
                reason += f"; {self.fail_streak} canais seguidos falharam, parei de pular"
            self._set_status(f"✕ Offline ({reason})", "bad")
            self.video.set_message(f"{ch.name} está offline")
            self.current = None
            self._update_now_info()
            self.view.viewport().update()

    def step(self, delta, auto=False, random_pick=False, pool=None):
        seq = pool if pool else self.visible
        if not seq:
            return
        skip = self.skip_cb.isChecked()
        cur_url = self.current.url if self.current else None
        candidates = [c for c in seq if not (skip and self.status_of(c.url) == "dead") or c.url == cur_url]
        if not candidates:
            self._set_status("✕ Nenhum canal disponível nesta lista", "bad")
            return
        if random_pick:
            options = [c for c in candidates if c.url != cur_url] or candidates
            return self.play(random.choice(options), auto=auto, force=True)
        urls = [c.url for c in seq]
        idx = urls.index(cur_url) if cur_url in urls else (-1 if delta > 0 else 0)
        n = len(seq)
        cand_urls = {c.url for c in candidates}
        for k in range(1, n + 1):
            c = seq[(idx + delta * k) % n]
            if c.url in cand_urls and c.url != cur_url:
                return self.play(c, auto=auto, force=True)

    def toggle_pause(self):
        if not self.current:
            return self.step(+1)
        if self.player.get_state() in (vlc.State.Stopped, vlc.State.Error, vlc.State.Ended,
                                       vlc.State.NothingSpecial):
            return self.play(self.current, force=True)
        self.player.pause()
        paused = self.player.get_state() != vlc.State.Paused  # estado muda de forma assíncrona
        for b in (self.play_btn, self.overlay.play_btn):
            set_btn_icon(b, "play" if paused else "pause")

    def stop(self):
        self.player.stop()
        if self.current and self.session_status.get(self.current.url) == "loading":
            self.session_status.pop(self.current.url)
        self.current = None
        self.zap_btn.setChecked(False)
        self._status = ("", "muted")
        set_btn_icon(self.play_btn, "play")
        self.video.set_message("Escolha um canal na lista")
        self._update_now_info()
        self.view.viewport().update()

    def _set_volume(self, v):
        self.cfg["volume"] = v
        self.player.audio_set_volume(v)
        if v and self.player.audio_get_mute():
            self.player.audio_set_mute(False)
        for b in (self.mute_btn, self.overlay.mute_btn):
            set_btn_icon(b, "mute" if v == 0 else "volume")
        for s in (self.vol, self.overlay.vol):
            if s.value() != v:
                s.blockSignals(True)
                s.setValue(v)
                s.blockSignals(False)

    def toggle_mute(self):
        m = not self.player.audio_get_mute()
        self.player.audio_set_mute(m)
        for b in (self.mute_btn, self.overlay.mute_btn):
            set_btn_icon(b, "mute" if m else "volume")

    # ================================================================ zapping
    def _zap_pool(self):
        if self.zap_favs_cb.isChecked():
            return [Channel.from_dict(f) for f in self.cfg["favorites"]] or None
        return None

    def _toggle_zap(self, on):
        self.zap_bar.setVisible(on)
        if self.overlay.zap_btn.isChecked() != on:
            self.overlay.zap_btn.blockSignals(True)
            self.overlay.zap_btn.setChecked(on)
            self.overlay.zap_btn.blockSignals(False)
        if on:
            self.zap_left = self.zap_spin.value()
            if not self.current:
                self.step(+1, auto=True, random_pick=self.zap_mode.currentIndex() == 1, pool=self._zap_pool())
        else:
            self.zap_lbl.setText("")
        self._zap_render()

    def _zap_tick(self):
        if not self.zap_btn.isChecked() or not self.current or not self.confirmed:
            return  # só conta tempo enquanto o canal está realmente tocando
        if self.player.get_state() == vlc.State.Paused:
            return
        self.zap_left -= 1
        if self.zap_left <= 0:
            self.step(+1, auto=True, random_pick=self.zap_mode.currentIndex() == 1, pool=self._zap_pool())
        self._zap_render()

    def _zap_render(self):
        total = self.zap_spin.value()
        self.zap_bar.setRange(0, total)
        self.zap_bar.setValue(max(0, total - self.zap_left))
        if self.zap_btn.isChecked():
            self.zap_lbl.setText(f"{max(0, self.zap_left)}s")

    # ================================================================ relógio (1s): zapping, timer, gravação, EPG
    def _clock_tick(self):
        self._zap_tick()
        now = time.time()
        if self.sleep_deadline:
            rem = self.sleep_deadline - now
            if rem <= 0:
                self.sleep_deadline = None
                self.close()
                return
            self.sleep_lbl.setText(f"{math.ceil(rem / 60)} min" if rem > 60 else f"{int(rem)} s")
            if rem <= 60 and not self._sleep_warned:
                self._sleep_warned = True
                self._show_marquee("Desligando em 1 minuto…", timeout=6000)
                self._info("O timer vai fechar o IPTV Player em 1 minuto.")
        if self.recorder.active:
            if self.recorder.failed():
                self.rec_btn.setChecked(False)
                self.recorder.stop()
                self._info(f"<span style='color:{T['bad']}'>A gravação falhou (canal fora do ar?)</span>")
            else:
                e = self.recorder.elapsed()
                mb = self.recorder.size() / 1e6
                self.info_lbl.setText(f"<span style='color:{T['bad']}'>● REC</span> {e // 60:02d}:{e % 60:02d}"
                                      f" · {self.recorder.ch.name} · {mb:.0f} MB")
        if int(now) % 30 == 0:
            self._update_now_info()
            self.view.viewport().update()

    # ================================================================ timer para desligar
    def _sleep_menu(self):
        m = QMenu(self)
        m.addAction("Desativado", lambda: self._set_sleep(None))
        m.addSeparator()
        for label, minutes in (("15 minutos", 15), ("30 minutos", 30), ("45 minutos", 45), ("1 hora", 60),
                               ("1 hora e meia", 90), ("2 horas", 120)):
            m.addAction(label, lambda mn=minutes: self._set_sleep(time.time() + mn * 60))
        if self.current:
            cur, _ = self.epg.now_next(self.current)
            if cur:
                m.addSeparator()
                m.addAction(f"Ao fim do programa atual ({hm(cur[1])})", lambda end=cur[1]: self._set_sleep(end))
        m.exec(self.sleep_btn.mapToGlobal(QPoint(0, -m.sizeHint().height() - 4)))

    def _set_sleep(self, deadline):
        self.sleep_deadline = deadline
        self._sleep_warned = False
        self.sleep_btn.setProperty("icon_color", "accent" if deadline else "text")
        set_btn_icon(self.sleep_btn, "timer")
        self.sleep_lbl.setText("")
        if deadline:
            self._info(f"O IPTV Player vai fechar às {hm(deadline)}.")

    # ================================================================ número do canal
    def eventFilter(self, obj, e):
        if (e.type() == QEvent.Type.KeyPress and QApplication.activeWindow() is self
                and 0x30 <= e.key() <= 0x39
                and not e.modifiers() & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier)
                and not isinstance(QApplication.focusWidget(), (QLineEdit, QAbstractSpinBox, QPlainTextEdit))):
            self._digit(e.key() - 0x30)
            return True
        return super().eventFilter(obj, e)

    def _digit(self, d):
        if len(self.num_buffer) >= 4:
            return
        self.num_buffer += str(d)
        self.num_timer.start()
        self._show_marquee(f"{self.num_buffer}_", size=64, position=6, timeout=1800)
        self._update_now_info()

    def _commit_number(self):
        n, self.num_buffer = int(self.num_buffer or 0), ""
        if 1 <= n <= len(self.all_channels):
            self.play(self.all_channels[n - 1], force=True)
        else:
            self._show_marquee(f"Canal {n} não existe nesta lista", timeout=2500)
            self._update_now_info()

    # ================================================================ foto e gravação
    def snapshot(self):
        if not self.current or not self.confirmed:
            return self._info("Nada tocando para fotografar.")
        path = snapshot_path(self.current)
        if self.player.video_take_snapshot(0, str(path), 0, 0) == 0:
            self._show_marquee("📷 Foto salva", timeout=2000)
            self._info(f"Foto salva · <a href='{QUrl.fromLocalFile(str(path.parent)).toString()}' "
                       f"style='color:{T['accent']}'>abrir pasta</a>", 8)
        else:
            self._info("Não foi possível tirar a foto.")

    def toggle_record(self):
        if self.recorder.active:
            ch, path = self.recorder.ch, self.recorder.stop()
            self.rec_btn.setChecked(False)
            if path:
                self._info(f"Gravação de {ch.name} salva · <a href='{QUrl.fromLocalFile(str(path.parent)).toString()}'"
                           f" style='color:{T['accent']}'>abrir pasta</a>", 10)
        elif self.current:
            self.recorder.start(self.current)
            self.rec_btn.setChecked(True)
            self._show_marquee("● Gravando", timeout=2500)
        else:
            self.rec_btn.setChecked(False)
            self._info("Escolha um canal para gravar.")

    def record_channel(self, ch):
        if self.recorder.active:
            self.recorder.stop()
        self.recorder.start(ch)
        self.rec_btn.setChecked(True)

    # ================================================================ favoritos / recentes / menu
    def toggle_fav_current(self):
        if self.current:
            self.toggle_fav(self.current)

    def toggle_fav(self, ch):
        if self.is_fav(ch.url):
            self.cfg["favorites"] = [f for f in self.cfg["favorites"] if f["url"] != ch.url]
        else:
            self.cfg["favorites"].append(ch.__dict__.copy())
        if self.current and self.current.url == ch.url:
            fav = self.is_fav(ch.url)
            set_btn_icon(self.fav_btn, "star_fill" if fav else "star", "star" if fav else "text")
        if self.category == src.FAV_KEY:
            self._select_category(src.FAV_KEY)
        self.view.viewport().update()
        self.cfg.save()

    def _add_recent(self, ch):
        rec = [r for r in self.cfg["recents"] if r["url"] != ch.url]
        rec.insert(0, ch.__dict__.copy())
        self.cfg["recents"] = rec[:40]

    def _context_menu(self, pos):
        idx = self.view.indexAt(pos)
        if not idx.isValid():
            return
        ch = idx.data(Qt.ItemDataRole.UserRole)
        m = QMenu(self)
        m.addAction(icon("play", "text", 16), "Assistir", lambda: self.play(ch, force=True))
        fav = self.is_fav(ch.url)
        m.addAction(icon("star_fill" if fav else "star", "star", 16),
                    "Remover dos favoritos" if fav else "Adicionar aos favoritos", lambda: self.toggle_fav(ch))
        m.addAction(icon("record", "bad", 16), "Gravar este canal", lambda: self.record_channel(ch))
        if self.epg.schedule(ch):
            m.addAction(icon("guide", "text", 16), "Ver guia de programação",
                        lambda: GuideDialog(ch, self.epg.schedule(ch), self).exec())
        m.addSeparator()
        m.addAction(icon("link", "text", 16), "Copiar link", lambda: QGuiApplication.clipboard().setText(ch.url))
        if self.status_of(ch.url) == "dead":
            m.addAction(icon("refresh", "text", 16), "Desmarcar como offline", lambda: (
                self.session_status.pop(ch.url, None), self.cfg["dead"].pop(ch.url, None),
                self.view.viewport().update(), self._update_count()))
        m.exec(self.view.viewport().mapToGlobal(pos))

    # ================================================================ teste de lista
    def _toggle_scan(self):
        if self.scanner.running:
            self.scanner.stop()
            return
        targets = [c for c in self.visible if self.status_of(c.url) != "ok"]
        if not targets:
            return
        self.scan_btn.setText("  Parar teste")
        set_btn_icon(self.scan_btn, "stop")
        self.scanner.start(targets)

    def _on_scan_result(self, url, ok):
        if self.current and self.current.url == url:
            return
        self._mark(url, "ok" if ok else "dead")

    def _on_scan_finished(self):
        self.scan_btn.setText("  Testar lista")
        set_btn_icon(self.scan_btn, "check")
        if self.hide_dead_cb.isChecked():
            self._apply_filter()
        else:
            self._update_count()
        self.cfg.save()

    # ================================================================ tela cheia
    def _wire_overlay(self):
        o = self.overlay
        o.prev.connect(lambda: self.step(-1))
        o.next.connect(lambda: self.step(+1))
        o.play_pause.connect(self.toggle_pause)
        o.exit_fs.connect(self.toggle_fullscreen)
        o.mute.connect(self.toggle_mute)
        o.zap_toggled.connect(self.zap_btn.setChecked)
        o.volume_changed.connect(self.vol.setValue)
        o.vol.setValue(self.vol.value())
        o.apply_style()

    def toggle_fullscreen(self):
        self.fullscreen = not self.fullscreen
        for w in (self.sidebar, self.chan_panel, self.controls):
            w.setVisible(not self.fullscreen)
        if self.fullscreen:
            self.showFullScreen()
            self.setFocus()
            screen = self.screen() or QGuiApplication.primaryScreen()
            self.overlay.place(screen.geometry())
            self._update_now_info()
            self._last_mouse = QCursor.pos()
            self._overlay_until = time.monotonic() + 3
            self.overlay.show()
            self.fs_timer.start()
            if self.current:
                self._show_marquee(self.current.name)
        else:
            self.fs_timer.stop()
            self.overlay.hide()
            self.showNormal()

    def _fs_mouse_tick(self):
        pos, now = QCursor.pos(), time.monotonic()
        over = self.overlay.isVisible() and self.overlay.geometry().contains(pos)
        if pos != self._last_mouse:
            self._last_mouse = pos
            self._overlay_until = now + 3
            if not self.overlay.isVisible() and self.isActiveWindow():
                self.overlay.show()
        elif self.overlay.isVisible() and not over and (now > self._overlay_until or not self.isActiveWindow()):
            self.overlay.hide()

    # ================================================================ janela flutuante (PiP)
    def enter_pip(self):
        if self.pip:
            return
        if not self.current:
            return self._info("Escolha um canal para abrir na janela flutuante.")
        if self.fullscreen:
            self.toggle_fullscreen()
        self.pip = PipWindow()
        self.pip.restore.connect(lambda: self.exit_pip())
        self.pip.close_stop.connect(lambda: self.exit_pip(stop=True))
        self.pip.prev.connect(lambda: self.step(-1))
        self.pip.next.connect(lambda: self.step(+1))
        self.pip.show()
        self.player.stop()
        self.player.set_hwnd(int(self.pip.video.winId()))
        self._replay()
        self._update_now_info()
        self.hide()

    def exit_pip(self, stop=False):
        pip, self.pip = self.pip, None
        if not pip:
            return
        self.player.stop()
        self.player.set_hwnd(int(self.video.winId()))
        pip.closing = True
        pip.close()
        pip.deleteLater()
        self.show()
        self.activateWindow()
        if stop:
            self.stop()
        else:
            self._replay()

    # ================================================================ mosaico
    def open_mosaic(self):
        if self.mosaic:
            self.mosaic.raise_()
            return
        pool = [c for c in self.visible if self.status_of(c.url) != "dead"]
        if self.current:
            pool = [self.current] + [c for c in pool if c.url != self.current.url]
        if len(pool) < 2:
            return self._info("Poucos canais disponíveis nesta lista para o mosaico.")
        if self.zap_btn.isChecked():
            self.zap_btn.setChecked(False)
        self._mosaic_prev = self.current
        self.player.stop()
        self.current = None
        self.video.set_message("Mosaico aberto em outra janela")
        self.mosaic = MosaicWindow(self.instance, pool, self.cfg["mosaic_size"], self.vol.value())
        self.mosaic.closed.connect(self._mosaic_closed)
        self.mosaic.channel_failed.connect(lambda url: self._mark(url, "dead"))
        self._update_now_info()
        self.mosaic.showMaximized()

    def _mosaic_closed(self, chosen):
        self.cfg["mosaic_size"] = self.mosaic.size
        self.mosaic.deleteLater()
        self.mosaic = None
        ch = chosen or self._mosaic_prev
        self.activateWindow()
        if ch:
            self.play(ch, force=True)
        else:
            self.stop()

    # ================================================================ atualizações do app
    def _check_updates(self):
        if not self.cfg["check_updates"]:
            return
        if version_tuple(self.cfg["latest_version"]) > version_tuple(APP_VERSION):
            self._show_update_banner(self.cfg["latest_version"])
        if time.time() - self.cfg["last_update_check"] < UPDATE_CHECK_EVERY:
            return
        req = make_request(f"https://api.github.com/repos/{REPO}/releases/latest", timeout=10000,
                           user_agent=b"IPTV-Player")
        req.setRawHeader(b"Accept", b"application/vnd.github+json")
        reply = self.nam.get(req)
        reply.finished.connect(lambda: self._on_update_reply(reply))

    def _on_update_reply(self, reply):
        try:
            if reply.error() != QNetworkReply.NetworkError.NoError:
                return
            tag = json.loads(bytes(reply.readAll()).decode()).get("tag_name", "")
            self.cfg["last_update_check"] = time.time()
            self.cfg["latest_version"] = tag
            self.cfg.save()
            if version_tuple(tag) > version_tuple(APP_VERSION):
                self._show_update_banner(tag)
        except (ValueError, AttributeError):
            pass
        finally:
            reply.deleteLater()

    def _show_update_banner(self, tag):
        self.update_banner.setText(f"  Nova versão {tag} disponível")
        self.update_banner.setToolTip("Abrir a página de download")
        self.update_banner.show()

    def _open_update_page(self):
        QDesktopServices.openUrl(QUrl(f"https://github.com/{REPO}/releases/latest"))

    # ================================================================ configurações
    def open_settings(self):
        dlg = SettingsDialog(self.cfg, self)
        dlg.theme_changed.connect(self.apply_theme)
        dlg.update_lists.connect(lambda: self.update_lists())
        dlg.reload_epg.connect(lambda: self._reload_epg(force=True))
        old_epg = (self.cfg["epg_enabled"], list(self.cfg["epg_urls"]))
        dlg.exec()
        self.apply_theme(self.cfg["theme"], self.cfg["accent"])  # desfaz a prévia se não salvou
        if (self.cfg["epg_enabled"], self.cfg["epg_urls"]) != old_epg:
            self._reload_epg()

    # ================================================================ encerramento
    def closeEvent(self, e):
        self.scanner.stop()
        if self.recorder.active:
            self.recorder.stop()
        if self.mosaic:
            self.mosaic.close()
        if self.pip:
            self.pip.closing = True
            self.pip.close()
        self.overlay.close()
        self.player.stop()
        self.cfg.save()
        super().closeEvent(e)
