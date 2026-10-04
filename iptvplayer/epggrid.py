"""Grade de programação: todos os canais da categoria em linhas e a programação numa linha do tempo,
como o guia das TVs por assinatura."""
import time

from PyQt6.QtCore import QRect, QSize, Qt
from PyQt6.QtGui import QColor, QFont, QPainter, QPainterPath
from PyQt6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QLineEdit, QMenu, QPushButton, QScrollArea, QToolTip, QVBoxLayout, QWidget,
)

from . import catchup
from .theme import T, icon

ROW_H = 46
HEAD_H = 30
NAME_W = 190
PX_PER_MIN = 4.2          # 30 min = 126 px
BEFORE = 2 * 3600         # mostra a partir de 2 h atrás
SPAN_MAX = 30 * 3600


def _hm(ts):
    return time.strftime("%H:%M", time.localtime(ts))


class _Grid(QWidget):
    """Desenha só a parte visível; a coluna dos canais e a régua das horas ficam presas nas bordas."""

    def __init__(self, dlg):
        super().__init__()
        self.dlg = dlg
        self.setMouseTracking(True)
        self.rows = []  # (canal, programação)
        self.t0 = self.t1 = 0
        self._hover = None

    def set_rows(self, rows, t0, t1):
        self.rows, self.t0, self.t1 = rows, t0, t1
        self.setFixedSize(QSize(NAME_W + int((t1 - t0) / 60 * PX_PER_MIN) + 20, HEAD_H + ROW_H * len(rows) + 4))
        self.update()

    def x_of(self, ts):
        return NAME_W + int((ts - self.t0) / 60 * PX_PER_MIN)

    def _scroll(self):
        sa = self.dlg.area
        return sa.horizontalScrollBar().value(), sa.verticalScrollBar().value()

    def hit(self, pos):
        """(linha, programa ou None) no ponto; programa = None quando é a coluna do nome."""
        sx, _sy = self._scroll()
        row = (pos.y() - HEAD_H) // ROW_H
        if pos.y() < self._scroll()[1] + HEAD_H or not 0 <= row < len(self.rows):
            return None
        ch, progs = self.rows[row]
        if pos.x() < sx + NAME_W:
            return row, None
        ts = self.t0 + (pos.x() - NAME_W) / PX_PER_MIN * 60
        for p in progs:
            if p[0] <= ts < p[1]:
                return row, p
        return None

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        clip = e.rect()
        p.fillRect(clip, QColor(T["bg"]))
        sx, sy = self._scroll()
        now = time.time()
        f = QFont(self.font())
        small = QFont(f)
        small.setPointSizeF(f.pointSizeF() * 0.85)
        bold = QFont(f)
        bold.setWeight(QFont.Weight.DemiBold)
        first = max(0, (clip.top() - HEAD_H) // ROW_H)
        last = min(len(self.rows), (clip.bottom() - HEAD_H) // ROW_H + 1)
        playing = self.dlg.win.current.url if self.dlg.win.current else None
        for i in range(first, last):
            ch, progs = self.rows[i]
            y = HEAD_H + i * ROW_H
            for start, stop, title, desc in progs:
                x1, x2 = max(self.x_of(start), NAME_W), self.x_of(stop)
                if x2 < clip.left() or x1 > clip.right():
                    continue
                r = QRect(x1 + 1, y + 3, x2 - x1 - 2, ROW_H - 6)
                live = start <= now < stop
                past = stop <= now
                hover = self._hover == (i, start)
                color = T["accent"] if live else T["surface2"] if hover else T["surface"]
                path = QPainterPath()
                path.addRoundedRect(r.toRectF(), 6, 6)
                p.fillPath(path, QColor(color))
                if live:  # parte que já passou do programa atual, mais escura
                    done = QRect(r.left(), r.top(), int((now - start) / max(1, stop - start) * r.width()), r.height())
                    p.save()
                    p.setClipPath(path)
                    p.fillRect(done, QColor(0, 0, 0, 50))
                    p.restore()
                p.setPen(QColor("white" if live else T["muted"] if past else T["text"]))
                p.setFont(bold if live else f)
                inner = r.adjusted(8, 4, -6, -ROW_H // 2 + 2)
                label = title
                if past and self.dlg.can_watch(ch, start):
                    label = "↺ " + title
                kinds = self.dlg.win.scheduled_kinds(ch, start)
                label = "🔔 " * ("remind" in kinds) + "● " * ("record" in kinds) + label
                p.drawText(inner, Qt.AlignmentFlag.AlignVCenter,
                           p.fontMetrics().elidedText(label, Qt.TextElideMode.ElideRight, inner.width()))
                p.setFont(small)
                p.setPen(QColor("white" if live else T["muted"]))
                sub = r.adjusted(8, ROW_H // 2 - 4, -6, -3)
                p.drawText(sub, Qt.AlignmentFlag.AlignVCenter,
                           p.fontMetrics().elidedText(f"{_hm(start)} – {_hm(stop)}", Qt.TextElideMode.ElideRight,
                                                      sub.width()))
            # coluna do canal, presa à esquerda
            nr = QRect(sx, y, NAME_W, ROW_H)
            p.fillRect(nr, QColor(T["panel"]))
            if ch.url == playing:
                p.fillRect(QRect(sx, y + 8, 3, ROW_H - 16), QColor(T["accent"]))
            pm = self.dlg.win.logos.get(ch.logo)
            if pm:
                s = pm.size().scaled(QSize(44, 30), Qt.AspectRatioMode.KeepAspectRatio)
                p.drawPixmap(QRect(sx + 10, y + (ROW_H - s.height()) // 2, s.width(), s.height()), pm)
            p.setFont(bold if ch.url == playing else f)
            p.setPen(QColor(T["strong"] if ch.url == playing else T["text"]))
            tr = QRect(sx + 62, y, NAME_W - 68, ROW_H)
            num = self.dlg.win.numbers.get(ch.url)
            p.drawText(tr, Qt.AlignmentFlag.AlignVCenter, p.fontMetrics().elidedText(
                f"{num}  {ch.name}" if num else ch.name, Qt.TextElideMode.ElideRight, tr.width()))
            p.setPen(QColor(T["border"]))
            p.drawLine(sx, y + ROW_H - 1, sx + self.dlg.area.viewport().width(), y + ROW_H - 1)
        # linha do "agora"
        xn = self.x_of(now)
        if xn > sx + NAME_W:
            p.fillRect(QRect(xn - 1, sy + HEAD_H, 2, self.height()), QColor(T["bad"]))
        # régua das horas, presa em cima
        p.fillRect(QRect(sx, sy, self.dlg.area.viewport().width(), HEAD_H), QColor(T["panel"]))
        p.setFont(small)
        t = self.t0 - self.t0 % 1800 + 1800
        while t < self.t1:
            x = self.x_of(t)
            if x > sx + NAME_W - 20:
                p.setPen(QColor(T["muted"]))
                p.drawText(QRect(x + 4, sy, 60, HEAD_H), Qt.AlignmentFlag.AlignVCenter, _hm(t))
                p.setPen(QColor(T["border"]))
                p.drawLine(x, sy + HEAD_H - 8, x, sy + HEAD_H)
            t += 1800
        if xn > sx + NAME_W:
            r = QRect(xn - 22, sy + 6, 44, 18)
            path = QPainterPath()
            path.addRoundedRect(r.toRectF(), 9, 9)
            p.fillPath(path, QColor(T["bad"]))
            p.setPen(QColor("white"))
            p.drawText(r, Qt.AlignmentFlag.AlignCenter, _hm(now))
        p.fillRect(QRect(sx, sy, NAME_W, HEAD_H), QColor(T["panel"]))
        p.setPen(QColor(T["muted"]))
        p.drawText(QRect(sx + 12, sy, NAME_W, HEAD_H), Qt.AlignmentFlag.AlignVCenter,
                   time.strftime("%d/%m", time.localtime(now)))
        p.setPen(QColor(T["border"]))
        p.drawLine(sx, sy + HEAD_H - 1, sx + self.dlg.area.viewport().width(), sy + HEAD_H - 1)
        p.end()

    def mouseMoveEvent(self, e):
        h = self.hit(e.position().toPoint())
        key = (h[0], h[1][0]) if h and h[1] else None
        if key != self._hover:
            self._hover = key
            self.update()
            self.setCursor(Qt.CursorShape.PointingHandCursor if h else Qt.CursorShape.ArrowCursor)
            if h and h[1]:
                start, stop, title, desc = h[1]
                QToolTip.showText(e.globalPosition().toPoint(),
                                  f"<b>{title}</b><br>{_hm(start)} – {_hm(stop)}"
                                  + (f"<br><br>{desc[:300]}" if desc else ""), self)
            else:
                QToolTip.hideText()

    def leaveEvent(self, _e):
        self._hover = None
        self.update()

    def mousePressEvent(self, e):
        h = self.hit(e.position().toPoint())
        if h:
            self.dlg.clicked(h[0], h[1], e.globalPosition().toPoint())


class EpgGridDialog(QDialog):
    def __init__(self, win, channels, title):
        super().__init__(win)
        self.win = win
        self.setWindowTitle(f"Grade de programação — {title}")
        self.resize(min(1300, win.width() - 60), min(780, win.height() - 60))
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(8)
        top = QHBoxLayout()
        top.addWidget(QLabel(title, objectName="h2"))
        self.count = QLabel("", objectName="muted")
        top.addWidget(self.count)
        top.addStretch(1)
        self.filter = QLineEdit(placeholderText="Filtrar canal ou programa…")
        self.filter.setClearButtonEnabled(True)
        self.filter.setFixedWidth(260)
        self.filter.textChanged.connect(self._fill)
        top.addWidget(self.filter)
        for text, delta in (("◀ 2 h", -2 * 3600), ("Agora", 0), ("2 h ▶", 2 * 3600), ("Hoje à noite", None)):
            b = QPushButton(text)
            b.clicked.connect(lambda _c=False, d=delta: self.jump(d))
            top.addWidget(b)
        lay.addLayout(top)
        self.area = QScrollArea()
        self.area.setWidgetResizable(False)
        self.grid = _Grid(self)
        self.area.setWidget(self.grid)
        for bar in (self.area.horizontalScrollBar(), self.area.verticalScrollBar()):
            bar.valueChanged.connect(self.grid.update)
        lay.addWidget(self.area, 1)
        hint = QLabel("Clique no programa que está passando para assistir · nos próximos, para lembrar ou gravar"
                      " · ↺ = já passou e dá para assistir", objectName="muted")
        lay.addWidget(hint)
        self.all = [(c, win.epg.schedule(c)) for c in channels]
        self.all = [(c, s) for c, s in self.all if s]
        now = time.time()
        self.t0 = now - BEFORE - (now - BEFORE) % 1800
        self.t1 = min(max((s[-1][1] for _c, s in self.all), default=now + 3600), self.t0 + SPAN_MAX)
        self._fill()
        self.win.logos.loaded.connect(self._logo_loaded)

    def _logo_loaded(self, _url):
        self.grid.update()

    def done(self, r):
        self.win.logos.loaded.disconnect(self._logo_loaded)
        super().done(r)

    def can_watch(self, ch, start):
        return catchup.available(ch, start)

    def _fill(self):
        q = self.filter.text().strip().lower()
        now = time.time()
        rows = []
        for ch, progs in self.all:
            visible = [p for p in progs if p[1] > self.t0 and p[0] < self.t1]
            if not visible:
                continue
            if q and q not in ch.name.lower() and not any(q in p[2].lower() for p in visible if p[1] > now):
                continue
            rows.append((ch, visible))
        self.grid.set_rows(rows, self.t0, self.t1)
        self.count.setText(f"{len(rows)} canais com programação")
        if not rows:
            self.count.setText("Nenhum canal desta categoria tem guia de programação")

    def jump(self, delta):
        bar = self.area.horizontalScrollBar()
        if delta is None:  # hoje às 20h
            lt = time.localtime()
            target = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 20, 0, 0, 0, 0, -1))
            if target < time.time():
                target = time.time()
            bar.setValue(self.grid.x_of(target) - NAME_W - 60)
        elif delta == 0:
            bar.setValue(self.grid.x_of(time.time()) - NAME_W - 120)
        else:
            bar.setValue(bar.value() + int(delta / 60 * PX_PER_MIN))

    def showEvent(self, e):
        super().showEvent(e)
        self.jump(0)

    def clicked(self, row, prog, gpos):
        ch, _progs = self.grid.rows[row]
        now = time.time()
        if prog is None or prog[0] <= now < prog[1]:
            self.win.play(ch, force=True)
            self.grid.update()
            return
        start, stop, title, _desc = prog
        m = QMenu(self)
        if stop <= now:
            if self.can_watch(ch, start):
                m.addAction(icon("play", "text", 16), "Assistir", lambda: (self.accept(),
                                                                           self.win.watch_program(ch, prog)))
            else:
                m.addAction("Já passou (este canal não guarda a programação)").setEnabled(False)
        else:
            kinds = self.win.scheduled_kinds(ch, start)
            m.addAction("🔔  " + ("Não lembrar" if "remind" in kinds else "Lembrar 1 min antes"),
                        lambda: self._toggle("remind", ch, prog))
            m.addAction("●  " + ("Não gravar" if "record" in kinds else "Gravar do começo ao fim"),
                        lambda: self._toggle("record", ch, prog))
        m.addSeparator()
        m.addAction(icon("tv", "text", 16), f"Assistir {ch.name} agora", lambda: self.win.play(ch, force=True))
        m.exec(gpos)

    def _toggle(self, kind, ch, prog):
        self.win.toggle_schedule(kind, ch, prog[0], prog[1], prog[2])
        self.grid.update()
