"""Banner "Hoje" no topo do Início: jogos do dia, lançamentos de filmes e notícias, lado a lado.

Jogos vêm da agenda tirada do guia (sports.py, via janela.sport_games); lançamentos são filmes novos do
seu catálogo (abrem a página do filme) mais manchetes de estreias no cinema e no streaming; notícias são as
principais manchetes do Google Notícias (news.py). Clique numa linha para assistir/abrir; as manchetes
abrem no navegador. Com muitas manchetes, a coluna vai trocando de página sozinha.
"""
import random
import time

from PyQt6.QtCore import QRect, QSize, Qt, QTimer, QUrl
from PyQt6.QtGui import QColor, QDesktopServices, QFont, QFontMetrics, QPainter
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QVBoxLayout, QWidget

from . import catalog as cat
from .catalog import CatalogNode, display_title, media_is_video, year_of
from .news import NewsFeed, ago
from .theme import T

LINES = 4               # linhas por coluna
PAGE_EVERY_MS = 12000   # troca de página das manchetes
WEEKDAYS = ("seg", "ter", "qua", "qui", "sex", "sáb", "dom")


class BannerLine(QWidget):
    """Uma linha clicável: selo à esquerda (horário/"AGORA"/"NO APP"), texto e um complemento apagado."""

    def __init__(self, badge_w):
        super().__init__()
        self.badge_w = badge_w
        self.badge, self.badge_color, self.text, self.sub, self.action = "", None, "", "", None
        self.setFixedHeight(26)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.setMouseTracking(True)
        self._hover = False

    def set_line(self, badge="", badge_color=None, text="", sub="", tip="", action=None):
        self.badge, self.badge_color, self.text, self.sub, self.action = badge, badge_color, text, sub, action
        self.setToolTip(tip or (f"{text} · {sub}" if sub else text))
        self.setCursor(Qt.CursorShape.PointingHandCursor if action else Qt.CursorShape.ArrowCursor)
        self.update()

    def enterEvent(self, e):
        self._hover = True
        self.update()

    def leaveEvent(self, e):
        self._hover = False
        self.update()

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and self.action and self.rect().contains(e.position().toPoint()):
            self.action()

    def paintEvent(self, _e):
        if not self.text:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self._hover and self.action:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(T["surface2"]))
            p.drawRoundedRect(self.rect(), 7, 7)
        x, h = 6, self.height()
        if self.badge:
            f = QFont(self.font())
            f.setPointSizeF(7.5)
            f.setBold(True)
            p.setFont(f)
            p.setPen(QColor(self.badge_color or T["muted"]))
            p.drawText(QRect(x, 0, self.badge_w, h), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                       self.badge)
        if self.badge_w:
            x += self.badge_w + 4
        room = self.width() - x - 6
        f = QFont(self.font())
        f.setPointSizeF(9.5)
        f.setWeight(QFont.Weight.DemiBold)
        fm = QFontMetrics(f)
        main = fm.elidedText(self.text, Qt.TextElideMode.ElideRight, room)
        p.setFont(f)
        p.setPen(QColor(T["text"]))
        p.drawText(QRect(x, 0, room, h), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, main)
        used = fm.horizontalAdvance(main)
        if self.sub and main == self.text and room - used > 40:
            f2 = QFont(self.font())
            f2.setPointSizeF(8.5)
            sub = QFontMetrics(f2).elidedText("  ·  " + self.sub, Qt.TextElideMode.ElideRight, room - used)
            p.setFont(f2)
            p.setPen(QColor(T["muted"]))
            p.drawText(QRect(x + used, 0, room - used, h), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                       sub)


class BannerColumn(QWidget):
    def __init__(self, title, more_text, on_more, badge_w):
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(1)
        head = QHBoxLayout()
        head.setContentsMargins(6, 0, 2, 4)
        self.title = QLabel(title)
        head.addWidget(self.title)
        self.note = QLabel("")
        head.addWidget(self.note)
        head.addStretch(1)
        self.more = QPushButton(more_text)
        self.more.setFlat(True)
        self.more.setCursor(Qt.CursorShape.PointingHandCursor)
        self.more.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.more.clicked.connect(on_more)
        head.addWidget(self.more)
        lay.addLayout(head)
        self.lines = [BannerLine(badge_w) for _ in range(LINES)]
        for ln in self.lines:
            lay.addWidget(ln)
        lay.addStretch(1)
        self.pages, self.page = [[]], 0

    def style(self):
        self.title.setStyleSheet(f"font-size: 10.5pt; font-weight: 800; color: {T['strong']}; background: transparent;")
        self.note.setStyleSheet(f"color: {T['muted']}; font-size: 8.5pt; background: transparent;")
        self.more.setStyleSheet(f"QPushButton {{ color: {T['accent2'] if T['mode'] == 'dark' else T['accent']};"
                                " border: none; background: transparent; font-size: 8.5pt; font-weight: 700;"
                                " padding: 0 4px; } QPushButton:hover { text-decoration: underline; }")

    def set_rows(self, rows, empty=""):
        """rows: [dict(badge, badge_color, text, sub, tip, action)] — divididas em páginas de LINES."""
        self.pages = [rows[i:i + LINES] for i in range(0, len(rows), LINES)] or [[]]
        if not rows and empty:
            self.pages = [[{"text": empty}]]
        self.page %= len(self.pages)
        self.show_page()

    def next_page(self):
        if len(self.pages) > 1:
            self.page = (self.page + 1) % len(self.pages)
            self.show_page()

    def show_page(self):
        rows = self.pages[self.page]
        for i, ln in enumerate(self.lines):
            ln.set_line(**rows[i]) if i < len(rows) else ln.set_line()


class TodayBanner(QFrame):
    def __init__(self, page):
        super().__init__(objectName="todayBanner")
        self.page = page
        self.news = NewsFeed(self)
        self.news.changed.connect(self.refresh)
        self._picks = (None, [])   # (dia, filmes escolhidos) — mesma escolha o dia todo
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 12, 14, 10)
        lay.setSpacing(10)
        self.games = BannerColumn("⚽  Jogos de hoje", "Agenda ›", lambda: page.open_section("sports"), 50)
        self.movies = BannerColumn("🎬  Lançamentos", "Ver filmes ›", self._open_movies, 54)
        self.headlines = BannerColumn("📰  Notícias", "Mais ›", lambda: self._open_url(
            "https://news.google.com/home?hl=pt-BR&gl=BR&ceid=BR:pt-419"), 0)
        self.seps = []
        for i, col in enumerate((self.games, self.movies, self.headlines)):
            if i:
                sep = QFrame()
                sep.setFixedWidth(1)
                self.seps.append(sep)
                lay.addWidget(sep)
            lay.addWidget(col, 1)
        self.setFixedHeight(12 + 10 + 26 + LINES * 27 + 6)
        self.pager = QTimer(self, interval=PAGE_EVERY_MS, timeout=self._flip)
        self.clock = QTimer(self, interval=60_000, timeout=self.refresh)   # "AGORA", "em 5 min"

    def sizeHint(self):
        return QSize(800, self.height())

    def _flip(self):
        for col in (self.games, self.movies, self.headlines):
            col.next_page()

    def showEvent(self, e):
        super().showEvent(e)
        self.pager.start()
        self.clock.start()
        self.refresh()

    def hideEvent(self, e):
        super().hideEvent(e)
        self.pager.stop()
        self.clock.stop()

    @staticmethod
    def _open_url(url):
        if url:
            QDesktopServices.openUrl(QUrl(url))

    # --- dados
    def _game_rows(self):
        win, now = self.page.win, time.time()
        lt = time.localtime(now)
        midnight = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday + 1, 0, 0, 0, 0, 0, -1))
        games = [(g, c) for g, c in win.sport_games() if not g.replay]
        today = [(g, c) for g, c in games if g.start < midnight]
        label = "hoje"
        if not today:                     # acabaram os de hoje: mostra os de amanhã
            today, label = [(g, c) for g, c in games if g.start >= midnight][:LINES * 3], "amanhã"
        # os que passam nos seus canais na frente; em cada grupo, o que está passando e depois os próximos
        today.sort(key=lambda gc: (not gc[1], gc[0].start > now, gc[0].start))
        rows = []
        for g, chs in today[:LINES * 3]:
            if g.start <= now < g.stop:
                badge, color = "● AGORA", T["bad"]
            elif label == "amanhã":
                badge, color = time.strftime("%H:%M", time.localtime(g.start)), T["muted"]
            else:
                mins = int((g.start - now) // 60)
                badge = f"em {max(1, mins)}m" if mins < 60 else time.strftime("%H:%M", time.localtime(g.start))
                color = T["accent"] if mins < 60 else T["text"]
            where = chs[0].name if chs else "fora das suas listas"
            sub = " · ".join(x for x in (g.competition or g.sport_name, where) if x)
            rows.append({"badge": badge, "badge_color": color, "text": f"{g.emoji} {g.teams}", "sub": sub,
                         "tip": f"{g.teams}\n{g.competition or g.sport_name}\n" +
                                (", ".join(c.name for c in chs[:4]) if chs else "Canal fora das suas listas") +
                                ("\nClique para assistir" if chs else ""),
                         "action": (lambda g=g, c=chs: win.watch_game(g, c)) if chs
                         else (lambda: self.page.open_section("sports"))})
        n = len(today)
        self.games.title.setText("⚽  Jogos de " + label)
        self.games.note.setText(f"  {n}" if n else "")
        if not win.epg.has_data():
            empty = ("Guia de programação carregando…" if win.cfg["epg_enabled"]
                     else "Ligue o guia nas Configurações para ver os jogos")
        else:
            empty = "Nenhum jogo no guia hoje"
        return rows, empty

    def _movie_picks(self):
        """Filmes novos do catálogo (deste ano e do anterior), sorteados uma vez por dia."""
        page = self.page
        day = time.strftime("%Y-%m-%d")
        catalog = page.catalog
        if catalog is None:
            return []
        if self._picks[0] == (day, id(catalog)):
            return self._picks[1]
        year = time.localtime().tm_year
        node = catalog.node((cat.MOVIES, "Lançamentos")) or catalog.node((cat.MOVIES,))
        pool = []
        if node:
            for i in node.indices[:600]:
                ch = catalog.channels[i]
                if media_is_video(ch) and catalog.logo_of(ch) and year_of(ch.name) >= year - 1:
                    pool.append(ch)
            if not pool:
                pool = [catalog.channels[i] for i in node.indices[:60] if catalog.logo_of(catalog.channels[i])]
        pool = [c for c in page.allowed(pool) if catalog.path_of(c)[0] not in (cat.ADULT, cat.UNSTABLE)]
        rnd = random.Random(day)
        rnd.shuffle(pool)
        pool.sort(key=lambda c: -year_of(c.name))   # os mais novos primeiro (sort estável mantém o sorteio)
        self._picks = ((day, id(catalog)), pool[:6])
        return self._picks[1]

    def _movie_rows(self):
        page = self.page
        mine = []
        for ch in self._movie_picks():
            y = year_of(ch.name)
            info = page.info_of(ch, fetch=False) or {}
            path = page.catalog.path_of(ch)
            genre = info.get("genre") or (path[1] if len(path) > 1 and path[1] not in ("Lançamentos", "Legendados")
                                          else "")
            mine.append({"badge": "NO APP", "badge_color": T["ok"], "text": display_title(ch.name),
                         "sub": " · ".join(x for x in (str(y) if y else "", genre.split(",")[0].strip()) if x),
                         "tip": f"{display_title(ch.name)}\nNo seu catálogo — clique para ver detalhes",
                         "action": lambda c=ch: page.activate(c, None)})
        heads = [{"badge": "ESTREIA", "badge_color": T["accent2"] if T["mode"] == "dark" else T["accent"],
                  "text": n["title"], "sub": " · ".join(x for x in (n["source"], ago(n["ts"])) if x),
                  "tip": f"{n['title']}\n{n['source']}\nAbrir no navegador",
                  "action": lambda u=n["url"]: self._open_url(u)} for n in self.news.items["movies"][:8]]
        # cada página: 2 filmes do catálogo + 2 manchetes de estreias (o que faltar de um, o outro completa)
        rows = []
        while mine or heads:
            take = [mine.pop(0) for _ in range(min(2, len(mine)))]
            take += [heads.pop(0) for _ in range(min(LINES - len(take), len(heads)))]
            take += [mine.pop(0) for _ in range(min(LINES - len(take), len(mine)))]
            rows += take
        return rows

    def _news_rows(self):
        return [{"text": n["title"], "sub": " · ".join(x for x in (n["source"], ago(n["ts"])) if x),
                 "tip": f"{n['title']}\n{n['source']} · {ago(n['ts'])}\nAbrir no navegador",
                 "action": lambda u=n["url"]: self._open_url(u)} for n in self.news.items["news"][:12]]

    def _open_movies(self):
        page = self.page
        if page.catalog is None:
            return
        node = page.catalog.node((cat.MOVIES, "Lançamentos"))
        page.open_path(node.path if node else (cat.MOVIES,))

    def refresh(self):
        if not self.isVisible():
            return
        self.setStyleSheet(f"QFrame#todayBanner {{ background: {T['surface']}; border: 1px solid {T['border']};"
                           " border-radius: 16px; }")
        for s in self.seps:
            s.setStyleSheet(f"background: {T['border']}; border: none;")
        for col in (self.games, self.movies, self.headlines):
            col.style()
        kids = self.page.win.cfg["kids"]
        self.headlines.setVisible(not kids)        # manchetes não são para o modo infantil
        self.seps[-1].setVisible(not kids)
        rows, empty = self._game_rows()
        self.games.set_rows(rows, empty)
        self.movies.set_rows(self._movie_rows(), "Sem lançamentos por enquanto")
        offline = "Sem internet — as manchetes voltam quando conectar"
        self.headlines.set_rows(self._news_rows(), offline if not self.news.updated else "Carregando manchetes…")
        upd = self.news.updated
        self.headlines.note.setText(f"  {ago(upd)}" if upd else "")
