"""Temas (claro/escuro + cor de destaque), folha de estilo e ícones vetoriais."""
from PyQt6.QtCore import QByteArray, QPointF, Qt
from PyQt6.QtGui import QColor, QIcon, QLinearGradient, QPainter, QPainterPath, QPixmap
from PyQt6.QtSvg import QSvgRenderer

ACCENTS = {
    "violet": ("Roxo", "#8b5cff", "#22d3ee"),
    "blue": ("Azul", "#3d8bff", "#00e1ff"),
    "green": ("Verde", "#00d98b", "#b4f527"),
    "orange": ("Laranja", "#ff7a1a", "#ffd23f"),
    "pink": ("Rosa", "#ff3d9a", "#a855f7"),
    "red": ("Vermelho", "#ff3b4e", "#ff9500"),
}

DARK = {
    "bg": "#0b0d14", "panel": "#10131c", "surface": "#191e2b", "surface2": "#222a3b",
    "border": "#262d3b", "border_hover": "#34405a", "text": "#e7e9f0", "muted": "#8a93a8",
    "strong": "#ffffff", "logo_bg": "#0a0c11", "scroll": "#2c3446", "check_border": "#3a4459",
    "ok": "#3ddc97", "warn": "#ffb84d", "bad": "#ff5d6c", "star": "#ffd166",
}
LIGHT = {
    "bg": "#f3f4f8", "panel": "#ffffff", "surface": "#f0f2f7", "surface2": "#e3e7f0",
    "border": "#e0e4ec", "border_hover": "#c5ccd9", "text": "#171a23", "muted": "#687083",
    "strong": "#0b0d12", "logo_bg": "#eceef4", "scroll": "#c9cfdb", "check_border": "#b3bccb",
    "ok": "#16a34a", "warn": "#d97706", "bad": "#dc2626", "star": "#f59e0b",
}
VIDEO_TEXT = "#4b5468"  # texto sobre a área de vídeo (sempre preta)

T = {}  # paleta ativa, lida por todos os widgets na hora de pintar


def apply_theme(mode="dark", accent="violet"):
    T.clear()
    T.update(LIGHT if mode == "light" else DARK)
    _, a1, a2 = ACCENTS.get(accent, ACCENTS["violet"])
    T.update(mode=mode, accent=a1, accent2=a2)
    _icon_cache.clear()


def gradient_css(x2=1, y2=0):
    return (f"qlineargradient(x1:0,y1:0,x2:{x2},y2:{y2}, "
            f"stop:0 {T['accent']}, stop:1 {T['accent2']})")


def rgba(hex_color, alpha):
    """Cor do tema com transparência (0-255), no formato da folha de estilo."""
    c = QColor(hex_color)
    return f"rgba({c.red()}, {c.green()}, {c.blue()}, {alpha})"


def display_font():
    """Fonte dos títulos grandes: a "Display" do Windows 11 quando existe."""
    from PyQt6.QtGui import QFontDatabase
    return "Segoe UI Variable Display" if "Segoe UI Variable Display" in QFontDatabase.families() else "Segoe UI"


def build_style():
    t = T
    disp = display_font()
    return f"""
* {{ font-family: 'Segoe UI'; font-size: 10pt; color: {t['text']}; }}
QMainWindow, QDialog, QWidget#root {{ background: {t['bg']}; }}
QWidget#sidebar {{ background: {t['panel']}; border-right: 1px solid {t['border']}; }}
QWidget#channels {{ background: {t['panel']}; }}
QWidget#controls {{ background: {t['panel']}; border-top: 1px solid {t['border']}; }}
QLabel#brand {{ font-size: 15pt; font-weight: 700; padding: 14px 16px 2px 16px; }}
QLabel#muted, QLabel#count {{ color: {t['muted']}; }}
QLabel#section {{ color: {t['muted']}; font-size: 8pt; font-weight: 700; padding: 10px 18px 2px 18px; }}
QLabel#nowTitle {{ font-size: 13pt; font-weight: 600; }}
QLabel#epg {{ color: {t['muted']}; font-size: 9pt; }}
QLabel#h2 {{ font-size: 12pt; font-weight: 600; padding-top: 6px; }}
QListWidget, QListView {{ background: transparent; border: none; outline: 0; }}
QListWidget#nav::item {{ padding: 8px 12px; margin: 1px 8px; border-radius: 8px; }}
QListWidget#nav::item:hover {{ background: {t['surface']}; }}
QListWidget#nav::item:selected {{ background: {t['surface2']}; color: {t['strong']};
    border-left: 3px solid {t['accent']}; }}
QLineEdit, QComboBox, QSpinBox, QPlainTextEdit {{
    background: {t['surface']}; border: 1px solid {t['border']}; border-radius: 8px; padding: 7px 10px;
}}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QPlainTextEdit:focus {{ border: 1px solid {t['accent']}; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox QAbstractItemView {{ background: {t['surface']}; border: 1px solid {t['border']};
    selection-background-color: {t['accent']}; selection-color: white; }}
QPushButton {{
    background: {t['surface']}; border: 1px solid {t['border']}; border-radius: 10px;
    padding: 7px 12px; min-height: 22px;
}}
QPushButton:hover {{ background: {t['surface2']}; border-color: {t['border_hover']}; }}
QPushButton:pressed {{ background: {t['border']}; }}
QPushButton:checked {{ background: {gradient_css()}; border: none; color: white; }}
QPushButton#round {{ border-radius: 19px; min-width: 38px; max-width: 38px; min-height: 38px; max-height: 38px;
    padding: 0; }}
QPushButton#tool {{ border-radius: 9px; min-width: 34px; max-width: 34px; min-height: 34px; max-height: 34px;
    padding: 0; }}
QPushButton#flat {{ background: transparent; border: none; border-radius: 8px; padding: 7px 10px;
    text-align: left; color: {t['muted']}; }}
QPushButton#flat:hover {{ background: {t['surface']}; color: {t['text']}; }}
QPushButton#play {{ border-radius: 23px; min-width: 46px; max-width: 46px; min-height: 46px; max-height: 46px;
    padding: 0; border: none; background: {gradient_css(1, 1)}; }}
QPushButton#primary {{ background: {gradient_css()}; border: none; color: white; font-weight: 600; }}
QPushButton#banner {{ background: {gradient_css()}; border: none; color: white; font-weight: 600;
    border-radius: 8px; margin: 0 8px; text-align: left; padding: 8px 10px; }}
QPushButton#swatch {{ border-radius: 14px; min-width: 28px; max-width: 28px; min-height: 28px; max-height: 28px;
    padding: 0; border: 2px solid transparent; }}
QPushButton#swatch:checked {{ border: 2px solid {t['strong']}; }}
QCheckBox {{ spacing: 6px; }}
QCheckBox::indicator {{ width: 16px; height: 16px; border-radius: 4px; border: 1px solid {t['check_border']};
    background: {t['surface']}; }}
QCheckBox::indicator:checked {{ background: {t['accent']}; border-color: {t['accent']}; }}
QSlider::groove:horizontal {{ height: 4px; background: {t['surface2']}; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {t['accent']}; border-radius: 2px; }}
QSlider::handle:horizontal {{ background: {t['strong'] if t['mode'] == 'dark' else t['accent']};
    width: 12px; margin: -5px 0; border-radius: 6px; }}
QProgressBar {{ background: {t['bg']}; border: none; max-height: 3px; }}
QProgressBar::chunk {{ background: {gradient_css()}; }}
QProgressBar#epgbar {{ background: {t['surface2']}; max-height: 3px; border-radius: 1px; }}
QScrollBar:vertical {{ background: transparent; width: 8px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {t['scroll']}; border-radius: 4px; min-height: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar:horizontal {{ height: 0; }}
QSplitter::handle {{ background: {t['border']}; width: 1px; }}
QMenu {{ background: {t['surface']}; border: 1px solid {t['border']}; padding: 4px; }}
QMenu::item {{ padding: 6px 22px 6px 12px; border-radius: 6px; }}
QMenu::item:selected {{ background: {t['accent']}; color: white; }}
QMenu::separator {{ height: 1px; background: {t['border']}; margin: 4px 6px; }}
QToolTip {{ background: {t['surface2']}; color: {t['text']}; border: 1px solid {t['border']}; padding: 4px; }}
QWidget#home, QScrollArea#homeScroll {{ background: {t['bg']}; border: none; }}
QWidget#homeBody {{ border: none; background: qlineargradient(x1:0,y1:0,x2:0.35,y2:1, stop:0 {rgba(t['accent'], 26)},
    stop:0.12 {t['bg']}, stop:1 {t['bg']}); }}
QWidget#homeBar {{ background: {t['bg']}; border-bottom: 1px solid {t['border']}; }}
QWidget#hero {{ background: qlineargradient(x1:0,y1:0,x2:1,y2:1, stop:0 {t['surface2']}, stop:1 {t['bg']});
    border: 1px solid {t['border']}; border-radius: 18px; }}
QLabel#heroKicker {{ color: {t['accent2'] if t['mode'] == 'dark' else t['accent']}; font-size: 9pt; font-weight: 800; letter-spacing: 2px; }}
QLabel#heroTitle {{ font-family: '{disp}'; font-size: 28pt; font-weight: 800; color: {t['strong']}; }}
QLabel#heroSub {{ color: {t['muted']}; font-size: 11pt; }}
QLabel#rowTitle {{ font-family: '{disp}'; font-size: 14.5pt; font-weight: 700; color: {t['strong']}; }}
QPushButton#link {{ background: transparent; border: none; color: {t['muted']}; padding: 4px 8px; }}
QPushButton#link:hover {{ color: {t['accent']}; }}
QPushButton#big {{ background: {gradient_css()}; border: none; color: white; font-weight: 700;
    font-size: 11pt; border-radius: 12px; padding: 11px 26px; }}
QPushButton#big:hover {{ background: {t['accent']}; }}
QPushButton#ghost {{ background: {rgba(t['text'], 28)}; border: 1px solid {rgba(t['text'], 40)}; font-size: 11pt;
    border-radius: 12px; padding: 11px 20px; color: {t['strong']}; }}
QPushButton#ghost:hover {{ background: {rgba(t['text'], 50)}; }}
QWidget#browseSide {{ background: {t['panel']}; border-right: 1px solid {t['border']}; }}
QLabel#brandBig {{ font-family: '{disp}'; font-size: 17pt; font-weight: 800; color: {t['strong']}; padding-left: 4px; }}
QPushButton#navBtn {{ background: transparent; border: none; border-radius: 12px; padding: 10px 14px;
    text-align: left; color: {t['muted']}; font-size: 10.5pt; min-height: 22px; }}
QPushButton#navBtn:hover {{ background: {t['surface']}; color: {t['text']}; }}
QPushButton#navBtn:checked {{ background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 {rgba(t['accent'], 230)},
    stop:1 {rgba(t['accent2'], 150)}); color: white; font-weight: 700; }}
QPushButton#nowBtn {{ background: {gradient_css()}; border: none; border-radius: 12px; padding: 10px 14px;
    text-align: left; color: white; font-weight: 600; min-height: 22px; margin-bottom: 6px; }}
QWidget#browseBar {{ background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 {rgba(t['accent'], 34)},
    stop:0.6 {t['bg']}, stop:1 {rgba(t['accent2'], 22)}); }}
QLineEdit#bigSearch {{ background: {t['surface']}; border: 1px solid {t['border']}; border-radius: 20px;
    padding: 9px 14px 9px 6px; font-size: 10.5pt; }}
QLineEdit#bigSearch:focus {{ border: 1px solid {t['accent']}; background: {t['panel']}; }}
QWidget#chips {{ background: transparent; }}
QPushButton#chip {{ background: {t['surface']}; border: 1px solid {t['border']}; border-radius: 15px;
    padding: 5px 15px; min-height: 18px; color: {t['text']}; }}
QPushButton#chip:hover {{ border-color: {t['accent']}; background: {t['surface2']}; }}
QPushButton#chip:checked {{ background: {gradient_css()}; border-color: {t['accent']}; color: white; font-weight: 700; }}
QPushButton#dot {{ background: {rgba(t['text'], 70)}; border: none; border-radius: 4px; min-width: 8px; max-width: 8px;
    min-height: 8px; max-height: 8px; padding: 0; }}
QPushButton#dot:checked {{ background: {gradient_css()}; min-width: 26px; max-width: 26px; }}
QLabel#h1 {{ font-family: '{disp}'; font-size: 22pt; font-weight: 700; color: {t['strong']}; }}
QLabel#heroMeta {{ color: {t['text']}; font-size: 10.5pt; }}
QLabel#heroPlot {{ color: {rgba(t['text'], 215)}; font-size: 10pt; }}
QLabel#detailPlot {{ color: {t['text']}; font-size: 11pt; padding-top: 6px; }}
QLabel#castList {{ font-size: 9.8pt; padding: 2px 2px 0 2px; }}
QTabWidget::pane {{ border: none; }}
QTabBar::tab {{ background: transparent; color: {t['muted']}; border: none; border-bottom: 2px solid {t['border']};
    padding: 7px 14px; margin-right: 2px; }}
QTabBar::tab:hover {{ color: {t['text']}; }}
QTabBar::tab:selected {{ color: {t['strong']}; border-bottom: 2px solid {t['accent']}; font-weight: 600; }}
"""


# ----------------------------------------------------------------------------- ícones
# SVG em grade 24x24; CUR é trocado pela cor
_F = 'fill="CUR" stroke="none"'
ICONS = {
    "play": f'<path d="M8 5.5v13a1 1 0 0 0 1.5.86l10.2-6.5a1 1 0 0 0 0-1.72L9.5 4.64A1 1 0 0 0 8 5.5z" {_F}/>',
    "pause": f'<rect x="6" y="5" width="4" height="14" rx="1.2" {_F}/><rect x="14" y="5" width="4" height="14" rx="1.2" {_F}/>',
    "stop": f'<rect x="6" y="6" width="12" height="12" rx="2" {_F}/>',
    "prev": f'<path d="M18 6.2v11.6a.8.8 0 0 1-1.25.66L9 13a1.2 1.2 0 0 1 0-2l7.75-5.46A.8.8 0 0 1 18 6.2z" {_F}/>'
            f'<rect x="5.5" y="5.5" width="2.2" height="13" rx="1.1" {_F}/>',
    "next": f'<path d="M6 6.2v11.6a.8.8 0 0 0 1.25.66L15 13a1.2 1.2 0 0 0 0-2L7.25 5.54A.8.8 0 0 0 6 6.2z" {_F}/>'
            f'<rect x="16.3" y="5.5" width="2.2" height="13" rx="1.1" {_F}/>',
    "volume": '<path d="M4 9.5h3.5L12 5.5v13l-4.5-4H4z" fill="CUR" stroke-width="1.5"/>'
              '<path d="M15.5 9a4 4 0 0 1 0 6"/><path d="M18.5 6.5a7.5 7.5 0 0 1 0 11"/>',
    "mute": '<path d="M4 9.5h3.5L12 5.5v13l-4.5-4H4z" fill="CUR" stroke-width="1.5"/><path d="M16 9.5l5 5M21 9.5l-5 5"/>',
    "fullscreen": '<path d="M4 9V5a1 1 0 0 1 1-1h4M15 4h4a1 1 0 0 1 1 1v4M20 15v4a1 1 0 0 1-1 1h-4M9 20H5a1 1 0 0 1-1-1v-4"/>',
    "fs_exit": '<path d="M9 4v4a1 1 0 0 1-1 1H4M20 9h-4a1 1 0 0 1-1-1V4M15 20v-4a1 1 0 0 1 1-1h4M4 15h4a1 1 0 0 1 1 1v4"/>',
    "star": '<path d="M12 3.5l2.6 5.3 5.9.9-4.25 4.1 1 5.8L12 16.9l-5.25 2.75 1-5.8L3.5 9.7l5.9-.9z"/>',
    "star_fill": '<path d="M12 3.5l2.6 5.3 5.9.9-4.25 4.1 1 5.8L12 16.9l-5.25 2.75 1-5.8L3.5 9.7l5.9-.9z" fill="CUR"/>',
    "shuffle": '<path d="M3 7h3.5c4.5 0 6.5 10 11 10H21"/><path d="M3 17h3.5c1.4 0 2.6-1 3.6-2.5"/>'
               '<path d="M13.9 9.5C15 8 16.2 7 17.5 7H21"/><path d="M18 4l3 3-3 3"/><path d="M18 14l3 3-3 3"/>',
    "grid": '<rect x="4" y="4" width="7" height="7" rx="1.5"/><rect x="13" y="4" width="7" height="7" rx="1.5"/>'
            '<rect x="4" y="13" width="7" height="7" rx="1.5"/><rect x="13" y="13" width="7" height="7" rx="1.5"/>',
    "list": '<path d="M9 6h11M9 12h11M9 18h11"/><circle cx="4.5" cy="6" r="1.2" fill="CUR" stroke="none"/>'
            '<circle cx="4.5" cy="12" r="1.2" fill="CUR" stroke="none"/><circle cx="4.5" cy="18" r="1.2" fill="CUR" stroke="none"/>',
    "camera": '<path d="M4 8.5A1.5 1.5 0 0 1 5.5 7h2l1.5-2h6l1.5 2h2A1.5 1.5 0 0 1 20 8.5v9A1.5 1.5 0 0 1 18.5 19h-13'
              'A1.5 1.5 0 0 1 4 17.5z"/><circle cx="12" cy="12.5" r="3.5"/>',
    "record": f'<circle cx="12" cy="12" r="8"/><circle cx="12" cy="12" r="4" {_F}/>',
    "pip": f'<rect x="3" y="5" width="18" height="14" rx="2"/><rect x="12" y="11" width="7" height="6" rx="1" {_F}/>',
    "mosaic": '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M12 4v16M3 12h18"/>',
    "timer": '<circle cx="12" cy="13.5" r="7.5"/><path d="M12 10v3.5l2.5 2M9.5 2.5h5M12 2.5V6"/>',
    "refresh": '<path d="M20 12a8 8 0 1 1-2.34-5.66"/><path d="M20 4v5h-5"/>',
    "plus": '<path d="M12 5v14M5 12h14"/>',
    "chev_left": '<path d="M15 5l-7 7 7 7"/>',
    "chev_right": '<path d="M9 5l7 7-7 7"/>',
    "home": '<path d="M3.5 11L12 4l8.5 7"/><path d="M6 9.5V20h12V9.5"/><path d="M10 20v-5h4v5"/>',
    "settings": '<path d="M4 7h9M17 7h3M4 12h3M11 12h9M4 17h11M19 17h1"/>'
                '<circle cx="15" cy="7" r="2"/><circle cx="9" cy="12" r="2"/><circle cx="17" cy="17" r="2"/>',
    "search": '<circle cx="11" cy="11" r="6.5"/><path d="M16 16l4 4"/>',
    "tv": '<rect x="3" y="6" width="18" height="13" rx="2"/><path d="M8 2.5l4 3.5 4-3.5"/>',
    "guide": '<rect x="4" y="5" width="16" height="15" rx="2"/><path d="M4 9.5h16M8.5 3v4M15.5 3v4M8 13.5h8M8 16.5h5"/>',
    "download": '<path d="M12 4v11M7.5 10.5L12 15l4.5-4.5M5 19.5h14"/>',
    "close": '<path d="M6 6l12 12M18 6L6 18"/>',
    "maximize": '<rect x="4" y="4" width="16" height="16" rx="2"/><path d="M10 14l7-7M12 7h5v5"/>',
    "clock": '<circle cx="12" cy="12" r="8.5"/><path d="M12 7.5V12l3 2"/>',
    "globe": '<circle cx="12" cy="12" r="8.5"/><path d="M3.5 12h17M12 3.5c2.5 2.6 3.5 5.4 3.5 8.5s-1 5.9-3.5 8.5'
             'c-2.5-2.6-3.5-5.4-3.5-8.5s1-5.9 3.5-8.5z"/>',
    "film": '<rect x="3.5" y="4" width="17" height="16" rx="2"/>'
            '<path d="M8 4v16M16 4v16M3.5 9h4.5M3.5 15h4.5M16 9h4.5M16 15h4.5"/>',
    "layers": '<path d="M12 3.5l8.5 4.5L12 12.5 3.5 8z"/><path d="M3.5 12L12 16.5 20.5 12"/><path d="M3.5 16L12 20.5 20.5 16"/>',
    "trophy": '<path d="M8 4h8v5a4 4 0 0 1-8 0z"/><path d="M8 6H5a3 3 0 0 0 3 4M16 6h3a3 3 0 0 1-3 4M12 13v4M8.5 20h7M9.5 17h5"/>',
    "news": '<rect x="3.5" y="4.5" width="17" height="15" rx="2"/><path d="M7 8.5h10M7 12h4M7 15.5h4"/>'
            '<rect x="13" y="11.5" width="4" height="4.5" rx=".5"/>',
    "music": '<path d="M9 17.5V5.5l11-2v12"/><circle cx="6.5" cy="17.5" r="2.5"/><circle cx="17.5" cy="15.5" r="2.5"/>',
    "smile": '<circle cx="12" cy="12" r="8.5"/><path d="M8.5 14a4.5 4.5 0 0 0 7 0"/>'
             '<circle cx="9" cy="9.5" r=".9" fill="CUR" stroke="none"/><circle cx="15" cy="9.5" r=".9" fill="CUR" stroke="none"/>',
    "heart": '<path d="M12 20s-7.5-4.4-7.5-10A4.3 4.3 0 0 1 12 7.3 4.3 4.3 0 0 1 19.5 10c0 5.6-7.5 10-7.5 10z"/>',
    "antenna": '<circle cx="12" cy="12" r="2"/><path d="M8 8a5.6 5.6 0 0 0 0 8M16 8a5.6 5.6 0 0 1 0 8'
               'M5.2 5.2a9.6 9.6 0 0 0 0 13.6M18.8 5.2a9.6 9.6 0 0 1 0 13.6"/>',
    "chat": '<path d="M4.5 5.5h15a1 1 0 0 1 1 1v9a1 1 0 0 1-1 1H10l-4.5 3.5V16.5h-1a1 1 0 0 1-1-1v-9a1 1 0 0 1 1-1z"/>',
    "sort": '<path d="M7 4.5v15M3.5 16l3.5 3.5 3.5-3.5"/><path d="M13.5 6.5h7M13.5 12h5M13.5 17.5h3"/>',
    "tracks": '<path d="M4 14.5V12a8 8 0 0 1 16 0v2.5"/><rect x="3.5" y="13.5" width="4.5" height="6.5" rx="1.5"/>'
              '<rect x="16" y="13.5" width="4.5" height="6.5" rx="1.5"/>',
    "flag": '<path d="M5 21V4M5 4.5h12l-2.5 4 2.5 4H5"/>',
    "folder": '<path d="M3.5 7a1.5 1.5 0 0 1 1.5-1.5h4l2 2h8a1.5 1.5 0 0 1 1.5 1.5v8.5A1.5 1.5 0 0 1 19 19H5'
              'a1.5 1.5 0 0 1-1.5-1.5z"/>',
    "link": '<path d="M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1"/>'
            '<path d="M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1"/>',
    "trash": '<path d="M4.5 7h15M9.5 7V4.5h5V7M6.5 7l1 12.5h9l1-12.5"/>',
    "check": '<path d="M5 12.5l4.5 4.5L19 7.5"/>',
    "compass": '<circle cx="12" cy="12" r="8.5"/><path d="M15.5 8.5l-2 5-5 2 2-5z" fill="CUR"/>',
    "sparkle": '<path d="M12 3.5l1.9 5.1 5.1 1.9-5.1 1.9L12 17.5l-1.9-5.1L5 10.5l5.1-1.9z"/>'
               '<path d="M18.5 15.5l.8 2 2 .8-2 .8-.8 2-.8-2-2-.8 2-.8z"/>',
    "back": '<path d="M4 12a8 8 0 1 0 2.34-5.66"/><path d="M4 4v5h5"/><path d="M10 10.5h2.5v3H10M14.5 10.5v3"/>',
    "forward": '<path d="M20 12a8 8 0 1 1-2.34-5.66"/><path d="M20 4v5h-5"/><path d="M9.5 10.5H12v3H9.5M14 10.5v3"/>',
    "live": '<circle cx="12" cy="12" r="2.6" fill="CUR"/><path d="M7.8 7.8a6 6 0 0 0 0 8.4M16.2 7.8a6 6 0 0 1 0 8.4M5 5a10 10 0 0 0 0 14M19 5a10 10 0 0 1 0 14"/>',
    "phone": '<rect x="7" y="2.5" width="10" height="19" rx="2.5"/><path d="M11 18.5h2"/>',
    "dub": '<rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5.5 11a6.5 6.5 0 0 0 13 0M12 17.5V21M8.5 21h7"/>',
    "cc": '<rect x="3" y="5" width="18" height="14" rx="2.5"/><path d="M10.6 10.3a2.4 2.4 0 1 0 0 3.4"/>'
          '<path d="M17.1 10.3a2.4 2.4 0 1 0 0 3.4"/>',
    "moon": '<path d="M20 14.5A8.5 8.5 0 0 1 9.5 4a8.5 8.5 0 1 0 10.5 10.5z"/>',
}

_icon_cache = {}


def icon_pixmap(name, color, size=20, dpr=2.0):
    key = (name, color, size, dpr)
    if key in _icon_cache:
        return _icon_cache[key]
    body = ICONS[name].replace("CUR", color)
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" '
           f'stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round">{body}</svg>')
    renderer = QSvgRenderer(QByteArray(svg.encode()))
    pm = QPixmap(int(size * dpr), int(size * dpr))
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    renderer.render(p)
    p.end()
    pm.setDevicePixelRatio(dpr)
    _icon_cache[key] = pm
    return pm


def color_of(key):
    if key == "white":
        return "#ffffff"
    return T.get(key, key)


def icon(name, color="text", size=20):
    return QIcon(icon_pixmap(name, color_of(color), size))


def app_icon_pixmap(size=256):
    """Ícone do PudimFlix: um pudim com calda de caramelo num pratinho (desenhado numa grade de 64x64)."""
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.scale(size / 64, size / 64)
    p.setPen(Qt.PenStyle.NoPen)
    # prato
    p.setBrush(QColor(0, 0, 0, 60))
    p.drawEllipse(QPointF(32, 55.5), 29, 6)
    plate = QLinearGradient(0, 48, 0, 60)
    plate.setColorAt(0, QColor("#ffffff"))
    plate.setColorAt(1, QColor("#c9d0de"))
    p.setBrush(plate)
    p.drawEllipse(QPointF(32, 53.5), 29, 6)
    # corpo do pudim (tronco de cone)
    body = QPainterPath()
    body.moveTo(17, 20)
    body.lineTo(47, 20)
    body.cubicTo(50, 34, 53, 44, 55, 51)
    body.cubicTo(45, 56, 19, 56, 9, 51)
    body.cubicTo(11, 44, 14, 34, 17, 20)
    g = QLinearGradient(9, 0, 55, 0)
    g.setColorAt(0, QColor("#f0a93a"))
    g.setColorAt(0.45, QColor("#ffd772"))
    g.setColorAt(1, QColor("#e8972c"))
    p.fillPath(body, g)
    # calda de caramelo escorrendo
    cal = QPainterPath()
    cal.moveTo(16, 21)
    cal.cubicTo(16, 13, 48, 13, 48, 21)
    cal.lineTo(49.5, 29)
    cal.cubicTo(49.5, 33, 45.5, 33, 45.5, 29)
    cal.lineTo(44.5, 26)
    cal.cubicTo(42, 27, 40, 27, 38.5, 26.5)
    cal.lineTo(38.5, 34)
    cal.cubicTo(38.5, 38.5, 33.5, 38.5, 33.5, 34)
    cal.lineTo(33.5, 27)
    cal.cubicTo(29, 27.5, 25, 27.5, 22.5, 26.5)
    cal.lineTo(21.5, 31)
    cal.cubicTo(21, 35, 16.5, 35, 16.8, 31)
    cal.lineTo(16, 21)
    cg = QLinearGradient(0, 13, 0, 36)
    cg.setColorAt(0, QColor("#a8481a"))
    cg.setColorAt(1, QColor("#6e2a0c"))
    p.fillPath(cal, cg)
    # brilho
    p.setBrush(QColor(255, 255, 255, 150))
    p.drawEllipse(QPointF(26, 17.5), 5, 1.8)
    p.setBrush(QColor(255, 255, 255, 90))
    p.drawEllipse(QPointF(14.5, 42), 1.6, 5)
    p.end()
    return pm


def app_icon(size=256):
    return QIcon(app_icon_pixmap(size))
