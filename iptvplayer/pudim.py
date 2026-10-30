"""Ajustes do PudimFlix para listas de conta (Xtream) com filmes e séries.

- filmes e episódios guardam a posição a cada 5 s (por nome, vale mesmo se o link mudar) e continuam de onde pararam;
- travou ou caiu: reconecta na hora, quantas vezes for preciso — o filme volta do ponto onde parou e o canal
  volta ao ao vivo; o intervalo entre tentativas cresce até 5 s para não sobrecarregar o servidor;
- links da conta abrem direto (sem o buffer de pausar o ao vivo) e com User-Agent de navegador;
- canal que falha não faz o player sair pulando: a conta só permite uma conexão por vez."""
import hashlib
import json
import os
import threading
import time
from urllib.parse import urljoin, urlsplit

import requests
from PyQt6.QtCore import QTimer, pyqtSignal

from .catalog import as_vod, catalog_fold, media_is_video
from .log import log
from . import mainwindow as _mw
from .mainwindow import MainWindow
from .paths import CACHE
from .vlcload import vlc

BOOKMARKS = CACHE / "vod_bookmarks.json"
STALL_SECONDS = 8            # filme parado (sem pausa) por mais que isso = travou
_mw.STALL_TIMEOUT = 6        # canal ao vivo parado por mais que isso = travou (antes 12 s)


def retry_delay_ms(attempt):
    """1ª tentativa quase na hora; depois 2 s, 3,5 s… até 5 s entre uma e outra."""
    return min(5000, 500 + 1500 * (attempt - 1))


EDGE_TRIES = 4              # servidores de vídeo testados antes de entregar o link original ao VLC
EDGE_WAIT = 8               # segundos sem receber vídeo = servidor travado (os bons às vezes levam 4 s)


def pick_edge(url):
    """A conta manda cada pedido para um servidor de vídeo (302 com token). Alguns aceitam a conexão e nunca
    mandam o vídeo (visto com VPN ligada): testa o servidor e, se travar, pede outro. None = nenhum respondeu."""
    headers = {"User-Agent": "Mozilla/5.0"}
    for attempt in range(1, EDGE_TRIES + 1):
        loc = url
        try:
            with requests.get(url, headers=headers, allow_redirects=False, timeout=8) as r:
                if not (r.is_redirect and r.headers.get("Location")):
                    return url   # sem servidor de vídeo separado: o VLC abre direto
                loc = urljoin(url, r.headers["Location"])
            with requests.get(loc, headers=headers, stream=True, timeout=(4, EDGE_WAIT)) as v:
                if v.ok and next(v.iter_content(8192), b""):
                    return loc
                problem = f"HTTP {v.status_code}"
        except requests.RequestException as e:
            problem = type(e).__name__
        log.warning("Servidor de vídeo %s não mandou o vídeo (%s), tentativa %d", urlsplit(loc).netloc,
                    problem, attempt)
    return None


def _recorded(item):
    return item is not None and media_is_video(item)


def _bookmark_key(item):
    return hashlib.sha256(catalog_fold(item.name).strip().encode("utf8")).hexdigest()


def _account_link(url):
    """Link de conta Xtream (…/usuário/senha/id): uma conexão só e sem buffer de timeshift."""
    pieces = [p for p in urlsplit(url).path.split("/") if p]
    if pieces and pieces[0] in ("live", "movie", "series"):
        pieces = pieces[1:]
    return len(pieces) == 3 and pieces[0].isdigit() and pieces[1].isdigit()


class PudimWindow(MainWindow):
    _edge_found = pyqtSignal(int, object)   # (pedido, link do servidor de vídeo que respondeu ou None)

    def __init__(self, cfg=None):
        self._edge_token = 0
        self._edge_pending = None    # (pedido, link, opções) esperando o teste do servidor de vídeo
        self._bookmarks = {}
        try:
            self._bookmarks = json.loads(BOOKMARKS.read_text(encoding="utf8"))
        except (OSError, ValueError):
            pass
        self._recovery_token = 0
        self._recovery_pending = False
        self._recovering = False
        self._vod_context = None
        self._last_failed = None
        self._live_pending = False   # canal esperando a próxima tentativa de reconexão
        self._live_retries = 0
        super().__init__(cfg)
        self._edge_found.connect(self._on_edge_found)

    # --- marcadores
    def _write_bookmarks(self):
        tmp = BOOKMARKS.with_suffix(".writing.json")
        try:
            tmp.write_text(json.dumps(self._bookmarks, ensure_ascii=False, separators=(",", ":")), encoding="utf8")
            os.replace(tmp, BOOKMARKS)
        except OSError as exc:
            log.warning("Não foi possível salvar o ponto do filme: %s", type(exc).__name__)

    def _checkpoint(self, force=False, final=False):
        if not self.vod or not _recorded(self.current):
            return
        now = time.monotonic()
        if not force and now - getattr(self, "_last_checkpoint", 0) < 5:
            return
        pos = max(0, self.player.get_time() / 1000) or getattr(self, "_good_pos", 0)
        length = max(0, self.player.get_length() / 1000) or getattr(self, "_good_len", 0)
        if pos < 5 and not final:
            return
        self._bookmarks[_bookmark_key(self.current)] = {"name": self.current.name, "position": pos, "length": length,
                                                        "saved": time.time(), "finished": bool(final)}
        self.cfg["resume"][self.current.url] = [-1 if final else pos, length, time.time()]
        self._last_checkpoint = now
        self._write_bookmarks()
        if now - getattr(self, "_last_cfg_save", 0) > 60:   # sobrevive a queda de energia / fechar à força
            self._last_cfg_save = now
            self.cfg.save()

    def resume_of(self, ch):
        rec = self._bookmarks.get(_bookmark_key(ch))
        mine = (rec["position"], rec.get("length") or 0, rec.get("saved") or 0) \
            if rec and not rec.get("finished") and rec.get("position", 0) > 0 else None
        base = super().resume_of(ch)
        return max((x for x in (mine, base) if x), key=lambda r: r[2], default=None)

    def resume_list(self):
        seen = {id(r[0]) for r in super().resume_list()}
        out = super().resume_list()
        if self.catalog is not None:
            for rec in self._bookmarks.values():
                if rec.get("finished") or rec.get("position", 0) <= 0:
                    continue
                ch = self.catalog.find_name(rec.get("name", ""))
                if ch is not None and id(ch) not in seen:
                    seen.add(id(ch))
                    out.append((ch, rec["position"], rec.get("length") or 0, rec.get("saved") or 0))
        return sorted(out, key=lambda r: -r[3])

    def resume_start(self, item):
        saved = self._bookmarks.get(_bookmark_key(item), {})
        if saved.get("finished"):
            return 0.0
        return max(0.0, float(saved.get("position") or 0)) or self._resume_pos(item.url)

    # --- tocar
    def play(self, ch, *args, **kwargs):
        if _recorded(ch) and ch.kind != "series":
            stream = kwargs.get("stream")
            url = stream[0] if isinstance(stream, (tuple, list)) and stream else ch.url
            return self.play_ondemand(as_vod(ch), url)
        self._checkpoint(force=True)
        self._recovery_token += 1
        self._recovery_pending = False
        self._last_failed = None
        if not self._live_pending:   # escolheu outro canal: zera a contagem de tentativas
            self._live_retries = 0
        self._live_pending = False
        return super().play(ch, *args, **kwargs)

    def play_ondemand(self, item, url, info="", start=None, live=None, playlist=None):
        self._edge_pending = None
        self._checkpoint(force=True)
        self._last_failed = None
        if _recorded(item):
            item = as_vod(item)
        self._recovery_token += 1
        self._recovery_pending = False
        if start is None:
            start = self.resume_start(item) if _recorded(item) else 0.0
        start = max(0.0, float(start or 0))
        saved = self._bookmarks.get(_bookmark_key(item), {})
        self._good_pos, self._good_len = start, float(saved.get("length") or 0)
        self._last_checkpoint = self._progress_clock = time.monotonic()
        self._observed = -1
        self._seek_target, self._seek_attempts = start, 0
        if not self._recovering:
            self._retries = 0
        self._live_pending = False
        result = super().play_ondemand(item, url, info=info or "", start=start, live=live, playlist=playlist)
        self._vod_context = (item, url, info, live, playlist)
        self.seek_row.show()
        self.show_player()
        return result

    def _replay(self):
        item = self.current or self._last_failed
        if _recorded(item):
            self._checkpoint(force=True)
            ctx = self._vod_context if self._vod_context and self._vod_context[0].url == item.url else \
                (item, item.url, "", None, None)
            return self.play_ondemand(ctx[0], ctx[1], info=ctx[2], start=self.resume_start(item), live=ctx[3],
                                      playlist=ctx[4])
        if item is not None:
            return super().play(item, auto=True, force=True, stream=self.stream)

    _vod_restart = _replay

    def toggle_pause(self):
        if self.current is None and self._last_failed is not None:
            return self._replay()   # ▶ depois de uma falha tenta o mesmo canal de novo
        return super().toggle_pause()

    # --- falhas: ao vivo para no canal; filme reconecta do ponto salvo
    def _on_fail(self, reason):
        if self.vod and _recorded(self.current):
            return self._vod_failed(reason)
        ch = self.current
        if ch is None or self._live_pending:
            return
        if self.explore_btn.isChecked() or self.zap_btn.isChecked():
            return super()._on_fail(reason)   # zapping/explorar: segue para o próximo canal, como antes
        self._live_retries += 1
        log.warning("Canal caiu: %s (%s) — reconectando, tentativa %d", ch.name, reason, self._live_retries)
        self.player.stop()
        self.confirmed = False
        self.session_status.pop(ch.url, None)  # não marca como fora do ar: pode ser a conexão da conta ocupada
        self._live_pending = True
        self._recovery_token += 1
        token, stream = self._recovery_token, self.stream
        self._set_status(f"↻ Reconectando… (tentativa {self._live_retries})", "warn")
        hint = "\nSe estiver usando VPN, desligue ou deixe o PudimFlix fora dela." if self._live_retries >= 3 else ""
        self.video.set_message(f"Reconectando “{ch.name}”…{hint}")

        def reconnect():
            if token != self._recovery_token or self.current is not ch:
                return   # escolheu outro canal ou parou
            self._live_pending = False
            MainWindow.play(self, ch, auto=True, force=True, stream=stream)
        QTimer.singleShot(retry_delay_ms(self._live_retries), reconnect)

    def _monitor_tick(self):
        if self._live_pending or self._edge_pending:
            return   # esperando a próxima tentativa ou o teste do servidor: não conta como nova falha
        super()._monitor_tick()
        if self.confirmed and self._live_retries and not self.vod:
            log.info("Reconectou depois de %d tentativa(s)", self._live_retries)
            self._live_retries = 0

    def _vod_failed(self, reason):
        if not self.vod or not _recorded(self.current):
            return super()._vod_failed(reason)
        if self._recovery_pending:
            return
        self._checkpoint(force=True)
        self._retries += 1
        self.player.stop()
        self.confirmed = False
        self.seek_row.show()
        self._recovery_pending = True
        self._recovery_token += 1
        token = self._recovery_token
        resume_at = max(0.0, self._good_pos - 2) if self._good_pos > 5 else self.resume_start(self.current)
        self._set_status(f"↻ Reconectando… (tentativa {self._retries})", "warn")
        self.video.set_message(f"Reconectando de {self._fmt_clock(resume_at)}…")
        log.warning("Filme: reconectando (tentativa %d, %.0fs) — %s", self._retries, resume_at, reason)

        def reconnect():
            if token != self._recovery_token or self._vod_context is None:
                return
            item, url, info, live, playlist = self._vod_context
            self._recovering = True
            try:
                self.play_ondemand(item, url, info=info, start=resume_at, live=live, playlist=playlist)
            finally:
                self._recovering = False
        QTimer.singleShot(retry_delay_ms(self._retries), reconnect)

    def _vod_tick(self):
        if not self.vod or self._recovery_pending:
            return
        state = self.player.get_state()
        now = time.monotonic()
        pos, length = self.player.get_time() / 1000, self.player.get_length() / 1000
        if pos > 0:
            self._good_pos = pos
        if length > 0:
            self._good_len = length
        if state == vlc.State.Ended and _recorded(self.current) and self._good_len > 0 \
                and self._good_pos < self._good_len - 20:
            return self._vod_failed("o servidor interrompeu o vídeo antes do fim")
        super()._vod_tick()
        if not self.vod or self._recovery_pending or not _recorded(self.current):
            return
        self.seek_row.show()
        self._checkpoint()
        if self.confirmed and self._retries:
            log.info("Filme reconectou depois de %d tentativa(s)", self._retries)
            self._retries = 0
        if state == vlc.State.Paused:
            self._progress_clock = now
            return
        if pos > 0 and pos != self._observed:
            self._observed, self._progress_clock = pos, now
        # alguns servidores ignoram o start-time: pula para o ponto salvo quando o vídeo já começou
        if self._seek_target > 5 and self.confirmed and self.player.is_seekable():
            if abs(pos - self._seek_target) > 8 and self._seek_attempts < 3:
                self.player.set_time(int(self._seek_target * 1000))
                self._seek_attempts += 1
                self._progress_clock = now
            else:
                self._seek_target = 0
        if now - self._progress_clock > STALL_SECONDS and self.confirmed and \
                state in (vlc.State.Playing, vlc.State.Buffering, vlc.State.Opening):
            self._vod_failed(f"parou de avançar por {STALL_SECONDS} segundos")

    def _vod_seek_update(self):
        if self.seek.isSliderDown():
            return
        length = max(self.player.get_length() / 1000, getattr(self, "_good_len", 0))
        pos = self.player.get_time() / 1000
        if pos < 0 or self._recovery_pending:
            pos = getattr(self, "_good_pos", 0)
        self.seek.blockSignals(True)
        self.seek.setRange(0, max(1, int(length)))
        self.seek.setValue(int(max(0, min(pos, length or pos))))
        self.seek.blockSignals(False)
        self.seek_pos_lbl.setText(self._fmt_clock(pos))
        self.seek_end_lbl.setText(self._fmt_clock(length) if length else "")

    def _save_resume(self, final=False):
        if _recorded(self.current):
            self._checkpoint(force=final, final=final)
        else:
            super()._save_resume(final)

    def _vod_ended(self):
        self._checkpoint(force=True, final=True)
        return super()._vod_ended()

    def stop(self):
        self._checkpoint(force=True)
        self._recovery_token += 1
        self._recovery_pending = False
        self._live_pending = False
        self._live_retries = 0
        self._edge_pending = None
        return super().stop()

    def closeEvent(self, e):
        self._checkpoint(force=True)
        return super().closeEvent(e)

    # --- rede
    def _set_media(self, url, opts):
        opts = list(opts or ())
        if str(url).startswith(("http://", "https://")):
            opts = [o for o in opts if not str(o).lstrip(":").startswith("http-user-agent=")]
            opts.append(":http-user-agent=Mozilla/5.0")
            if self.vod:
                opts = [o for o in opts if not str(o).lstrip(":").startswith(("network-caching=", "file-caching="))]
                opts += [":network-caching=5000", ":file-caching=5000", ":http-reconnect", ":tcp-timeout=15000"]
            else:
                opts.append(":network-caching=2000")
        return super()._set_media(url, opts)

    def _open_stream(self, stream):
        self._edge_token += 1
        self._edge_pending = None
        if not (self.current is not None and _account_link(self.current.url)):
            return super()._open_stream(stream)
        if self._live_retries and not self.vod and str(stream[0]).startswith("http://"):
            # caiu: testa o servidor de vídeo antes, senão o VLC cairia de novo no mesmo servidor travado
            self.player.stop()
            self.timeshift.park()
            self._ts_sid = None
            token = self._edge_token
            self._edge_pending = (token, stream[0], stream[1])
            threading.Thread(target=lambda: self._edge_found.emit(token, pick_edge(stream[0])),
                             name="servidor-video", daemon=True).start()
            return
        keep = self.cfg["timeshift"]
        self.cfg["timeshift"] = False
        try:
            return super()._open_stream(stream)
        finally:
            self.cfg["timeshift"] = keep

    def _on_edge_found(self, token, url):
        if not self._edge_pending or self._edge_pending[0] != token or self.current is None:
            return   # trocou de canal ou parou enquanto testava
        _, original, opts = self._edge_pending
        self._edge_pending = None
        if url is None:
            log.warning("Nenhum servidor de vídeo mandou o vídeo de %s", self.current.name)
        self.started_at = time.monotonic()   # o tempo do teste não conta como demora para abrir
        self.last_progress = self.started_at
        self._set_media(url or original, opts)
