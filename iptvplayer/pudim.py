"""Ajustes do PudimFlix para listas de conta (Xtream) com filmes e séries.

- filmes e episódios guardam a posição a cada 5 s (por nome, vale mesmo se o link mudar) e continuam de onde pararam;
- se a conexão do filme cai, reconecta sozinho do ponto salvo (até 3 vezes), sem perder a barra de tempo;
- links da conta abrem direto (sem o buffer de pausar o ao vivo) e com User-Agent de navegador;
- canal que falha não faz o player sair pulando: a conta só permite uma conexão por vez."""
import hashlib
import json
import os
import time
from urllib.parse import urlsplit

from PyQt6.QtCore import QTimer

from .catalog import as_vod, catalog_fold, media_is_video
from .log import log
from .mainwindow import MainWindow
from .paths import CACHE
from .vlcload import vlc

BOOKMARKS = CACHE / "vod_bookmarks.json"
RETRY_DELAYS_MS = (4000, 7000, 10000)
STALL_SECONDS = 25


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
    def __init__(self, cfg=None):
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
        super().__init__(cfg)

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
        return super().play(ch, *args, **kwargs)

    def play_ondemand(self, item, url, info="", start=None, live=None, playlist=None):
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
            self._retries, self._retry_window = 0, time.monotonic()
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
        if ch is None:
            return
        log.warning("Canal não abriu: %s (%s)", ch.name, reason)
        self.player.stop()
        self.explore_btn.setChecked(False)
        self.zap_btn.setChecked(False)
        self.confirmed = False
        self.vod = None
        self._last_failed = ch
        self.current = None
        self.session_status.pop(ch.url, None)  # não marca como fora do ar: pode ser a conexão da conta ocupada
        self._set_status("✕ Não abriu", "bad")
        self.video.set_message(f"Não foi possível abrir “{ch.name}”.\nTente de novo em instantes ou escolha outro.")
        self._update_count()

    def _vod_failed(self, reason):
        if not self.vod or not _recorded(self.current):
            return super()._vod_failed(reason)
        if self._recovery_pending:
            return
        self._checkpoint(force=True)
        now = time.monotonic()
        if now - self._retry_window > 300:
            self._retry_window, self._retries = now, 0
        self._retries += 1
        self.player.stop()
        self.confirmed = False
        self.seek_row.show()
        self._recovery_pending = True
        if self._retries > len(RETRY_DELAYS_MS):
            self.video.set_message("A conexão caiu várias vezes.\nSeu ponto foi salvo: clique em ▶ para continuar.")
            self.vod["ended"] = True
            self._recovery_pending = False
            log.warning("Filme: reconexão desistiu depois de %d tentativas", self._retries - 1)
            return
        self._recovery_token += 1
        token = self._recovery_token
        self.video.set_message("Reconectando do ponto onde parou…")
        log.warning("Filme: reconectando (tentativa %d, %.0fs) — %s", self._retries, self._good_pos, reason)

        def reconnect():
            if token != self._recovery_token or self._vod_context is None:
                return
            item, url, info, live, playlist = self._vod_context
            self._recovering = True
            try:
                self.play_ondemand(item, url, info=info, start=self.resume_start(item), live=live, playlist=playlist)
            finally:
                self._recovering = False
        QTimer.singleShot(RETRY_DELAYS_MS[self._retries - 1], reconnect)

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
        if not (self.current is not None and _account_link(self.current.url)):
            return super()._open_stream(stream)
        keep = self.cfg["timeshift"]
        self.cfg["timeshift"] = False
        try:
            return super()._open_stream(stream)
        finally:
            self.cfg["timeshift"] = keep
