"""Janela principal do IPTV Player."""
import json
import math
import random
import re
import time
from dataclasses import replace
from pathlib import Path

from PyQt6.QtCore import QByteArray, QEvent, QPoint, QRect, QSize, Qt, QTimer, QUrl
from PyQt6.QtGui import QColor, QCursor, QDesktopServices, QFont, QGuiApplication, QKeySequence, QPixmap, QShortcut
from PyQt6.QtNetwork import QNetworkAccessManager, QNetworkReply
from PyQt6.QtWidgets import (
    QAbstractSpinBox, QApplication, QCheckBox, QComboBox, QDialog, QHBoxLayout, QInputDialog, QLabel, QLineEdit,
    QListView,
    QListWidget, QListWidgetItem, QMainWindow, QMenu, QMessageBox, QPlainTextEdit, QProgressBar, QSizePolicy,
    QSlider, QSpinBox, QSplitter, QVBoxLayout, QWidget,
)

from . import APP_NAME, APP_VERSION, REPO
from . import sources as src
from .audio import AudioEngine
from .dub import Dubber
from .captions import CaptionWorker, SubtitleOverlay, missing_deps, video_rect_global
from .config import DEAD_TTL, Config
from .discovery import MAX_ALT_TRIES, ExploredSet, LinkIndex, NewChannels, ScanProgress, list_urls
from .discovery import _norm_name as norm_channel_name
from .dialogs import AddListDialog, GuideDialog, KidsDialog, SettingsDialog, pin_hash
from .epg import EpgManager
from .log import log
from .m3u import Channel, header_epg_urls, parse_m3u
from .mosaic import MosaicWindow
from .net import ListDownloader, LogoLoader, Scanner, make_request
from .pip import PipWindow
from .recorder import Recorder, clip_path, snapshot_path
from .timeshift import LIVE_DELAY_OPT, START_WAIT_MS, Timeshift, supports as timeshift_supports
from .remote import RemoteServer, qr_pixmap
from .updater import UpdateDownloader, can_self_update, run_installer, setup_asset
from .theme import T, app_icon, apply_theme, build_style, icon
from .vlcload import vlc
from .widgets import (
    ChannelModel, FullscreenOverlay, GridDelegate, ListDelegate, SeekSlider, VideoFrame, make_btn, refresh_icons,
    set_btn_icon,
)

AUTO_SCAN_EVERY = 6 * 3600   # teste automático de todos os canais (as marcas de offline duram 6 h)
CONNECT_TIMEOUT = 15         # segundos para o canal começar a tocar
STALL_TIMEOUT = 12           # segundos sem avançar = travado
MAX_CONSECUTIVE_FAILS = 25   # evita loop infinito pulando canais
LIST_MAX_AGE = 86400          # atualização automática das listas (1× por dia)
UPDATE_CHECK_EVERY = 12 * 3600
COMPACT_BELOW = 1340         # largura da janela abaixo da qual a barra lateral vira só ícones
LABEL_ROLE = Qt.ItemDataRole.UserRole + 1


def hm(ts):
    return time.strftime("%H:%M", time.localtime(ts))


def version_tuple(s):
    return tuple(int(x) for x in re.findall(r"\d+", s or "")[:3]) or (0,)


class MainWindow(QMainWindow):
    def __init__(self, cfg=None):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(app_icon())
        avail = QGuiApplication.primaryScreen().availableGeometry()
        self.resize(min(1440, avail.width() - 40), min(860, avail.height() - 40))
        self.start_maximized = avail.width() < 1500 or avail.height() < 900
        self.cfg = cfg or Config()

        self.logos = LogoLoader(self)
        self.logos.loaded.connect(self._on_logo)
        self.scanner = Scanner(self)
        self.scanner.result.connect(self._on_scan_result)
        self.scanner.progress.connect(lambda d, t: self.count_lbl.setText(f"Testando… {d}/{t}"))
        self.scanner.finished.connect(self._on_scan_finished)
        # teste automático de todos os canais (devagar, em segundo plano)
        self.auto_scanner = Scanner(self, max_active=6)
        self.auto_scanner.result.connect(self._on_auto_scan_result)
        self.auto_scanner.finished.connect(self._on_auto_scan_finished)
        self._auto_counts = [0, 0]
        self.scan_progress = ScanProgress()
        QTimer.singleShot(3 * 60 * 1000, self._maybe_auto_scan)
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
        self._dead_checked = time.time()
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
        self._compact = False
        self._was_maximized = False
        self._side_btns = []
        self._banner_tag = ""
        self.links = LinkIndex()     # outros links do mesmo canal, para quando um cair
        self.newch = NewChannels()   # canais que chegaram nas últimas atualizações
        self.explored = ExploredSet()
        self.stream = None           # (url, opções) realmente tocando; pode ser um link alternativo
        self._alt_tried = set()
        pos = self.cfg["explore_pos"]
        self.explore_cat, self.explore_idx = (pos[0], pos[1]) if len(pos) == 2 else (None, -1)

        self.instance = vlc.Instance(["--no-video-title-show", "--network-caching=1500", "--quiet",
                                      "--sub-source=marq"])
        self.player = self.instance.media_player_new()
        self.recorder = Recorder(self.instance)
        self._build_ui()
        self.overlay = FullscreenOverlay()
        self._wire_overlay()
        self.updater = None          # download da versão nova (atualização com um clique)
        for item in self.cfg["schedule"]:
            if item.get("state") == "recording":
                item["state"] = "waiting"  # o programa fechou no meio: volta a gravar se ainda estiver passando
        self.timeshift = Timeshift(self.cfg["timeshift_minutes"], self.instance, self)
        self.timeshift.ready.connect(self._ts_ready)
        self.timeshift.failed.connect(self._ts_failed)
        self.timeshift.exported.connect(self._clip_saved)
        self.remote = RemoteServer(self.cfg["remote_key"], self)
        self.cfg["remote_key"] = self.remote.key  # a mesma chave sempre: o QR lido continua valendo
        self.remote.command.connect(self._remote_cmd)
        if self.cfg["remote"]:
            self.remote.start()
        self._ts_sid = None          # sessão do buffer do canal atual (None = tocando direto)
        self._ts_origin = 0.0        # início (s) do pedaço em que a mídia atual do VLC começou
        self._paused_at = None       # ponto (ms) em que a pausa com buffer começou
        self.audio = AudioEngine()   # som tocado pelo programa enquanto a dublagem está ligada
        self.dubber = None
        self.captions = None         # IA de legendas (carregada na primeira vez que é ligada)
        self.subs = SubtitleOverlay(self)
        self._apply_caption_prefs()
        self.subs_timer = QTimer(self, interval=250, timeout=self._subs_tick)
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
        self._set_compact(self._narrow_screen())
        if self.cfg.data.get("geometry"):
            self.restoreGeometry(QByteArray.fromHex(self.cfg["geometry"].encode()))
            self.start_maximized = False
        self._set_view_mode(self.cfg["view_mode"])
        self._kids_button_text()
        self._select_category(self.cfg["last_category"])
        last = next((c for c in self.all_channels if c.url == self.cfg["last_url"]), None)
        if last:
            QTimer.singleShot(400, lambda: self.play(last))
        QTimer.singleShot(1500, lambda: self._reload_epg())
        QTimer.singleShot(4000, self._auto_update_lists)
        self.list_timer = QTimer(self, interval=3600 * 1000, timeout=self._auto_update_lists)
        self.list_timer.start()  # app aberto por muito tempo também recebe os links novos
        QTimer.singleShot(3000, self._check_updates)
        if self.cfg["captions"]:
            QTimer.singleShot(2500, lambda: self.cc_btn.setChecked(True))

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
        self.update_banner = make_btn("download", "", self._update_clicked, kind="banner", icon_color="white",
                                      icon_size=16)
        self.update_banner.hide()
        sl.addWidget(self.update_banner)
        for name, text, slot in (("smile", "Modo infantil", self.toggle_kids),
                                 ("phone", "Controle pelo celular", self.open_remote),
                                 ("plus", "Adicionar lista", self.add_list),
                                 ("refresh", "Atualizar canais", lambda: self.update_lists()),
                                 ("settings", "Configurações", self.open_settings)):
            b = make_btn(name, "", slot, kind="flat", text="  " + text, icon_color="muted", icon_size=17)
            self._side_btns.append((b, text))
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
        self.chan_panel.setMinimumWidth(290)
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

        # barra de tempo do buffer (pausar e voltar a TV ao vivo): só aparece quando o canal tem buffer
        self.seek_row = QWidget()
        sr = QHBoxLayout(self.seek_row)
        sr.setContentsMargins(0, 0, 0, 0)
        self.seek_pos_lbl = QLabel("", objectName="muted")
        self.seek = SeekSlider(Qt.Orientation.Horizontal)
        self.seek.setToolTip("Arraste ou clique para ir a qualquer ponto do que foi guardado")
        self.seek.sliderMoved.connect(self._seek_preview)
        self.seek.sliderPressed.connect(lambda: self._seek_preview(self.seek.value()))
        self.seek.sliderReleased.connect(self._seek_released)
        self.seek_end_lbl = QLabel("", objectName="muted")
        sr.addWidget(self.seek_pos_lbl)
        sr.addWidget(self.seek, 1)
        sr.addWidget(self.seek_end_lbl)
        self.seek_row.hide()
        v.addWidget(self.seek_row)

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
        self.play_btn = make_btn("play", "Assistir / pausar", self.toggle_pause, kind="play",
                                 icon_color="white", icon_size=22)
        top.addWidget(self.play_btn)
        top.addWidget(make_btn("next", "Próximo canal (PgDn)", lambda: self.step(+1)))
        top.addWidget(make_btn("stop", "Parar", self.stop))
        top.addSpacing(8)
        self.back_btn = make_btn("back", "Voltar 30 segundos (Ctrl+←)", lambda: self.seek_relative(-30))
        self.fwd_btn = make_btn("forward", "Avançar 30 segundos (Ctrl+→)", lambda: self.seek_relative(+30))
        self.live_btn = make_btn("live", "Ir para o ao vivo (Ctrl+End)", self.go_live)
        self.clip_btn = make_btn("film", "Salvar em vídeo o que acabou de passar", self._clip_menu)
        for b in (self.back_btn, self.fwd_btn, self.live_btn, self.clip_btn):
            b.setEnabled(False)
            top.addWidget(b)
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
        auto.setSpacing(6)
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
        self.minute_btn = make_btn("next", "Rolagem automática: passa para o próximo canal da lista "
                                   "a cada 1 minuto (Ctrl+Shift+Z)", kind="", text=" 1 min",
                                   checkable=True, icon_size=15)
        self.minute_btn.toggled.connect(self._toggle_minute)
        auto.addWidget(self.minute_btn)
        self.zap_spin = QSpinBox()
        self.zap_spin.setRange(5, 3600)
        self.zap_spin.setSuffix(" s")
        self.zap_spin.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        self.zap_spin.setFixedWidth(64)
        self.zap_spin.setToolTip("Intervalo do zapping (role o mouse ou digite)")
        self.zap_spin.setValue(self.cfg["zap_interval"])
        self.zap_spin.valueChanged.connect(lambda val: self.cfg.__setitem__("zap_interval", val))
        self.zap_spin.valueChanged.connect(lambda val: val != 60 and self._uncheck_minute())
        auto.addWidget(self.zap_spin)
        self.zap_mode = QComboBox()
        self.zap_mode.addItems(["Sequencial", "Aleatório"])
        self.zap_mode.setFixedWidth(112)
        self.zap_mode.setCurrentIndex(1 if self.cfg["zap_random"] else 0)
        self.zap_mode.currentIndexChanged.connect(lambda i: self.cfg.__setitem__("zap_random", i == 1))
        self.zap_mode.currentIndexChanged.connect(lambda i: i != 0 and self._uncheck_minute())
        auto.addWidget(self.zap_mode)
        self.zap_favs_cb = make_btn("star", "Zapping só pelos favoritos", kind="tool", checkable=True, icon_size=17)
        self.zap_favs_cb.setChecked(self.cfg["zap_favs"])
        self.zap_favs_cb.toggled.connect(lambda val: self.cfg.__setitem__("zap_favs", val))
        auto.addWidget(self.zap_favs_cb)
        self.explore_btn = make_btn("compass", "Explorar: passa sozinho por todos os canais de todas as listas, "
                                    "pulando os offline e os que você já viu (Ctrl+E)", kind="",
                                    text="  Explorar", checkable=True, icon_size=17)
        self.explore_btn.toggled.connect(self._toggle_explore)
        auto.addWidget(self.explore_btn)
        self.zap_lbl = QLabel("", objectName="muted")
        self.zap_lbl.setMinimumWidth(30)
        auto.addWidget(self.zap_lbl)
        self.info_lbl = QLabel("", objectName="muted")
        self.info_lbl.setOpenExternalLinks(True)
        self.info_lbl.setTextFormat(Qt.TextFormat.RichText)
        self.info_lbl.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.info_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        auto.addWidget(self.info_lbl, 1)
        self.cc_btn = make_btn("cc", "Legendas traduzidas por IA (Ctrl+T)", kind="tool", checkable=True,
                               icon_size=18)
        self.cc_btn.toggled.connect(self._toggle_captions)
        self.dub_btn = make_btn("dub", "Dublagem por IA: fala a tradução em português por cima do som "
                                "original (Ctrl+U)", kind="tool", checkable=True, icon_size=18)
        self.dub_btn.toggled.connect(self._toggle_dub)
        self.tracks_btn = make_btn("tracks", "Áudio e legenda do canal (Ctrl+Shift+A)", self._tracks_menu,
                                   kind="tool", icon_size=18)
        self.guide_btn = make_btn("guide", "Guia de programação (Ctrl+G)", self.open_guide, kind="tool", icon_size=18)
        self.snap_btn = make_btn("camera", "Tirar foto da tela (Ctrl+S)", self.snapshot, kind="tool", icon_size=18)
        self.rec_btn = make_btn("record", "Gravar canal (Ctrl+R)", self.toggle_record, kind="tool",
                                checkable=True, icon_size=18)
        self.pip_btn = make_btn("pip", "Janela flutuante (Ctrl+P)", self.enter_pip, kind="tool", icon_size=18)
        self.mosaic_btn = make_btn("mosaic", "Mosaico: vários canais ao mesmo tempo", self.open_mosaic,
                                   kind="tool", icon_size=18)
        self.sleep_btn = make_btn("timer", "Timer para desligar", self._sleep_menu, kind="tool", icon_size=18)
        for b in (self.cc_btn, self.dub_btn, self.tracks_btn, self.guide_btn, self.snap_btn, self.rec_btn, self.pip_btn, self.mosaic_btn, self.sleep_btn):
            auto.addWidget(b)
        self.sleep_lbl = QLabel("", objectName="muted")
        auto.addWidget(self.sleep_lbl)
        v.addLayout(auto)
        return w

    def _paint_brand(self):
        if self._compact:
            self.brand.setText(f"<span style='color:{T['accent']}'>▶</span>")
        else:
            self.brand.setText(f"▶ IPTV <span style='color:{T['accent']}'>Player</span>")

    def _set_compact(self, compact):
        """Barra lateral só com ícones em janelas estreitas (telas pequenas ou com zoom alto)."""
        if compact == self._compact:
            return
        self._compact = compact
        self.sidebar.setFixedWidth(64 if compact else 224)
        self.zap_btn.setText("" if compact else "  Zapping")
        self.explore_btn.setText("" if compact else "  Explorar")
        for b, text in self._side_btns:
            b.setText("" if compact else "  " + text)
            b.setToolTip(text if compact else "")
        self.side_lbl.setVisible(not compact)
        if self._banner_tag:
            self._show_update_banner(self._banner_tag)
        self._paint_brand()
        self._build_nav()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._place_subs()
        if not self.fullscreen:
            self._set_compact(self._narrow_screen() or self.width() < COMPACT_BELOW)

    def _narrow_screen(self):
        screen = self.screen() or QGuiApplication.primaryScreen()
        return screen.availableGeometry().width() < 1400

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
        sc("Ctrl+Shift+Z", self.minute_btn.toggle)
        sc("Ctrl+G", self.open_guide)
        sc("Ctrl+S", self.snapshot)
        sc("Ctrl+R", lambda: self.rec_btn.click())
        sc("Ctrl+P", self.enter_pip)
        sc("Ctrl+L", self._toggle_view_mode)
        sc("Ctrl+T", self.cc_btn.toggle)
        sc("Ctrl+U", self.dub_btn.toggle)
        sc("Ctrl+Shift+A", self._next_audio_track)
        sc("Ctrl+Left", lambda: self.seek_relative(-30))
        sc("Ctrl+Right", lambda: self.seek_relative(+30))
        sc("Ctrl+End", self.go_live)
        sc("Ctrl+E", self.explore_btn.toggle)

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
            it = QListWidgetItem(icon(ic, "muted", 18), "" if self._compact else "  " + label)
            it.setData(Qt.ItemDataRole.UserRole, key)
            it.setData(LABEL_ROLE, label)
            if self._compact:
                it.setToolTip(label)
            self.nav.addItem(it)

        def section(title):
            it = QListWidgetItem("" if self._compact else title.upper())
            it.setFlags(Qt.ItemFlag.NoItemFlags)
            f = QFont()
            f.setPointSizeF(7.5)
            f.setBold(True)
            f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 0.8)
            it.setFont(f)
            it.setForeground(QColor(T["muted"]))
            it.setSizeHint(QSize(10, 12 if self._compact else 30))
            self.nav.addItem(it)

        if self.cfg["kids"]:
            section("Modo infantil")
            for key, label, ic in self._all_categories():
                if key in self.cfg["kids_cats"]:
                    add(key, label, ic)
        else:
            add(src.FAV_KEY, "Favoritos", "star")
            add(src.RECENT_KEY, "Recentes", "clock")
            add(src.NEW_KEY, f"Novos ({len(self.newch)})" if len(self.newch) else "Novos", "sparkle")
            section("Canais")
            for key, label, ic in src.builtin_entries():
                add(key, label, ic)
        if self.cfg["custom_sources"] and not self.cfg["kids"]:
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

    def _kids_block(self):
        """No modo infantil, avisa e devolve True (a ação fica bloqueada)."""
        if self.cfg["kids"]:
            self._info("🔒 Bloqueado no modo infantil.", 5)
            return True
        return False

    def _nav_menu(self, pos):
        if self.cfg["kids"]:
            return
        it = self.nav.itemAt(pos)
        key = it.data(Qt.ItemDataRole.UserRole) if it else None
        if not key or key in src.SPECIAL_KEYS:
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
                return it.data(LABEL_ROLE) or it.text().strip()
        return key

    def _load_category(self, key):
        """Canais da categoria; dispara o download se ainda não houver arquivo local."""
        if key == src.FAV_KEY:
            return [Channel.from_dict(f) for f in self.cfg["favorites"]]
        if key == src.RECENT_KEY:
            return [Channel.from_dict(f) for f in self.cfg["recents"]]
        if key == src.NEW_KEY:
            return self.newch.channels()
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
        if self.cfg["kids"] and key not in self.cfg["kids_cats"]:
            key = self.cfg["kids_cats"][0]
        if not from_nav:
            for i in range(self.nav.count()):
                if self.nav.item(i).data(Qt.ItemDataRole.UserRole) == key:
                    self.nav.blockSignals(True)
                    self.nav.setCurrentRow(i)
                    self.nav.blockSignals(False)
                    break
        self.category = key
        self.all_channels = self._load_category(key)
        self.merged = 0
        if self.cfg["merge_dupes"] and key not in src.SPECIAL_KEYS:
            self.all_channels, self.merged = self._merge_dupes(self.all_channels)
        self.numbers = {c.url: i + 1 for i, c in enumerate(self.all_channels)}
        if key not in src.SPECIAL_KEYS and not self.cfg["kids"]:
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
        if getattr(self, "merged", 0):
            txt += f"  ·  {self.merged} repetidos juntados"
        if not self.all_channels and self.category == src.FAV_KEY:
            txt = "Sem favoritos ainda — use a estrela ★"
        if not self.all_channels and self.category == src.NEW_KEY:
            txt = "Nenhum canal novo nos últimos 3 dias"
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
        self.chan_panel.setMinimumWidth(2 * GridDelegate.CARD.width() + 52 if grid else 290)
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
        self._new_found = 0
        if jobs:
            self._side_status(f"Atualizando listas… 0/{len(jobs)}")
            self.upd_btn.setEnabled(False)
            self.downloader.start(jobs)

    def _on_list_done(self, key, ok):
        if not ok:
            return
        old = self.downloader.old_urls.pop(key, None)
        if old is None and not key.startswith(src.CUSTOM_PREFIX):
            old = list_urls(src.BASE / f"{key}.m3u")  # 1ª atualização: compara com a lista que veio no programa
        path = src.cache_path(key, self.cfg["custom_sources"])
        if old and path.exists():
            try:
                fresh = [c for c in parse_m3u(path)[0] if c.url not in old]
                self._new_found += self.newch.record(fresh)
            except OSError:
                pass
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
        log.info("Listas atualizadas: %d ok, %d com erro", ok, fail)
        if ok and fail:
            msg += f", {fail} com erro"
        if ok:
            self.links.invalidate()
            self.newch.save()
            self._build_nav()
            if self.category == src.NEW_KEY:
                self._select_category(src.NEW_KEY, from_nav=None)
        if self._new_found:
            msg += f" · ✨ {self._new_found} canais novos (veja em Novos)"
            self._info(f"✨ <b>{self._new_found} canais novos</b> chegaram nas listas — veja na categoria "
                       "<b>Novos</b>.", 12)
        self._side_status(msg)
        QTimer.singleShot(8000, lambda: self.side_lbl.text() == msg and self._side_status(""))
        self._reload_epg()

    def add_list(self):
        if self._kids_block():
            return
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
        GuideDialog(self.current, self.epg.schedule(self.current), self, scheduler=self).exec()

    # ================================================================ estado
    def status_of(self, url):
        st = self.session_status.get(url)
        if st not in (None, "dead"):
            return st
        t = self.cfg["dead"].get(url)  # a marca de offline vence sozinha depois de DEAD_TTL
        return "dead" if t and time.time() - t < DEAD_TTL else None

    def _expire_dead(self):
        """Canais cuja marca de offline venceu voltam para a lista (e para o zapping)."""
        if not self.cfg.prune_dead():
            return
        for u in [u for u, st in self.session_status.items() if st == "dead" and u not in self.cfg["dead"]]:
            del self.session_status[u]
        if self.hide_dead_cb.isChecked():
            self._apply_filter()
        else:
            self._update_count()
            self.view.viewport().update()

    def _mark(self, url, st):
        self.session_status[url] = st
        if st == "dead":
            self.cfg["dead"][url] = time.time()
        elif st == "ok":
            self.cfg["dead"].pop(url, None)
        self.view.viewport().update()

    def is_new(self, url):
        return self.newch.is_new(url)

    def is_fav(self, url):
        return any(f["url"] == url for f in self.cfg["favorites"])

    def _info(self, html, seconds=6):
        self.info_lbl.setText(html)
        QTimer.singleShot(seconds * 1000, lambda: self.info_lbl.text() == html and self.info_lbl.setText(""))

    # ================================================================ reprodução
    def play(self, ch, auto=False, force=False, stream=None):
        """stream = (url, opções) de um link alternativo; sem ele usa o link da lista (ou o que já substituiu)."""
        if ch is None:
            return
        if (not force and self.current and ch.url == self.current.url
                and time.monotonic() - self.started_at < 1.5):
            return  # clique duplo não reinicia o canal
        if self.current and self.session_status.get(self.current.url) == "loading":
            self.session_status.pop(self.current.url)
        log.info("Tocando: %s — %s", ch.name, (stream[0] if stream else ch.url))
        if not auto:
            self.fail_streak = 0
        self.current = ch
        self.retried = False
        if stream is None:
            self._alt_tried = set()
            fix = self.cfg["alt_links"].get(ch.url)
            stream = (fix["url"], fix["opts"]) if fix else (ch.url, ch.opts)
        self.stream = stream
        self._open_stream(stream)
        if self.captions:
            self.captions.reset()
        if self.dubber:
            self.dubber.reset()
        self.subs.clear()
        if self.tracks_btn.property("icon_color") == "accent":
            self.tracks_btn.setProperty("icon_color", "text")
            set_btn_icon(self.tracks_btn, "tracks")
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

    # ================================================================ pausar e voltar a TV ao vivo
    def _open_stream(self, stream):
        """Abre o canal pelo buffer local (pausar/voltar) quando dá; senão, direto como antes."""
        self.player.stop()
        self._ts_origin = 0.0
        self._paused_at = None
        self._ts_buttons()
        if self.cfg["timeshift"] and timeshift_supports(stream[0]):
            sid = self._ts_sid = self.timeshift.open(stream[0], stream[1])
            QTimer.singleShot(START_WAIT_MS, lambda: self._ts_too_slow(sid))
        else:
            self._ts_sid = None
            self.timeshift.close()
            self._set_media(stream[0], stream[1])

    def _set_media(self, url, opts):
        media = self.instance.media_new(url)
        for o in opts:
            media.add_option(o)
        self.player.set_media(media)
        self.player.play()

    def _ts_ready(self, sid, url):
        if sid == self._ts_sid and self.current:
            self.started_at = time.monotonic()  # o tempo guardando os primeiros pedaços não conta como demora
            self._set_media(url, [LIVE_DELAY_OPT])

    def _ts_failed(self, sid):
        if sid != self._ts_sid:
            return
        if self.player.get_media() is None or self.player.get_state() in (vlc.State.Stopped,
                                                                          vlc.State.NothingSpecial):
            # o buffer não deu certo antes de começar: toca direto, como antes
            self._ts_sid = None
            if self.current:
                self._set_media(*self.stream)
        # se já estava tocando, o próprio monitor percebe quando o canal parar

    def _ts_too_slow(self, sid):
        if sid == self._ts_sid and self.player.get_media() is None or (
                sid == self._ts_sid and self.player.get_state() in (vlc.State.Stopped, vlc.State.NothingSpecial)):
            log.info("Timeshift: servidor lento, tocando direto")
            self._ts_sid = None
            self.timeshift.close()
            if self.current:
                self._set_media(*self.stream)

    def _ts_active(self):
        return self._ts_sid is not None and self.confirmed

    def _ts_pos(self):
        """Ponto que está na tela, em segundos desde que o canal abriu."""
        return self._ts_origin + max(0, self.player.get_time()) / 1000

    def _ts_live_point(self):
        """Onde fica o "ao vivo": três pedaços antes do fim do que já foi guardado
        (com menos o VLC fica esperando a lista crescer antes de começar)."""
        _start, end = self.timeshift.span()
        return max(0.0, end - 3 * self.timeshift.target())

    def _ts_behind(self):
        """Segundos atrás do ao vivo (0 = ao vivo)."""
        base = self._paused_at if self._paused_at is not None else self._ts_pos()
        return max(0.0, self._ts_live_point() - base)

    def _ts_open_at(self, pos):
        """Toca a partir do pedaço que contém pos. O VLC começa sempre do início da lista local,
        então nunca precisa reposicionar dentro de um canal ao vivo (o que trava às vezes)."""
        # um pedaço antes: o VLC às vezes começa no segundo pedaço da lista, e assim nada se perde
        url, start = self.timeshift.url_at(pos - self.timeshift.target())
        if not url:
            return
        self._ts_origin = start
        self._paused_at = None
        self._set_media(url, [LIVE_DELAY_OPT])
        for b in (self.play_btn, self.overlay.play_btn):
            set_btn_icon(b, "pause")
        QTimer.singleShot(1500, self._ts_status)

    def seek_relative(self, seconds):
        if not self._ts_active():
            return self._info("Este canal não permite voltar ou avançar." if self.current else "")
        base = self._paused_at if self._paused_at is not None else self._ts_pos()
        start, _end = self.timeshift.span()
        self._ts_open_at(min(max(start, base + seconds), self._ts_live_point()))

    def go_live(self):
        if self._ts_active():
            self._ts_open_at(self._ts_live_point())

    def _ts_buttons(self):
        on = self._ts_active()
        for b in (self.back_btn, self.fwd_btn, self.live_btn, self.clip_btn):
            b.setEnabled(on)
        self.seek_row.setVisible(on)

    def _clip_menu(self):
        if not self._ts_active():
            return
        start, _end = self.timeshift.span()
        pos = self._paused_at if self._paused_at is not None else self._ts_pos()
        kept = pos - start
        menu = QMenu(self)
        for minutes in (1, 5, 10, 30):
            act = menu.addAction(f"Últimos {minutes} minuto{'s' if minutes > 1 else ''}")
            act.setEnabled(kept >= minutes * 60 * 0.5)
            act.triggered.connect(lambda _c=False, m=minutes: self._save_clip(pos - m * 60, pos))
        menu.addAction(f"Tudo o que foi guardado ({int(kept // 60)} min {int(kept % 60)} s)",
                       lambda: self._save_clip(start, pos))
        menu.exec(QCursor.pos())

    def _save_clip(self, start, end):
        ext = ".mp4" if self.timeshift.fmp4() and not self.timeshift.session.audio else ".ts"
        path = clip_path(self.current, ext)
        self._info("Salvando o trecho…", 4)
        self.timeshift.export(max(0.0, start), end, path)

    def _clip_saved(self, path, error):
        if error:
            return self._info(f"<span style='color:{T['bad']}'>Não deu para salvar o trecho: {error}</span>", 10)
        folder = QUrl.fromLocalFile(str(Path(path).parent)).toString()
        self._info(f"Trecho salvo em Vídeos · <a href='{folder}' style='color:{T['accent']}'>abrir pasta</a>", 10)

    @staticmethod
    def _fmt_behind(sec):
        m, s = divmod(int(max(0, sec)), 60)
        return f"-{m}:{s:02d}" if sec >= 1 else "ao vivo"

    def _seek_update(self):
        """Barra de tempo: do mais antigo guardado (esquerda) até o ao vivo (direita)."""
        if not self._ts_active() or self.seek.isSliderDown():
            return
        start, _end = self.timeshift.span()
        live = self._ts_live_point()
        pos = self._paused_at if self._paused_at is not None else self._ts_pos()
        self.seek.blockSignals(True)
        self.seek.setRange(int(start), max(int(start) + 1, int(live)))
        self.seek.setValue(int(min(max(pos, start), live)))
        self.seek.blockSignals(False)
        self.seek_pos_lbl.setText(self._fmt_behind(live - pos))
        kept = live - start
        self.seek_end_lbl.setText(f"{int(kept // 60)}:{int(kept % 60):02d} guardados")

    def _seek_preview(self, value):
        self.seek_pos_lbl.setText(self._fmt_behind(self.seek.maximum() - value))

    def _seek_released(self):
        if self._ts_active():
            self._ts_open_at(float(self.seek.value()))

    def _ts_status(self):
        """Mostra "Ao vivo" ou quanto está atrás, e quanto o buffer já guardou."""
        if not self._ts_active():
            return
        kept = self.timeshift.buffered()
        self.live_btn.setToolTip(f"Ir para o ao vivo (Ctrl+End)\nGuardando os últimos "
                                 f"{self.cfg['timeshift_minutes']} min — já tem {int(kept // 60)} min "
                                 f"{int(kept % 60)} s")
        self._seek_update()
        behind = self._ts_behind()
        if self._paused_at is not None:
            m, s = divmod(int(behind), 60)
            self._set_status(f"❚❚ Pausado — {m}:{s:02d} atrás do ao vivo", "warn")
        elif behind > 2 * self.timeshift.target() + 5:  # folga: recarregar leva alguns segundos
            m, s = divmod(int(behind), 60)
            self._set_status(f"◷ {m}:{s:02d} atrás do ao vivo", "warn")
        elif self._status[0].startswith(("◷", "❚❚")):
            self._set_status("● Ao vivo", "ok")

    def _replay(self):
        if self.current:
            self.play(self.current, auto=True, force=True, stream=self.stream)

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
            self._ts_status()  # o "atrás do ao vivo" continua aumentando durante a pausa
            return
        t = self.player.get_time()
        if not self.confirmed:
            if st == vlc.State.Playing and (self.player.has_vout() or t > 800):
                self.confirmed = True
                self.fail_streak = 0
                self.last_time, self.last_progress = t, now
                self._mark(self.current.url, "ok")
                self._remember_stream()
                if self.explore_btn.isChecked():
                    self.explored.add(self.current.url)
                self._set_status("● Ao vivo", "ok")
                self.video.set_message("")
                self.player.audio_set_volume(self.vol.value())
                self._ts_buttons()
                self._apply_saved_tracks()
                url = self.current.url  # faixas de áudio e legenda às vezes só aparecem depois de alguns segundos
                QTimer.singleShot(3000, lambda: self.current and self.current.url == url and self._apply_saved_tracks())
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
            self._ts_status()
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
        log.warning("Canal falhou: %s (%s)", ch.name, reason)
        if not self.explore_btn.isChecked() and self._try_alternative(ch):
            return
        self._mark(ch.url, "dead")
        if self.explore_btn.isChecked():
            self.player.stop()
            self._set_status(f"✕ Offline ({reason}) — explorando o próximo…", "bad")
            self.video.set_message(f"{ch.name} está offline — indo para o próximo…")
            self.confirmed = True
            self.last_progress = float("inf")
            QTimer.singleShot(350, lambda: self.current is ch and self._explore_next())
            return
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

    # ================================================================ links alternativos
    def _link_files(self):
        keys = [k for k, *_ in src.builtin_entries()] + [src.custom_key(s) for s in self.cfg["custom_sources"]]
        return [p for p in (src.file_for(k, self.cfg["custom_sources"]) for k in keys) if p]

    def _try_alternative(self, ch):
        """Canal caiu: procura o mesmo canal em outro link (de qualquer lista) antes de desistir."""
        self._alt_tried.add(self.stream[0])
        if len(self._alt_tried) > MAX_ALT_TRIES:
            return False
        if self.links.by_key is None:
            self._set_status("↻ Procurando outro link deste canal…", "warn")
            QApplication.processEvents()
            self.links.build(self._link_files())
        alts = self.links.alternatives(ch, self._alt_tried, lambda u: self.status_of(u) == "dead")
        if not alts:
            return False
        alt = alts[0]
        self._alt_tried.add(alt.url)
        self.play(ch, auto=True, force=True, stream=(alt.url, alt.opts))
        self._set_status(f"↻ Link fora do ar — tentando outro ({len(self._alt_tried) - 1}/{MAX_ALT_TRIES})…", "warn")
        return True

    def _remember_stream(self):
        """Guarda o link que funcionou para o canal abrir direto por ele da próxima vez."""
        ch, (url, opts) = self.current, self.stream
        known = self.cfg["alt_links"]
        if url != ch.url:
            if known.get(ch.url, {}).get("url") != url:
                known[ch.url] = {"url": url, "opts": list(opts)}
                self._info(f"Link de <b>{ch.name}</b> estava fora do ar: trocado automaticamente por outro "
                           "que funciona.", 8)
        elif ch.url in known:
            known.pop(ch.url)  # o link original voltou

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
        resuming = self.player.get_state() == vlc.State.Paused
        if self._ts_active():
            # com o buffer: pausa normal; ao continuar, toca de novo a partir do ponto da pausa
            # (despausar um canal ao vivo faria o VLC pular para perto do "agora")
            if resuming and self._paused_at is not None:
                return self._ts_open_at(self._paused_at)
            self._paused_at = self._ts_pos()
            self.player.pause()
            for b in (self.play_btn, self.overlay.play_btn):
                set_btn_icon(b, "play")
            return self._ts_status()
        self.player.pause()
        paused = not resuming
        for b in (self.play_btn, self.overlay.play_btn):
            set_btn_icon(b, "play" if paused else "pause")

    def stop(self):
        self.player.stop()
        self.timeshift.close()
        self._ts_sid = None
        self._ts_buttons()
        self.explore_btn.setChecked(False)
        if self.current and self.session_status.get(self.current.url) == "loading":
            self.session_status.pop(self.current.url)
        self.current = None
        self.zap_btn.setChecked(False)
        self._status = ("", "muted")
        set_btn_icon(self.play_btn, "play")
        self.video.set_message("Escolha um canal na lista")
        self.subs.clear()
        self._update_now_info()
        self.view.viewport().update()

    def _set_volume(self, v):
        self.cfg["volume"] = v
        self.player.audio_set_volume(v)
        self.audio.volume = v / 100
        if v and self.player.audio_get_mute():
            self.player.audio_set_mute(False)
        for b in (self.mute_btn, self.overlay.mute_btn):
            set_btn_icon(b, "mute" if v == 0 else "volume")
        self._caption_sound_notice()
        for s in (self.vol, self.overlay.vol):
            if s.value() != v:
                s.blockSignals(True)
                s.setValue(v)
                s.blockSignals(False)

    def toggle_mute(self):
        m = not (self.audio.muted if self.audio.attached else self.player.audio_get_mute() == 1)
        self.player.audio_set_mute(m)
        self.audio.muted = m
        for b in (self.mute_btn, self.overlay.mute_btn):
            set_btn_icon(b, "mute" if m else "volume")
        self._caption_sound_notice(m)

    # ================================================================ zapping
    def _zap_pool(self):
        if self.zap_favs_cb.isChecked():
            return [Channel.from_dict(f) for f in self.cfg["favorites"]] or None
        return None

    def _toggle_zap(self, on):
        if on and self.explore_btn.isChecked():
            self.explore_btn.setChecked(False)
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
            self._uncheck_minute()
        self._zap_render()

    def _toggle_minute(self, on):
        """Rolagem automática: zapping sequencial pela lista, trocando a cada 1 minuto."""
        if on:
            self.zap_spin.setValue(60)
            self.zap_mode.setCurrentIndex(0)
            if self.zap_btn.isChecked():
                self.zap_left = 60
                self._zap_render()
            else:
                self.zap_btn.setChecked(True)
        elif self.zap_btn.isChecked():
            self.zap_btn.setChecked(False)

    def _uncheck_minute(self):
        if self.minute_btn.isChecked():
            self.minute_btn.blockSignals(True)
            self.minute_btn.setChecked(False)
            self.minute_btn.blockSignals(False)

    def _zap_tick(self):
        exploring = self.explore_btn.isChecked()
        if not (self.zap_btn.isChecked() or exploring) or not self.current or not self.confirmed:
            return  # só conta tempo enquanto o canal está realmente tocando
        if self.player.get_state() == vlc.State.Paused:
            return
        self.zap_left -= 1
        if self.zap_left <= 0:
            if exploring:
                self._explore_next()
            else:
                self.step(+1, auto=True, random_pick=self.zap_mode.currentIndex() == 1, pool=self._zap_pool())
        self._zap_render()

    def _zap_render(self):
        total = self.zap_spin.value()
        self.zap_bar.setRange(0, total)
        self.zap_bar.setValue(max(0, total - self.zap_left))
        if self.zap_btn.isChecked() or self.explore_btn.isChecked():
            self.zap_lbl.setText(f"{max(0, self.zap_left)}s")

    # ================================================================ modo Explorar
    def _toggle_explore(self, on):
        if on and self._kids_block():
            self.explore_btn.setChecked(False)
            return
        if on and self.zap_btn.isChecked():
            self.zap_btn.setChecked(False)
        self.zap_bar.setVisible(on)
        if on:
            self._explore_next()
        else:
            self.zap_lbl.setText("")
            self.info_lbl.setText("")
            self.explored.save()
        self._zap_render()

    def _explore_order(self):
        return [k for k, *_ in src.builtin_entries()] + [src.custom_key(s) for s in self.cfg["custom_sources"]]

    def _explore_next(self, restarted=False):
        """Próximo canal ainda não visto, atravessando todas as listas; a lista rola acompanhando."""
        order = self._explore_order()
        if self.explore_cat not in order:
            self.explore_cat = self.category if self.category in order else order[0]
            self.explore_idx = -1
        first = order.index(self.explore_cat)
        cur_url = self.current.url if self.current else None
        for step_n in range(len(order) + 1):
            key = order[(first + step_n) % len(order)]
            chans = self._load_category(key)
            start = self.explore_idx + 1 if step_n == 0 else 0
            for i in range(start, len(chans)):
                c = chans[i]
                if c.url in self.explored or c.url == cur_url or self.status_of(c.url) == "dead":
                    continue
                self.explore_cat, self.explore_idx = key, i
                self.cfg["explore_pos"] = [key, i]
                if self.category != key or self.search.text() or self.group_box.currentIndex() > 0:
                    self.search.blockSignals(True)
                    self.search.clear()
                    self.search.blockSignals(False)
                    self.group_box.setCurrentIndex(0)
                    self._select_category(key)
                self.play(c, auto=True, force=True)
                self._select_current_in_view()
                self.info_lbl.setText(f"<span style='color:{T['accent']}'>🧭 Explorando</span> "
                                      f"<b>{self._label_for(key)}</b> · canal {i + 1} de {len(chans)} · "
                                      f"{len(self.explored)} já vistos")
                return
        if restarted:
            self.explore_btn.setChecked(False)
            return self._info("Nenhum canal disponível para explorar agora (listas ainda baixando?).")
        self.explored.clear()
        self.explore_cat, self.explore_idx = order[0], -1
        self._info("🎉 Você já explorou todos os canais! Recomeçando do início.", 10)
        self._explore_next(restarted=True)

    # ================================================================ relógio (1s): zapping, timer, gravação, EPG
    def _clock_tick(self):
        self._zap_tick()
        now = time.time()
        if now - self._dead_checked >= 600:
            self._dead_checked = now
            self._expire_dead()
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
        if int(now) % 5 == 0:
            self._check_schedule(now)
        if int(now) % 600 == 0:
            self._maybe_auto_scan()
        if self.remote.running:
            self._remote_state()
        if int(now) % 30 == 0:
            self._update_now_info()
            self.view.viewport().update()

    # ================================================================ modo infantil
    def _all_categories(self):
        out = list(src.builtin_entries())
        out += [(src.custom_key(s), s["name"], "folder") for s in self.cfg["custom_sources"]]
        return out

    def _kids_button_text(self):
        text = "Sair do modo infantil" if self.cfg["kids"] else "Modo infantil"
        for b, t in self._side_btns:
            if t in ("Modo infantil", "Sair do modo infantil"):
                b.setText("" if self._compact else "  " + text)
                b.setToolTip(text if self._compact else "")
                self._side_btns[self._side_btns.index((b, t))] = (b, text)
                break

    def toggle_kids(self):
        if self.cfg["kids"]:
            pin, ok = QInputDialog.getText(self, "Sair do modo infantil", "Senha:", QLineEdit.EchoMode.Password)
            if not ok:
                return
            if pin_hash(pin.strip()) != self.cfg["kids_pin"]:
                log.warning("Modo infantil: senha errada")
                return self._info(f"<span style='color:{T['bad']}'>Senha errada.</span>", 5)
            self.cfg["kids"] = False
            log.info("Modo infantil desligado")
        else:
            cats = [(k, label) for k, label, _ic in self._all_categories()]
            if KidsDialog(self.cfg, cats, self).exec() != QDialog.DialogCode.Accepted:
                return
            self.cfg["kids"] = True
            self.explore_btn.setChecked(False)
            log.info("Modo infantil ligado: %s", ", ".join(self.cfg["kids_cats"]))
        self.cfg.save()
        self._kids_button_text()
        self._build_nav()
        self._select_category(self.cfg["kids_cats"][0] if self.cfg["kids"] else self.cfg["last_category"])
        if self.cfg["kids"] and self.current and not any(c.url == self.current.url for c in self.all_channels):
            self.stop()  # o canal que estava tocando não é de uma categoria liberada

    # ================================================================ controle pelo celular
    def open_remote(self):
        from PyQt6.QtWidgets import QDialog, QVBoxLayout
        dlg = QDialog(self)
        dlg.setWindowTitle("Controle pelo celular")
        v = QVBoxLayout(dlg)
        v.setContentsMargins(20, 16, 20, 16)
        on = QCheckBox("Ligar o controle pelo celular")
        on.setChecked(self.remote.running)
        qr = QLabel(alignment=Qt.AlignmentFlag.AlignCenter)
        url = QLabel(alignment=Qt.AlignmentFlag.AlignCenter, objectName="muted")
        url.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        tip = QLabel("Aponte a câmera do celular para o código (o celular precisa estar no mesmo Wi-Fi deste PC). "
                     "Se o Windows perguntar sobre o firewall, permita em <b>redes privadas</b>. Usa VPN (Surfshark, "
                     "NordVPN…)? Ative nela a opção de <b>permitir acesso à rede local</b>.",
                     wordWrap=True, objectName="muted")

        def refresh():
            if self.remote.running:
                qr.setPixmap(qr_pixmap(self.remote.url(), 260))
                url.setText(self.remote.url())
            else:
                qr.setPixmap(QPixmap())
                qr.setText("Desligado")
                url.setText("")

        def toggle(checked):
            ok = self.remote.start() if checked else (self.remote.stop() or True)
            if checked and not ok:
                on.setChecked(False)
                self._info(f"<span style='color:{T['bad']}'>Não foi possível ligar: a porta já está em uso.</span>")
            self.cfg["remote"] = self.remote.running
            refresh()

        def new_key():
            import secrets
            self.remote.key = self.cfg["remote_key"] = secrets.token_urlsafe(6)
            refresh()
        on.toggled.connect(toggle)
        regen = make_btn(None, "O QR antigo para de funcionar (útil se alguém de fora o tiver lido)",
                         new_key, kind="", text="Gerar novo código")
        for w in (on, qr, url, tip, regen):
            v.addWidget(w)
        refresh()
        dlg.exec()

    def _remote_state(self):
        st = self.player.get_state()
        cats = [(src.FAV_KEY, "Favoritos"), (src.RECENT_KEY, "Recentes")]
        cats += [(k, label) for k, label, _ic in self._all_categories()]
        if self.cfg["kids"]:
            cats = [c for c in cats if c[0] in self.cfg["kids_cats"]]
        self.remote.state = {
            "name": self.current.name if self.current else "",
            "url": self.current.url if self.current else "",
            "status": self._status[0],
            "paused": st == vlc.State.Paused,
            "muted": self.audio.muted if self.audio.attached else self.player.audio_get_mute() == 1,
            "volume": self.vol.value(),
            "cc": self.cc_btn.isChecked(),
            "dub": self.dub_btn.isChecked(),
            "cat_key": self.category,
            "cats": cats,
            "channels": [{"name": c.name, "url": c.url, "num": self.numbers.get(c.url, "")}
                         for c in self.visible[:800]],
        }

    def _remote_cmd(self, cmd, arg):
        log.info("Celular: %s %s", cmd, arg)
        if cmd in ("prev", "next"):
            self.step(-1 if cmd == "prev" else +1)
        elif cmd == "pause":
            self.toggle_pause()
        elif cmd in ("back", "fwd"):
            self.seek_relative(-30 if cmd == "back" else 30)
        elif cmd == "live":
            self.go_live()
        elif cmd == "mute":
            self.toggle_mute()
        elif cmd == "vol" and arg.isdigit():
            self.vol.setValue(int(arg))
        elif cmd == "cc":
            self.cc_btn.toggle()
        elif cmd == "dub":
            self.dub_btn.toggle()
        elif cmd == "fullscreen":
            self.toggle_fullscreen()
        elif cmd == "play" and arg.isdigit() and int(arg) < len(self.visible):
            self.play(self.visible[int(arg)])
        elif cmd == "cat" and arg and (not self.cfg["kids"] or arg in self.cfg["kids_cats"]):
            self._select_category(arg)
        self._remote_state()

    # ================================================================ lembretes e gravações agendadas
    REMIND_BEFORE = 60           # aviso 1 min antes
    RECORD_BEFORE = 60           # gravação começa 1 min antes...
    RECORD_AFTER = 120           # ...e termina 2 min depois (programas costumam atrasar)

    def schedule_items(self):
        return sorted(self.cfg["schedule"], key=lambda i: i["start"])

    def scheduled_kinds(self, ch, start):
        return {i["kind"] for i in self.cfg["schedule"] if i["ch"]["url"] == ch.url and i["start"] == start}

    def toggle_schedule(self, kind, ch, start, stop, title):
        items = self.cfg["schedule"]
        found = [i for i in items if i["kind"] == kind and i["ch"]["url"] == ch.url and i["start"] == start]
        if found:
            self.cancel_schedule(found[0])
            return False
        items.append({"kind": kind, "ch": ch.__dict__.copy(), "start": start, "stop": stop, "title": title,
                      "state": "waiting"})
        self.cfg.save()
        log.info("Agendado (%s): %s em %s às %s", kind, title, ch.name, time.strftime("%d/%m %H:%M",
                                                                                  time.localtime(start)))
        return True

    def cancel_schedule(self, item):
        if item.get("state") == "recording" and self.recorder.active:
            self._stop_scheduled_recording(item)
        if item in self.cfg["schedule"]:
            self.cfg["schedule"].remove(item)
            self.cfg.save()

    def _notify(self, title, text, ch=None):
        """Aviso do Windows (canto da tela); clicar nele leva ao canal."""
        from PyQt6.QtWidgets import QSystemTrayIcon
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        if not getattr(self, "tray", None):
            self.tray = QSystemTrayIcon(app_icon(), self)
            self.tray.setToolTip("IPTV Player")
            self.tray.messageClicked.connect(self._notify_clicked)
            self.tray.show()
        self._notify_ch = ch
        self.tray.showMessage(title, text, app_icon(), 15000)

    def _notify_clicked(self):
        ch = getattr(self, "_notify_ch", None)
        if ch:
            self.showNormal() if self.isMinimized() else None
            self.raise_()
            self.activateWindow()
            self.play(ch)

    def _check_schedule(self, now):
        changed = False
        for item in list(self.cfg["schedule"]):
            ch = Channel.from_dict(item["ch"])
            if item["kind"] == "remind":
                if now >= item["start"] - self.REMIND_BEFORE:
                    if now < item["stop"]:
                        self._notify(f"Vai começar: {item['title']}",
                                     f"{ch.name} às {time.strftime('%H:%M', time.localtime(item['start']))}"
                                     " — clique para assistir", ch)
                        self._info(f"🔔 {item['title']} vai começar em {ch.name}", 15)
                    self.cfg["schedule"].remove(item)
                    changed = True
            elif item["state"] == "waiting" and now >= item["start"] - self.RECORD_BEFORE:
                if now >= item["stop"]:
                    self.cfg["schedule"].remove(item)  # o programa já acabou (o programa estava fechado)
                    changed = True
                elif self.recorder.active:
                    log.warning("Gravação agendada de %s não começou: já há outra gravação", item["title"])
                    self._notify("Gravação não começou", f"{item['title']}: já há outra gravação em andamento")
                    self.cfg["schedule"].remove(item)
                    changed = True
                else:
                    self.record_channel(ch)
                    item["state"] = "recording"
                    changed = True
                    log.info("Gravação agendada começou: %s em %s", item["title"], ch.name)
                    self._notify("Gravando", f"{item['title']} em {ch.name}")
            elif item["state"] == "recording" and now >= item["stop"] + self.RECORD_AFTER:
                self._stop_scheduled_recording(item)
                self.cfg["schedule"].remove(item)
                changed = True
        if changed:
            self.cfg.save()

    def _stop_scheduled_recording(self, item):
        path = self.recorder.stop()
        self.rec_btn.setChecked(False)
        self.info_lbl.setText("")
        log.info("Gravação agendada terminou: %s (%s)", item["title"], path)
        if path:
            self._notify("Gravação concluída", f"{item['title']} — salvo em Vídeos\\IPTV Player")

    # ================================================================ timer para desligar
    # ================================================================ faixas de áudio e legenda
    def _track_lists(self):
        """Faixas de áudio e de legenda do canal tocando, como [(id, nome)] (sem a opção "desligar")."""
        def names(desc):
            return [(i, n.decode("utf-8", "replace") if isinstance(n, bytes) else str(n))
                    for i, n in (desc or []) if i >= 0]
        if not self.current:
            return [], []
        return names(self.player.audio_get_track_description()), names(self.player.video_get_spu_description())

    def _tracks_menu(self):
        audio, spu = self._track_lists()
        m = QMenu(self)

        def header(text):
            m.addAction(text).setEnabled(False)

        def option(text, checked, fn):
            a = m.addAction(text, fn)
            a.setCheckable(True)
            a.setChecked(checked)

        if len(audio) < 2 and not spu:
            header("Este canal não tem outros áudios nem legendas")
        if len(audio) >= 2:
            header("Áudio")
            cur = self.player.audio_get_track()
            for i, name in audio:
                option(name, i == cur, lambda i=i, n=name: self._set_track("audio", i, n))
        if spu:
            if len(audio) >= 2:
                m.addSeparator()
            header("Legenda do canal")
            cur = self.player.video_get_spu()
            option("Desligada", cur < 0, lambda: self._set_track("spu", -1, ""))
            for i, name in spu:
                option(name, i == cur, lambda i=i, n=name: self._set_track("spu", i, n))
        m.exec(self.tracks_btn.mapToGlobal(QPoint(0, -m.sizeHint().height() - 4)))

    def _set_track(self, kind, track_id, name):
        """Troca a faixa e lembra a escolha para este canal."""
        if kind == "audio":
            self.player.audio_set_track(track_id)
        else:
            self.player.video_set_spu(track_id)
        if self.current:
            self.cfg["tracks"].setdefault(self.current.url, {})[kind] = name
            self.cfg.save()
        self._show_marquee(f"{'Áudio' if kind == 'audio' else 'Legenda'}: {name or 'desligada'}")

    def _next_audio_track(self):
        audio, _ = self._track_lists()
        if len(audio) < 2:
            return self._show_marquee("Este canal só tem um áudio")
        ids = [i for i, _ in audio]
        cur = self.player.audio_get_track()
        i, name = audio[(ids.index(cur) + 1) % len(ids) if cur in ids else 0]
        self._set_track("audio", i, name)

    def _apply_saved_tracks(self):
        """Volta a usar o áudio e a legenda escolhidos da última vez neste canal; destaca o botão
        quando o canal tem outras faixas."""
        audio, spu = self._track_lists()
        self.tracks_btn.setProperty("icon_color", "accent" if len(audio) >= 2 or spu else "text")
        set_btn_icon(self.tracks_btn, "tracks")
        pref = self.cfg["tracks"].get(self.current.url) if self.current else None
        if not pref:
            return
        want = pref.get("audio")
        for i, name in audio:
            if name == want and i != self.player.audio_get_track():
                self.player.audio_set_track(i)
        if "spu" in pref:
            want = pref["spu"]
            target = next((i for i, name in spu if name == want), None) if want else -1
            if target is not None and target != self.player.video_get_spu():
                self.player.video_set_spu(target)

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
        if self.current and ch.url == self.current.url and self.stream:
            ch = replace(ch, url=self.stream[0], opts=list(self.stream[1]))  # grava o link que está funcionando
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
    # ================================================================ canais repetidos e teste automático
    def _merge_dupes(self, channels):
        """Um canal por nome/ID do guia: fica o melhor link; os outros viram reserva automática
        (os links alternativos são procurados pelo mesmo nome/ID quando o canal cai)."""
        def score(c):
            st = self.status_of(c.url)
            q = int(re.sub(r"\D", "", c.quality) or 0)
            return (st == "ok", st != "dead", q)
        def key(c):
            if c.tvg_id:  # mesma emissora em outra qualidade: AajTak.in@SD = AajTak.in@HD
                base, _, feed = c.tvg_id.lower().partition("@")
                return f"id:{base}@{re.sub(r'(fhd|uhd|hd|sd|4k)$', '', feed)}"
            n = norm_channel_name(c.name)
            return "nome:" + n if len(n) >= 3 else "url:" + c.url
        groups, order = {}, []
        for c in channels:
            k = key(c)
            if k not in groups:
                groups[k] = c
                order.append(k)
            elif score(c) > score(groups[k]):
                groups[k] = c
        return [groups[k] for k in order], len(channels) - len(order)

    def _maybe_auto_scan(self):
        if not self.cfg["auto_scan"] or self.auto_scanner.running:
            return
        resuming = self.scan_progress.active  # rodada interrompida quando o programa foi fechado
        if not resuming and time.time() - self.cfg["last_full_scan"] < AUTO_SCAN_EVERY:
            return
        seen, targets = set(), []
        keys = [k for k, *_ in src.builtin_entries()] + [src.custom_key(s) for s in self.cfg["custom_sources"]]
        for key in keys:
            path = src.file_for(key, self.cfg["custom_sources"])
            if not path:
                continue
            try:
                chans = self.playlists[key][0] if key in self.playlists else parse_m3u(path)[0]
            except OSError:
                continue
            for c in chans:
                if c.url not in seen:
                    seen.add(c.url)
                    if c.url not in self.scan_progress:
                        targets.append(c)
        if not resuming:
            self.scan_progress.begin()
        if not targets:
            self._on_auto_scan_finished()
            return
        log.info("Teste automático: %d canais%s", len(targets),
                 f" (continuando; {len(seen) - len(targets)} já testados)" if resuming else "")
        self._auto_counts = [0, 0]
        self.auto_scanner.start(targets)

    def _on_auto_scan_result(self, url, ok):
        self.scan_progress.add(url)
        if self.current and self.current.url == url:
            return
        self._auto_counts[0 if ok else 1] += 1
        self.session_status[url] = "ok" if ok else "dead"
        if ok:
            self.cfg["dead"].pop(url, None)
        else:
            self.cfg["dead"][url] = time.time()
        if sum(self._auto_counts) % 300 == 0:
            self.view.viewport().update()

    def _on_auto_scan_finished(self):
        if self.auto_scanner.cancelled:  # programa fechando: guarda o progresso para continuar depois
            self.scan_progress.save()
            return
        ok, dead = self._auto_counts
        log.info("Teste automático terminou: %d no ar, %d fora do ar", ok, dead)
        self.cfg["last_full_scan"] = time.time()
        self.scan_progress.finish()
        self.cfg.save()
        if self.hide_dead_cb.isChecked():
            self._select_category(self.category, from_nav=None)
        else:
            self._update_count()

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
        o.captions_toggled.connect(self.cc_btn.setChecked)
        o.volume_changed.connect(self.vol.setValue)
        o.vol.setValue(self.vol.value())
        o.apply_style()

    def toggle_fullscreen(self):
        self.fullscreen = not self.fullscreen
        for w in (self.sidebar, self.chan_panel, self.controls):
            w.setVisible(not self.fullscreen)
        if self.fullscreen:
            self._was_maximized = self.isMaximized()
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
            self.showMaximized() if self._was_maximized else self.showNormal()

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
        if self.subs.parentWidget() is pip:  # senão a legenda seria apagada junto com a janela flutuante
            self.subs.set_owner(self)
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
        self.explore_btn.setChecked(False)
        self.dub_btn.setChecked(False)
        self._mosaic_prev = self.current
        self.player.stop()
        self.timeshift.close()
        self._ts_sid = None
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

    # ================================================================ legendas traduzidas (IA)
    def _toggle_captions(self, on):
        if on and missing_deps():
            self.cc_btn.setChecked(False)
            QMessageBox.information(
                self, "Legendas traduzidas",
                "Esta versão do IPTV Player não inclui a IA de legendas.\n\n"
                f"Para usar pelo código-fonte, instale:  pip install {' '.join(missing_deps())}")
            return
        for b in (self.cc_btn, self.overlay.cc_btn):
            if b.isChecked() != on:
                b.blockSignals(True)
                b.setChecked(on)
                b.blockSignals(False)
        self.cfg["captions"] = on
        if on:
            if not self.captions:
                self._start_caption_worker()
            self.captions.set_active(True)
            self.subs_timer.start()
            self._place_subs()
            self._caption_sound_notice()
        else:
            if self.captions and not self.dub_btn.isChecked():
                self.captions.set_active(False)  # a IA continua carregada para religar na hora
            self.subs_timer.stop()
            self.subs.clear()

    # ================================================================ dublagem por IA
    def _toggle_dub(self, on):
        if on and missing_deps():
            self.dub_btn.setChecked(False)
            QMessageBox.information(
                self, "Dublagem por IA",
                "Esta versão do IPTV Player não inclui a IA de legendas, que a dublagem usa.\n\n"
                f"Para usar pelo código-fonte, instale:  pip install {' '.join(missing_deps())}")
            return
        if on == self.audio.attached:
            return
        log.info("Dublagem %s", "ligada" if on else "desligada")
        # o som muda de caminho (VLC <-> programa): o canal recomeça para valer
        self.player.stop()
        if on:
            self.audio.volume = self.vol.value() / 100
            self.audio.muted = False
            self.audio.duck_level = self.cfg["dub_duck"]
            self.audio.ai_sink = lambda block: self.captions and self.captions.feed(block)
            self.audio.attach(self.player)
            self._new_dubber()
            if not self.captions:
                self._start_caption_worker()
            self.captions.use_tap(True)
            self.captions.set_active(True)
            if not self.cc_btn.isChecked():
                self._info("Dublagem ligada: a IA vai falar a tradução em português. "
                           "Ligue também a legenda (Ctrl+T) se quiser ler.", 8)
        else:
            if self.dubber:
                self.dubber.stop()
                self.dubber = None
            self.audio.detach()
            if self.captions:
                self.captions.use_tap(False)
                self.captions.set_active(self.cc_btn.isChecked())
            self.player.audio_set_volume(self.vol.value())
        self._replay()

    def _new_dubber(self):
        self.dubber = Dubber(self.audio, self.cfg["dub_voice"], self)
        self.dubber.status.connect(lambda t: self._info(t, 8) if t else self.info_lbl.setText(""))

    def _start_caption_worker(self):
        w = CaptionWorker(self.cfg["cap_model"], self.cfg["cap_source"], self.cfg["cap_translator"])
        w.caption.connect(self._on_caption)
        w.partial.connect(lambda _o, t: self.cc_btn.isChecked() and self.subs.set_preview(t))
        w.status.connect(self._on_caption_status)
        w.failed.connect(self._on_caption_failed)
        w.finished.connect(w.deleteLater)
        self.captions = w
        w.use_tap(self.audio.attached)
        w.start()
        w.set_active(self.cc_btn.isChecked() or self.dub_btn.isChecked())

    def _stop_caption_worker(self, wait=False):
        w, self.captions = self.captions, None
        if w:
            for sig in (w.caption, w.partial, w.status, w.failed):
                sig.disconnect()
            w.stop()
            if wait:
                w.wait(4000)

    def _on_caption(self, original, translated, lang, replace):
        if self.cc_btn.isChecked():
            self.subs.add(translated or original, original, replace)
        if self.dubber and translated:
            self.dubber.on_caption(translated, lang, replace)

    def _on_caption_status(self, text):
        if self.cc_btn.isChecked():
            self.subs.set_notice(text)

    def _on_caption_failed(self, msg):
        log.error("Legendas: %s", msg)
        self.dub_btn.setChecked(False)
        self._stop_caption_worker()
        self.cc_btn.setChecked(False)
        self._info(f"<span style='color:{T['bad']}'>{msg}</span>", 12)

    def _caption_sound_notice(self, muted=None):
        if not self.cc_btn.isChecked():
            return
        if self.audio.attached:  # com a dublagem a IA ouve o player direto: o volume não importa
            return self.subs.set_notice("") if self.subs.notice.startswith("Som desligado") else None
        if muted is None:
            muted = self.player.audio_get_mute() == 1
        if muted or self.vol.value() == 0:
            self.subs.set_notice("Som desligado: a IA precisa ouvir o canal para legendar")
        elif self.subs.notice.startswith("Som desligado"):
            self.subs.set_notice("")

    def _apply_caption_prefs(self):
        self.subs.scale = self.cfg["cap_scale"]
        self.subs.show_original = self.cfg["cap_original"]
        self.subs.video_rect = QRect()  # força refazer o layout com o novo tamanho
        self._place_subs()

    def _subs_tick(self):
        self.subs.expire()
        self._place_subs()

    def _place_subs(self):
        if not hasattr(self, "subs") or not self.cc_btn.isChecked():
            return
        if self.pip:
            owner, video = self.pip, self.pip.video
        elif self.mosaic or self.isMinimized() or not self.isVisible():
            return self.subs.place(QRect())
        else:
            owner, video = self, self.video
        if self.subs.parentWidget() is not owner:
            self.subs.set_owner(owner)
        rect = video_rect_global(video)
        margin = 0
        if self.fullscreen and self.overlay.isVisible():  # fica acima dos controles da tela cheia
            margin = rect.bottom() - self.overlay.geometry().top() + 12
        self.subs.place(rect, margin)

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
            release = json.loads(bytes(reply.readAll()).decode())
            tag = release.get("tag_name", "")
            self.cfg["last_update_check"] = time.time()
            self.cfg["latest_version"] = tag
            self.cfg["update_url"], self.cfg["update_size"] = setup_asset(release)
            self.cfg.save()
            if version_tuple(tag) > version_tuple(APP_VERSION):
                self._show_update_banner(tag)
        except (ValueError, AttributeError):
            pass
        finally:
            reply.deleteLater()

    def _show_update_banner(self, tag):
        self._banner_tag = tag
        self.update_banner.setText("" if self._compact else f"  Nova versão {tag} disponível")
        self.update_banner.setToolTip("Baixar e instalar agora" if self._one_click_update()
                                      else "Abrir a página de download")
        self.update_banner.show()

    def _one_click_update(self):
        return can_self_update() and bool(self.cfg["update_url"])

    def _update_clicked(self):
        if not self._one_click_update():
            return QDesktopServices.openUrl(QUrl(f"https://github.com/{REPO}/releases/latest"))
        if self.updater and self.updater.running:
            return
        tag = self.cfg["latest_version"]
        mb = self.cfg["update_size"] / 1e6
        if QMessageBox.question(
                self, "Atualizar o IPTV Player",
                f"Baixar e instalar a versão {tag} agora ({mb:.0f} MB)?\n\n"
                "Quando o download terminar, o programa fecha, instala a versão nova "
                "e abre de novo sozinho. Seus favoritos e configurações continuam.") \
                != QMessageBox.StandardButton.Yes:
            return
        if not self.updater:
            self.updater = UpdateDownloader(self)
            self.updater.progress.connect(self._update_progress)
            self.updater.finished.connect(self._update_downloaded)
        self.update_banner.setEnabled(False)
        self._update_progress(0)
        self.updater.start(self.cfg["update_url"], self.cfg["update_size"])

    def _update_progress(self, pct):
        self.update_banner.setText(f"{pct}%" if self._compact else f"  Baixando atualização… {pct}%")

    def _update_downloaded(self, path):
        self.update_banner.setEnabled(True)
        if not path:
            self._show_update_banner(self.cfg["latest_version"])
            self._info(f"<span style='color:{T['bad']}'>Não foi possível baixar a atualização. "
                       "Tente de novo mais tarde.</span>", 10)
            return
        try:
            run_installer(path)
        except OSError as e:
            log.exception("Atualização: o instalador não abriu")
            self._info(f"<span style='color:{T['bad']}'>O instalador não abriu: {e}</span>", 10)
            return
        self.close()  # libera os arquivos para o instalador substituir

    # ================================================================ configurações
    def open_settings(self):
        if self._kids_block():
            return
        dlg = SettingsDialog(self.cfg, self)
        dlg.theme_changed.connect(self.apply_theme)
        dlg.update_lists.connect(lambda: self.update_lists())
        dlg.reload_epg.connect(lambda: self._reload_epg(force=True))
        old_epg = (self.cfg["epg_enabled"], list(self.cfg["epg_urls"]))
        old_ai = (self.cfg["cap_model"], self.cfg["cap_source"], self.cfg["cap_translator"])
        old_voice = self.cfg["dub_voice"]
        old_merge = self.cfg["merge_dupes"]
        dlg.exec()
        if self.cfg["merge_dupes"] != old_merge:
            self._select_category(self.category, from_nav=None)
        self._apply_caption_prefs()
        if self.captions and (self.cfg["cap_model"], self.cfg["cap_source"], self.cfg["cap_translator"]) != old_ai:
            self._stop_caption_worker()
            if self.cc_btn.isChecked() or self.dub_btn.isChecked():
                self._start_caption_worker()
        self.audio.duck_level = self.cfg["dub_duck"]
        if self.dubber and self.cfg["dub_voice"] != old_voice:
            self.dubber.stop()
            self._new_dubber()
        self.apply_theme(self.cfg["theme"], self.cfg["accent"])  # desfaz a prévia se não salvou
        if (self.cfg["epg_enabled"], self.cfg["epg_urls"]) != old_epg:
            self._reload_epg()

    # ================================================================ encerramento
    def closeEvent(self, e):
        self.scanner.stop()
        self.auto_scanner.stop()
        self.scan_progress.save()
        if self.updater:
            self.updater.cancel()
        self.subs_timer.stop()
        self._stop_caption_worker(wait=True)
        self.subs.close()
        if self.recorder.active:
            self.recorder.stop()
        if self.mosaic:
            self.mosaic.close()
        if self.pip:
            self.pip.closing = True
            self.pip.close()
        self.overlay.close()
        self.player.stop()
        self.timeshift.shutdown()
        self.remote.stop()
        if self.dubber:
            self.dubber.stop()
        self.audio.detach()
        if self.fullscreen:
            self.showNormal()
        self.explored.save()
        self.newch.save()
        self.cfg["geometry"] = bytes(self.saveGeometry().toHex()).decode()
        self.cfg.save()
        super().closeEvent(e)
