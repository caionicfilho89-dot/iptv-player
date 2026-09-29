"""Preferências do usuário (player_config.json)."""
import json
import time

from .paths import CONFIG_FILE

DEAD_TTL = 6 * 3600  # canal marcado offline fica assim por 6h

DEFAULT_EPG_URLS = [
    "https://epgshare01.online/epgshare01/epg_ripper_BR1.xml.gz",
    "https://epgshare01.online/epgshare01/epg_ripper_PT1.xml.gz",
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
    "check_updates": True, "last_update_check": 0, "latest_version": "",
    "mosaic_size": 2,
}


class Config:
    def __init__(self):
        self.data = json.loads(json.dumps(DEFAULTS))  # cópia profunda
        try:
            # utf-8-sig aceita o arquivo mesmo se foi salvo no Bloco de Notas (com BOM)
            loaded = json.loads(CONFIG_FILE.read_text(encoding="utf-8-sig"))
            if not isinstance(loaded, dict):
                raise ValueError("formato inválido")
            self.data.update(loaded)
        except OSError:
            pass  # primeira execução
        except ValueError:
            # arquivo corrompido: guarda uma cópia em vez de apagar favoritos e listas em silêncio
            try:
                CONFIG_FILE.replace(CONFIG_FILE.with_name(f"player_config.corrompido-{int(time.time())}.json"))
            except OSError:
                pass
        now = time.time()
        if not isinstance(self.data.get("dead"), dict):
            self.data["dead"] = {}
        self.data["dead"] = {u: t for u, t in self.data["dead"].items() if now - t < DEAD_TTL}

    def __getitem__(self, k):
        return self.data[k]

    def __setitem__(self, k, v):
        self.data[k] = v

    def save(self):
        try:
            tmp = CONFIG_FILE.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=1), encoding="utf-8")
            tmp.replace(CONFIG_FILE)
        except OSError:
            pass
