"""Diálogos: configurações, adicionar lista e guia de programação."""
import time

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QButtonGroup, QCheckBox, QComboBox, QDialog, QFileDialog, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QPlainTextEdit, QPushButton, QVBoxLayout,
)

from . import APP_VERSION
from .config import DEFAULT_EPG_URLS
from .theme import ACCENTS, T
from .widgets import make_btn


def _row(*widgets, stretch_last=False):
    h = QHBoxLayout()
    h.setSpacing(8)
    for w in widgets:
        h.addWidget(w)
    if not stretch_last:
        h.addStretch(1)
    return h


class SettingsDialog(QDialog):
    theme_changed = pyqtSignal(str, str)
    update_lists = pyqtSignal()
    reload_epg = pyqtSignal()

    def __init__(self, cfg, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.setWindowTitle("Configurações")
        self.setMinimumWidth(560)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 16, 22, 18)
        lay.setSpacing(8)

        lay.addWidget(QLabel("Aparência", objectName="h2"))
        self.mode = QComboBox()
        self.mode.addItems(["Escuro", "Claro"])
        self.mode.setCurrentIndex(1 if cfg["theme"] == "light" else 0)
        self.mode.currentIndexChanged.connect(self._theme)
        lay.addLayout(_row(QLabel("Tema"), self.mode))
        self.swatches = QButtonGroup(self)
        sw_row = [QLabel("Cor de destaque")]
        for key, (label, c1, c2) in ACCENTS.items():
            b = QPushButton(objectName="swatch")
            b.setCheckable(True)
            b.setToolTip(label)
            b.setProperty("accent", key)
            b.setStyleSheet(f"QPushButton#swatch {{ background: qlineargradient(x1:0,y1:0,x2:1,y2:1,"
                            f" stop:0 {c1}, stop:1 {c2}); }}")
            b.setChecked(key == cfg["accent"])
            self.swatches.addButton(b)
            sw_row.append(b)
        self.swatches.buttonClicked.connect(lambda _: self._theme())
        lay.addLayout(_row(*sw_row))

        lay.addWidget(QLabel("Canais", objectName="h2"))
        self.auto_update = QCheckBox("Atualizar as listas de canais automaticamente (1× por semana)")
        self.auto_update.setChecked(cfg["auto_update_lists"])
        lay.addWidget(self.auto_update)
        upd = cfg["lists_updated"]
        self.upd_lbl = QLabel("Última atualização: " + (time.strftime("%d/%m/%Y %H:%M", time.localtime(upd))
                                                         if upd else "nunca"), objectName="muted")
        lay.addLayout(_row(make_btn("refresh", "", self.update_lists.emit, kind="", text="  Atualizar agora",
                                    icon_size=16), self.upd_lbl))

        lay.addWidget(QLabel("Guia de programação (EPG)", objectName="h2"))
        self.epg_on = QCheckBox("Mostrar o que está passando agora (quando o canal tiver guia)")
        self.epg_on.setChecked(cfg["epg_enabled"])
        lay.addWidget(self.epg_on)
        lay.addWidget(QLabel("Endereços de guias XMLTV, um por linha (.xml ou .xml.gz). "
                             "Listas de operadoras que trazem o próprio guia são usadas automaticamente.",
                             objectName="muted", wordWrap=True))
        self.epg_urls = QPlainTextEdit("\n".join(cfg["epg_urls"]))
        self.epg_urls.setFixedHeight(84)
        lay.addWidget(self.epg_urls)
        lay.addLayout(_row(make_btn("refresh", "", self._reload_epg, kind="", text="  Recarregar guia",
                                    icon_size=16),
                           make_btn(None, "", lambda: self.epg_urls.setPlainText("\n".join(DEFAULT_EPG_URLS)),
                                    kind="", text="Restaurar padrão")))

        lay.addWidget(QLabel("Geral", objectName="h2"))
        self.check_upd = QCheckBox("Avisar quando houver uma nova versão do IPTV Player")
        self.check_upd.setChecked(cfg["check_updates"])
        lay.addWidget(self.check_upd)
        lay.addSpacing(8)
        ok = QPushButton("Salvar", objectName="primary")
        ok.clicked.connect(self.accept)
        cancel = QPushButton("Cancelar")
        cancel.clicked.connect(self.reject)
        bottom = QHBoxLayout()
        bottom.addWidget(QLabel(f"IPTV Player v{APP_VERSION}", objectName="muted"))
        bottom.addStretch(1)
        bottom.addWidget(cancel)
        bottom.addWidget(ok)
        lay.addLayout(bottom)

    def _accent(self):
        b = self.swatches.checkedButton()
        return b.property("accent") if b else self.cfg["accent"]

    def _theme(self):
        self.theme_changed.emit("light" if self.mode.currentIndex() == 1 else "dark", self._accent())

    def _save_epg_fields(self):
        self.cfg["epg_enabled"] = self.epg_on.isChecked()
        self.cfg["epg_urls"] = [u.strip() for u in self.epg_urls.toPlainText().splitlines() if u.strip()]

    def _reload_epg(self):
        self._save_epg_fields()
        self.reload_epg.emit()

    def accept(self):
        self.cfg["theme"] = "light" if self.mode.currentIndex() == 1 else "dark"
        self.cfg["accent"] = self._accent()
        self.cfg["auto_update_lists"] = self.auto_update.isChecked()
        self.cfg["check_updates"] = self.check_upd.isChecked()
        self._save_epg_fields()
        self.cfg.save()
        super().accept()


class AddListDialog(QDialog):
    def __init__(self, parent=None, name="", url=""):
        super().__init__(parent)
        self.setWindowTitle("Adicionar lista")
        self.setMinimumWidth(520)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 16, 22, 18)
        lay.setSpacing(8)
        lay.addWidget(QLabel("Adicionar lista de canais", objectName="h2"))
        lay.addWidget(QLabel("Cole o link de uma lista M3U (por exemplo, a que sua operadora de IPTV forneceu) "
                             "ou escolha um arquivo .m3u do computador.", objectName="muted", wordWrap=True))
        self.name = QLineEdit(name, placeholderText="Nome (ex.: Minha operadora)")
        lay.addWidget(self.name)
        self.url = QLineEdit(url, placeholderText="https://…/lista.m3u   ou   C:\\…\\lista.m3u")
        browse = make_btn("folder", "Escolher arquivo", self._browse, kind="tool", icon_size=18)
        h = QHBoxLayout()
        h.addWidget(self.url, 1)
        h.addWidget(browse)
        lay.addLayout(h)
        self.err = QLabel("", objectName="muted")
        lay.addWidget(self.err)
        ok = QPushButton("Adicionar", objectName="primary")
        ok.clicked.connect(self._ok)
        cancel = QPushButton("Cancelar")
        cancel.clicked.connect(self.reject)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(cancel)
        row.addWidget(ok)
        lay.addLayout(row)

    def _browse(self):
        path, _ = QFileDialog.getOpenFileName(self, "Escolher lista", "", "Listas M3U (*.m3u *.m3u8);;Todos (*.*)")
        if path:
            self.url.setText(path)
            if not self.name.text():
                import os
                self.name.setText(os.path.splitext(os.path.basename(path))[0])

    def _ok(self):
        if not self.url.text().strip():
            self.err.setText("Informe um link ou arquivo.")
            return
        self.accept()

    def values(self):
        return self.name.text().strip(), self.url.text().strip()


class GuideDialog(QDialog):
    def __init__(self, ch, schedule, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Guia — {ch.name}")
        self.setMinimumSize(520, 520)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 14, 18, 16)
        lay.addWidget(QLabel(ch.name, objectName="h2"))
        lst = QListWidget()
        lst.setWordWrap(True)
        lst.setStyleSheet("QListWidget::item { padding: 8px 6px; border-bottom: 1px solid %s; }" % T["border"])
        now = time.time()
        cur_row = 0
        last_day = None
        for start, stop, title, desc in schedule:
            if stop < now - 3600:
                continue
            day = time.strftime("%d/%m", time.localtime(start))
            if day != last_day:
                sep = QListWidgetItem(("Hoje" if day == time.strftime("%d/%m") else day))
                sep.setFlags(Qt.ItemFlag.NoItemFlags)
                sep.setForeground(QColor(T["accent"]))
                lst.addItem(sep)
                last_day = day
            live = start <= now < stop
            txt = f"{time.strftime('%H:%M', time.localtime(start))}   {title}"
            if live:
                txt += "   ● AGORA"
                cur_row = lst.count()
            it = QListWidgetItem(txt + (f"\n{desc}" if desc else ""))
            if live:
                f = it.font()
                f.setBold(True)
                it.setFont(f)
            lst.addItem(it)
        if lst.count() == 0:
            lst.addItem("Sem programação disponível para este canal.")
        lay.addWidget(lst, 1)
        lst.scrollToItem(lst.item(cur_row), QListWidget.ScrollHint.PositionAtTop)
