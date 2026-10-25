"""Preferências do usuário (player_config.json)."""
import json
import logging
import os
import time

from .paths import CONFIG_FILE, LIST_CACHE

log = logging.getLogger(__name__)
BACKUP_FILE = CONFIG_FILE.with_name("player_config.bak")


def _read(path):
    """O dict salvo em path, ou None se não existe ou está corrompido (ex.: cheio de zeros após uma queda)."""
    try:
        # utf-8-sig aceita o arquivo mesmo se foi salvo no Bloco de Notas (com BOM)
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None

DEAD_TTL = 6 * 3600  # canal marcado offline fica assim por 6h

DEFAULT_EPG_URLS = [
    "https://epgshare01.online/epgshare01/epg_ripper_BR1.xml.gz",
    "https://epgshare01.online/epgshare01/epg_ripper_PT1.xml.gz",
    "https://epgshare01.online/epgshare01/epg_ripper_PLEX1.xml.gz",  # canais FAST dos EUA (E! Keeping Up etc.)
]

DEFAULTS = {
    "favorites": [], "recents": [], "dead": {}, "volume": 80,
    "skip_dead": True, "hide_dead": False,
    "zap_interval": 30, "zap_random": False, "zap_favs": False,
    "last_category": "melhor_iptv", "last_url": "",
    "theme": "dark", "accent": "violet", "view_mode": "list",
    "auto_update_lists": True, "lists_updated": 0,
    "custom_sources": [],
    "epg_enabled": True, "epg_urls": DEFAULT_EPG_URLS,
    "check_updates": True, "last_update_check": 0, "latest_version": "", "update_url": "", "update_size": 0,
    "mosaic_size": 2,
    "alt_links": {}, "explore_pos": [],
    "captions": False, "cap_model": "small", "cap_source": "auto", "cap_original": False, "cap_scale": 1.0, "cap_translator": "google",
    "dub_voice": "", "dub_duck": 0.25,
    "timeshift": True, "timeshift_minutes": 30,
    # conexão: cache de rede do VLC (ms), conexões simultâneas por canal/download, canal anterior em segundo plano
    "net_cache_ms": 1500, "net_conns": 4, "keep_prev": False,
    # jogos: avisar antes (minutos), falar o aviso, só nos canais das listas, reprises, esportes ([] = todos),
    # times favoritos ([] = todos), jogos marcados com "Avisar" e os já anunciados ({chave: início})
    "sports_auto": True, "sports_lead": 10, "sports_voice": True, "sports_mine": True, "sports_replays": False,
    "sports_kinds": [], "sports_teams": [], "sports_alerts": {}, "sports_announced": {},
    # dicas pelo seu gosto: aviso diário com uma novidade, dia da última dica e títulos já indicados
    "daily_tip": True, "tip_day": "", "tips_shown": [],
    "dl_dir": "", "dl_upload": False,   # assistir sem internet: pasta dos baixados ("" = Vídeos\PudimFlix Downloads)
    "schedule": [],
    "resume": {},  # onde parou em filmes/episódios: {url: [segundos (-1 = assistido), duração, quando]}
    "region": "all",  # filtro de país da tela Início (themes.REGIONS)
    "sort_mode": "brasil",  # brasil (BR primeiro), list, stable (mais estáveis primeiro) ou name
    "stats": {},  # retrospectiva: {url: {"name","logo","secs","count","last"}}
    "tracks": {},  # áudio/legenda escolhidos por canal: {url: {"audio": nome, "spu": nome ou ""}}
    "remote": False, "remote_key": "",
    "auto_scan": True, "last_full_scan": 0, "merge_dupes": True,
    "kids": False, "kids_pin": "", "kids_cats": ["infantil"],
    "only_my_lists": False,  # só as listas do usuário (sem as categorias embutidas e serviços grátis)
}


class Config:
    def __init__(self):
        self.data = json.loads(json.dumps(DEFAULTS))  # cópia profunda
        loaded = _read(CONFIG_FILE)
        if loaded is None and CONFIG_FILE.exists():
            # arquivo corrompido: guarda uma cópia em vez de apagar favoritos e listas em silêncio
            try:
                CONFIG_FILE.replace(CONFIG_FILE.with_name(f"player_config.corrompido-{int(time.time())}.json"))
            except OSError:
                pass
            loaded = _read(BACKUP_FILE)  # e volta para a última versão boa
            if loaded is not None:
                log.warning("Configurações corrompidas — restauradas da cópia de segurança")
        if loaded:
            self.data.update(loaded)
            if "only_my_lists" not in loaded and loaded.get("custom_sources") and not loaded.get("auto_update_lists", True):
                self.data["only_my_lists"] = True  # v1.13: quem já usava uma lista única continua só com ela
        if not isinstance(self.data.get("dead"), dict):
            self.data["dead"] = {}
        self.prune_dead()
        self._migrate()

    def _migrate(self):
        """v1.11: a "Pluto TV Brasil" virou parte da "Pluto TV" (Brasil + mundo numa lista só)."""
        if self.data.get("last_category") == "pluto_br":
            self.data["last_category"] = "pluto"
        kids = self.data.get("kids_cats")
        if isinstance(kids, list) and "pluto_br" in kids:
            self.data["kids_cats"] = list(dict.fromkeys("pluto" if k == "pluto_br" else k for k in kids))
        old = LIST_CACHE / "pluto_br.m3u"
        if old.exists():  # a "pluto.m3u" antiga só tem o mundo: baixa de novo a lista unificada
            for f in (old, LIST_CACHE / "pluto.m3u"):
                try:
                    f.unlink()
                except OSError:
                    pass

    def prune_dead(self):
        """Esquece as marcas de offline vencidas; devolve quantos canais voltaram."""
        now, dead = time.time(), self.data["dead"]
        expired = [u for u, t in dead.items() if not isinstance(t, (int, float)) or now - t >= DEAD_TTL]
        for u in expired:
            del dead[u]
        return len(expired)

    def __getitem__(self, k):
        return self.data[k]

    def __setitem__(self, k, v):
        self.data[k] = v

    def save(self):
        try:
            tmp = CONFIG_FILE.with_suffix(".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(json.dumps(self.data, ensure_ascii=False, indent=1))
                f.flush()
                os.fsync(f.fileno())  # sem isso, uma queda de energia deixa o arquivo cheio de zeros
            if _read(CONFIG_FILE) is not None:
                CONFIG_FILE.replace(BACKUP_FILE)  # guarda a última versão boa
            tmp.replace(CONFIG_FILE)
        except OSError:
            pass
