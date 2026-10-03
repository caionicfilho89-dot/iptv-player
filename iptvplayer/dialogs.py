"""Diálogos: configurações, adicionar lista e guia de programação."""
import time

from PyQt6.QtCore import Qt, QUrl, pyqtSignal
from PyQt6.QtGui import QColor, QDesktopServices
from PyQt6.QtWidgets import (
    QButtonGroup, QCheckBox, QComboBox, QDialog, QFileDialog, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QPlainTextEdit, QPushButton, QVBoxLayout,
)

from . import APP_VERSION
from .captions import (
    CUDA_DOWNLOAD_MB, GPU_MODEL, MODELS, NLLB_MB, SOURCE_LANGS, TRANSLATORS, cuda_ready, gpu_available,
    nllb_ready,
)
from .config import DEFAULT_EPG_URLS
from .dub import DEFAULT_VOICE, voices
from .log import LOG_FILE
from .theme import ACCENTS, T
from .widgets import make_btn


def _open_log():
    target = LOG_FILE if LOG_FILE.exists() else LOG_FILE.parent
    QDesktopServices.openUrl(QUrl.fromLocalFile(str(target)))


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
        self.auto_update = QCheckBox("Atualizar as listas de canais automaticamente (todo dia) e marcar os canais novos")
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

        lay.addWidget(QLabel("Legendas traduzidas por IA", objectName="h2"))
        lay.addWidget(QLabel("A IA ouve o som do canal, reconhece a fala no seu computador e traduz para o "
                             "português (Ctrl+T liga e desliga).", objectName="muted", wordWrap=True))
        self.cap_model = QComboBox()
        has_gpu = gpu_available()
        for key, (label, mb) in MODELS.items():
            if key == GPU_MODEL:
                if not has_gpu:
                    continue  # só aparece em PCs com placa NVIDIA
                if not cuda_ready():
                    mb += CUDA_DOWNLOAD_MB  # acelerador da placa, baixado junto na primeira vez
            self.cap_model.addItem(f"{label} · {mb} MB", key)
        self.cap_model.setCurrentIndex(max(0, self.cap_model.findData(cfg["cap_model"])))
        self.cap_source = QComboBox()
        for key, label in SOURCE_LANGS.items():
            self.cap_source.addItem(label, key)
        self.cap_source.setCurrentIndex(max(0, self.cap_source.findData(cfg["cap_source"])))
        self.cap_scale = QComboBox()
        for label, v in (("Pequena", 0.8), ("Média", 1.0), ("Grande", 1.25), ("Muito grande", 1.5)):
            self.cap_scale.addItem(label, v)
        self.cap_scale.setCurrentIndex(max(0, self.cap_scale.findData(cfg["cap_scale"])))
        lay.addLayout(_row(QLabel("Qualidade da IA"), self.cap_model, QLabel("Idioma do canal"), self.cap_source))
        self.cap_translator = QComboBox()
        for key, label in TRANSLATORS.items():
            if key == "local" and not nllb_ready():
                label += f" · baixa {NLLB_MB} MB"
            self.cap_translator.addItem(label, key)
        self.cap_translator.setCurrentIndex(max(0, self.cap_translator.findData(cfg["cap_translator"])))
        self.cap_translator.setToolTip("Com o Google, se a internet cair, o programa usa o tradutor do PC "
                                       "(se ele já tiver sido baixado)")
        lay.addLayout(_row(QLabel("Tradutor"), self.cap_translator))
        self.cap_original = QCheckBox("Mostrar também a frase original")
        self.cap_original.setChecked(cfg["cap_original"])
        lay.addLayout(_row(QLabel("Tamanho da legenda"), self.cap_scale, self.cap_original))
        self.dub_voice = QComboBox()
        for key, label in voices():
            self.dub_voice.addItem(label, key)
        cur = cfg["dub_voice"] or DEFAULT_VOICE
        self.dub_voice.setCurrentIndex(max(0, self.dub_voice.findData(cur if ":" in cur else f"sapi:{cur}")))
        self.dub_duck = QComboBox()
        for label, v in (("Bem baixo", 0.12), ("Baixo", 0.25), ("Médio", 0.45)):
            self.dub_duck.addItem(label, v)
        self.dub_duck.setCurrentIndex(max(0, self.dub_duck.findData(cfg["dub_duck"])))
        lay.addLayout(_row(QLabel("Voz da dublagem (Ctrl+U)"), self.dub_voice,
                           QLabel("Som original enquanto ela fala"), self.dub_duck))

        lay.addWidget(QLabel("Pausar e voltar a TV ao vivo", objectName="h2"))
        self.ts_on = QCheckBox("Guardar o canal enquanto assisto, para pausar, voltar e avançar")
        self.ts_on.setChecked(cfg["timeshift"])
        self.ts_minutes = QComboBox()
        for label, v in (("15 minutos", 15), ("30 minutos", 30), ("1 hora", 60), ("2 horas", 120)):
            self.ts_minutes.addItem(label, v)
        self.ts_minutes.setCurrentIndex(max(0, self.ts_minutes.findData(cfg["timeshift_minutes"])))
        self.ts_minutes.setToolTip("Quanto mais tempo, mais espaço em disco (cerca de 1 GB a cada 30 min em HD). "
                                   "Tudo é apagado ao trocar de canal ou fechar o programa.")
        lay.addLayout(_row(self.ts_on, QLabel("Guardar os últimos"), self.ts_minutes))

        lay.addWidget(QLabel("Geral", objectName="h2"))
        self.auto_scan = QCheckBox("Testar todos os canais em segundo plano a cada 6 horas (esconde os fora do ar "
                                   "quando \"Ocultar offline\" está marcado)")
        self.auto_scan.setChecked(cfg["auto_scan"])
        lay.addWidget(self.auto_scan)
        self.merge_dupes = QCheckBox("Juntar canais repetidos (fica o melhor link; os outros viram reserva)")
        self.merge_dupes.setChecked(cfg["merge_dupes"])
        lay.addWidget(self.merge_dupes)
        self.check_upd = QCheckBox("Avisar quando houver uma nova versão do IPTV Player")
        self.check_upd.setChecked(cfg["check_updates"])
        lay.addWidget(self.check_upd)
        lay.addLayout(_row(make_btn("folder", "Arquivo com o que o programa fez e os erros que aconteceram — "
                                    "útil para descobrir por que algo não funcionou", _open_log, kind="",
                                    text="  Abrir registro de erros", icon_size=16)))
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
        self.cfg["cap_model"] = self.cap_model.currentData()
        self.cfg["cap_source"] = self.cap_source.currentData()
        self.cfg["cap_scale"] = self.cap_scale.currentData()
        self.cfg["cap_original"] = self.cap_original.isChecked()
        self.cfg["cap_translator"] = self.cap_translator.currentData()
        self.cfg["dub_voice"] = self.dub_voice.currentData()
        self.cfg["timeshift"] = self.ts_on.isChecked()
        self.cfg["auto_scan"] = self.auto_scan.isChecked()
        self.cfg["merge_dupes"] = self.merge_dupes.isChecked()
        self.cfg["timeshift_minutes"] = self.ts_minutes.currentData()
        self.cfg["dub_duck"] = self.dub_duck.currentData()
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
    """Programação do canal; um programa selecionado pode ser lembrado ou gravado."""

    def __init__(self, ch, schedule, parent=None, scheduler=None):
        super().__init__(parent)
        self.ch, self.scheduler = ch, scheduler
        self.setWindowTitle(f"Guia — {ch.name}")
        self.setMinimumSize(560, 600)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 14, 18, 16)
        lay.addWidget(QLabel(ch.name, objectName="h2"))
        self.lst = lst = QListWidget()
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
            if live:
                cur_row = lst.count()
            it = QListWidgetItem()
            it.setData(Qt.ItemDataRole.UserRole, (start, stop, title, desc))
            if live:
                f = it.font()
                f.setBold(True)
                it.setFont(f)
            lst.addItem(it)
            self._paint(it)
        if lst.count() == 0:
            lst.addItem("Sem programação disponível para este canal.")
        lay.addWidget(lst, 1)
        lst.scrollToItem(lst.item(cur_row), QListWidget.ScrollHint.PositionAtTop)
        if scheduler:
            self.remind_btn = QPushButton("🔔  Lembrar")
            self.remind_btn.setToolTip("Aviso 1 minuto antes de começar; clique no aviso para ir ao canal")
            self.rec_btn = QPushButton("●  Gravar")
            self.rec_btn.setToolTip("Grava sozinho do começo ao fim (o programa precisa estar aberto)")
            self.remind_btn.clicked.connect(lambda: self._toggle("remind"))
            self.rec_btn.clicked.connect(lambda: self._toggle("record"))
            all_btn = QPushButton("Agendamentos…")
            all_btn.clicked.connect(self._show_all)
            lay.addLayout(_row(self.remind_btn, self.rec_btn, all_btn))
            lst.currentItemChanged.connect(self._update_buttons)
            self._update_buttons()

    def _prog(self, it=None):
        it = it or self.lst.currentItem()
        return it.data(Qt.ItemDataRole.UserRole) if it else None

    def _paint(self, it):
        start, stop, title, desc = it.data(Qt.ItemDataRole.UserRole)
        txt = f"{time.strftime('%H:%M', time.localtime(start))}   {title}"
        if start <= time.time() < stop:
            txt += "   ● AGORA"
        if self.scheduler:
            kinds = self.scheduler.scheduled_kinds(self.ch, start)
            txt += "   🔔" * ("remind" in kinds) + "   ● REC agendado" * ("record" in kinds)
        it.setText(txt + (f"\n{desc}" if desc else ""))

    def _update_buttons(self, *_):
        p = self._prog()
        now = time.time()
        kinds = self.scheduler.scheduled_kinds(self.ch, p[0]) if p else set()
        self.remind_btn.setEnabled(bool(p) and p[0] > now)
        self.rec_btn.setEnabled(bool(p) and p[1] > now)
        self.remind_btn.setText("🔔  Não lembrar" if "remind" in kinds else "🔔  Lembrar")
        self.rec_btn.setText("●  Não gravar" if "record" in kinds else "●  Gravar")

    def _toggle(self, kind):
        p = self._prog()
        if p:
            self.scheduler.toggle_schedule(kind, self.ch, p[0], p[1], p[2])
            self._paint(self.lst.currentItem())
            self._update_buttons()

    def _show_all(self):
        dlg = QDialog(self)
        dlg.setWindowTitle("Agendamentos")
        dlg.setMinimumSize(520, 360)
        v = QVBoxLayout(dlg)
        lst = QListWidget()
        v.addWidget(lst, 1)

        def fill():
            lst.clear()
            for item in self.scheduler.schedule_items():
                when = time.strftime("%d/%m %H:%M", time.localtime(item["start"]))
                kind = "● Gravar" if item["kind"] == "record" else "🔔 Lembrar"
                it = QListWidgetItem(f"{when}   {kind}   {item['title']}   —   {item['ch']['name']}")
                it.setData(Qt.ItemDataRole.UserRole, item)
                lst.addItem(it)
            if not lst.count():
                lst.addItem("Nenhum lembrete ou gravação agendada.")

        def cancel():
            it = lst.currentItem()
            item = it.data(Qt.ItemDataRole.UserRole) if it else None
            if item:
                self.scheduler.cancel_schedule(item)
                fill()
                for i in range(self.lst.count()):
                    if self.lst.item(i).data(Qt.ItemDataRole.UserRole):
                        self._paint(self.lst.item(i))
                self._update_buttons()
        fill()
        rm = QPushButton("Cancelar o selecionado")
        rm.clicked.connect(cancel)
        close = QPushButton("Fechar")
        close.clicked.connect(dlg.accept)
        v.addLayout(_row(rm, close))
        dlg.exec()
