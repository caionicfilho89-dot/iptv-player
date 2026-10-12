"""Temas dos canais (Esportes, Notícias, Filmes…) e país de origem, para a tela Início.

Cada canal recebe um tema por regras (grupo da lista, listas temáticas do iptv-org e palavras do nome).
O que as regras não resolvem fica para a IA (Gemma 3, ver ThemeAI), que classifica em segundo plano e
guarda a resposta em temas.json — cada canal é perguntado uma vez só."""
import json
import re
import threading
import unicodedata

from PyQt6.QtCore import QThread, pyqtSignal

from .log import log
from .m3u import is_brazilian, name_key, parse_m3u
from .paths import CACHE

AI_FILE = CACHE / "temas.json"

# chave, rótulo, ícone (na ordem das fileiras da tela Início)
THEMES = [
    ("variedades", "Variedades", "tv"),
    ("esportes", "Esportes", "trophy"),
    ("noticias", "Notícias", "news"),
    ("filmes", "Filmes", "film"),
    ("series", "Séries e novelas", "layers"),
    ("infantil", "Infantil", "smile"),
    ("documentarios", "Documentários e natureza", "globe"),
    ("musica", "Música", "music"),
    ("estilo", "Culinária, casa e estilo", "heart"),
    ("religiosos", "Religiosos", "heart"),
]
THEME_KEYS = [t[0] for t in THEMES]
THEME_LABEL = {k: label for k, label, _ in THEMES}

# listas temáticas do iptv-org (chave da categoria no programa -> tema)
CATEGORY_LISTS = {"filmes": "filmes", "series": "series", "esportes": "esportes", "notícias": "noticias",
                  "documentários": "documentarios", "música": "musica", "infantil": "infantil",
                  "religiosos": "religiosos", "gerais": "variedades"}

# listas de um país só (chave da categoria -> país)
CATEGORY_COUNTRY = {"brasil": "BR", "portugal": "PT"}

# palavras (sem acento, minúsculas) por tema; a ordem importa: "Kids Movies" é infantil, "Gospel Music" é religioso
_WORDS = [
    ("infantil", r"kids?|infant\w*|child\w*|cartoon\w*|anime|animat\w*|animac\w*|desenhos?|junior|jr|baby|bebes?|"
                 r"toons?|kinder\w*|ninos|nick\w*|disney ?jr|gloob\w*|discovery kids|family kids|pequen\w*"),
    ("religiosos", r"relig\w*|faith|gospel|christian\w*|cristo|crista\w*|church|igreja|catolic\w*|evang\w*|bibl\w*|iglesia|jesus|"
                   r"islam\w*|quran|aparecida|cancao nova|rede vida|shalom|adventist\w*|novo tempo|tv mundial|"
                   r"tbn|daystar|ewtn|hope channel|deus|dios"),
    ("esportes", r"sports?\w*|esport\w*|deport\w*|futebol|football|soccer|futbol|golf|tennis|nba|nfl|nhl|mlb|ufc|"
                 r"wwe|fight\w*|boxing|racing|motorsport\w*|nascar|f1|surf\w*|"
                 r"olymp\w*|stadium|bike|ski|espn|fox sports|bein|dazn|sportv|premiere|combate|band ?sports|tnt sports"),
    ("noticias", r"news\w*|noticia\w*|nachrichten|info\w*|jornal\w*|journal|weather|clima|politic\w*|business|"
                 r"financ\w*|econom\w*|bloomberg|cnn|cnbc|msnbc|euronews|al jazeera|cbn|globonews|legislat\w*|"
                 r"senado|camara|congress\w*|parlament\w*|tv justica|press|reuters|sky news|fox news|abc news|"
                 r"cbs news|nbc news|newsmax|jovem pan news"),
    ("filmes", r"movies?|films?|filmes?|cine\w*|pelicul\w*|peliculas|hollywood|western\w*|horror|terror|thriller|"
               r"action|acao|classic movies|paramount movies|filmrise|cinema"),
    ("series", r"series|serie|seriados?|drama\w*|novelas?|telenovel\w*|sitcom\w*|tv shows|shows? classicos|"
               r"crime|mystery|misterio|detective|investiga\w*|soap"),
    ("musica", r"music\w*|musica\w*|musique|radio|hits|mtv|vh1|karaoke|rock|pop|jazz|sertanej\w*|funk|rap|"
               r"hip ?hop|dance|classical|classica|concert\w*|stingray|trace|clubbing|reggae|samba|pagode|forro|"
               r"country music|latin music|vevo|xite|qello"),
    ("documentarios", r"document\w*|nature|natureza|science|ciencia\w*|history|historia|travel\w*|viage\w*|"
                      r"viaje\w*|discovery|nat ?geo|national geographic|animals?|animal planet|wild\w*|space|"
                      r"educa\w*|culture|universe|earth|planet\w*|smithsonian|curiosity|explor\w*"),
    ("estilo", r"food\w*|cook\w*|culinari\w*|cocina|gourmet|chef|home|casa|lifestyle|estilo|fashion|moda|beauty|"
               r"design|garden\w*|decor\w*|pets?|auto|cars?|motor|shop\w*|compras|travel channel|hgtv|tastemade|"
               r"wellness|fitness|yoga|saude|health|receitas?|outdoor|fishing|pesca|hunting|agro\w*|agricult\w*|rural"),
    ("variedades", r"entertainment|entretenimento|entretenimiento|general|geral|gerais|variety|variedades|comedy|"
                   r"comedia|reality|game ?shows?|talk ?shows?|family|familia|local|regional|community|"
                   r"comunitaria|universitaria|legend\w*|retro|classic tv|tv aberta"),
]
_WORD_RE = [(t, re.compile(r"(?<![a-z0-9])(?:" + w + r")(?![a-z0-9])")) for t, w in _WORDS]


def fold(text):
    t = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"[_|/;:\-]+", " ", t)


def theme_by_words(text):
    t = fold(text)
    for theme, rx in _WORD_RE:
        if rx.search(t):
            return theme
    return None


# ---------------------------------------------------------------- país
_COUNTRY_NAMES = {
    "brazil": "BR", "brasil": "BR", "portugal": "PT", "united states": "US", "usa": "US", "united kingdom": "GB",
    "uk": "GB", "mexico": "MX", "spain": "ES", "espana": "ES", "canada": "CA", "france": "FR", "germany": "DE",
    "italy": "IT", "india": "IN", "argentina": "AR", "chile": "CL", "colombia": "CO", "peru": "PE",
    "austria": "AT", "switzerland": "CH", "netherlands": "NL", "sweden": "SE", "denmark": "DK", "norway": "NO",
    "finland": "FI", "ireland": "IE", "australia": "AU", "new zealand": "NZ", "belgium": "BE", "korea": "KR",
    "japan": "JP", "poland": "PL", "luxembourg": "LU",
}
_ID_CC_RE = re.compile(r"\.([a-z]{2})(?:@|$)")

# filtros de país da tela Início: chave, rótulo, países (None = todos)
LATAM_ES = {"MX", "AR", "CL", "CO", "PE", "ES", "UY", "PY", "BO", "EC", "VE", "CR", "DO", "GT", "HN", "SV", "NI",
            "PA", "PR", "CU"}
REGIONS = [
    ("all", "Todos os países (Brasil primeiro)", None),
    ("br", "Só do Brasil", {"BR"}),
    ("pt", "Em português (Brasil e Portugal)", {"BR", "PT", "AO", "MZ", "CV"}),
    ("en", "Em inglês (EUA, Reino Unido…)", {"US", "GB", "CA", "AU", "IE", "NZ"}),
    ("es", "Em espanhol", LATAM_ES),
]
REGION_SET = {k: s for k, _, s in REGIONS}


def country_of(ch):
    if ch.country:
        return ch.country.upper()[:2]
    m = _ID_CC_RE.search((ch.tvg_id or "").lower())
    if m:
        return "GB" if m.group(1) == "uk" else m.group(1).upper()
    g = fold(ch.group).strip()
    if g in _COUNTRY_NAMES:
        return _COUNTRY_NAMES[g]
    return "BR" if is_brazilian(ch) else ""


def country_from_source(url):
    """País de uma lista pelo nome do arquivo: br.m3u, br_samsung.m3u, brlg.m3u, ssungbra.m3u…"""
    name = url.rsplit("/", 1)[-1].lower()
    m = re.match(r"([a-z]{2})(?:_[a-z]+)?\.m3u8?$", name) or re.match(r"([a-z]{2})lg\.m3u$", name)
    if m:
        cc = m.group(1).upper()
        return "GB" if cc == "UK" else cc
    if name.startswith("ssung") and len(name) >= 11:
        return {"bra": "BR", "mex": "MX", "aus": "AU", "usa": "US"}.get(name[5:8], "")
    if name.startswith("us") and name.endswith("lg.m3u"):
        return "US"
    return ""


# ---------------------------------------------------------------- biblioteca
class Entry:
    """Um canal na tela Início: o melhor link entre todas as listas, com tema e país."""
    __slots__ = ("ch", "key", "theme", "country", "pending")

    def __init__(self, ch, key, theme, country, pending):
        self.ch, self.key, self.theme, self.country, self.pending = ch, key, theme, country, pending


def load_ai_cache():
    try:
        data = json.loads(AI_FILE.read_text(encoding="utf-8"))
        return {k: v for k, v in data.items() if v in THEME_KEYS}
    except (OSError, ValueError):
        return {}


def save_ai_cache(data):
    try:
        tmp = AI_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        tmp.replace(AI_FILE)
    except OSError:
        pass


def build_library(files, score, ai_cache):
    """files: [(chave da categoria, caminho)]; score(url) -> 0..1 ou None.
    Devolve os canais sem repetir (mesmo nome = mesmo canal; fica o link mais estável)."""
    by_cat_theme = {}
    best, order = {}, []
    for cat, path in files:
        try:
            chans, _ = parse_m3u(path)
        except OSError:
            continue
        cat_theme, cat_cc = CATEGORY_LISTS.get(cat), CATEGORY_COUNTRY.get(cat)
        for c in chans:
            if c.kind:
                continue  # filmes e episódios de contas Xtream não entram nas fileiras de TV ao vivo
            k = name_key(c.name)
            if len(k) < 2:
                continue
            if cat_theme:
                by_cat_theme.setdefault(k, cat_theme)
            cc = cat_cc or country_of(c)
            s = score(c.url)
            rank = (0.5 if s is None else s) + (0.05 if c.logo else 0)
            old = best.get(k)
            if old is None:
                order.append(k)
                best[k] = [rank, c, c.group, cc]
            else:
                if not old[2] and c.group:
                    old[2] = c.group
                if not old[3] or cat_cc:
                    old[3] = cc or old[3]
                if rank > old[0]:
                    old[0], old[1] = rank, c
    out = []
    for k in order:
        _, c, group, cc = best[k]
        theme = theme_by_words(group) or by_cat_theme.get(k) or theme_by_words(c.name) or ai_cache.get(k)
        out.append(Entry(c, k, theme or "variedades", cc, theme is None))
    return out


# ---------------------------------------------------------------- IA
_AI_PROMPT = ("Classifique cada canal de TV em UM destes temas: " + ", ".join(THEME_KEYS) + ".\n"
              "Use o nome do canal (e o grupo, quando houver). Se não souber, use variedades.\n"
              "Regras: emissoras abertas e generalistas (Globo, SBT, Record, Band, RedeTV, TV Brasil, "
              "afiliadas e TVs regionais) são variedades; noticias só para canais de jornalismo (CNN, GloboNews, "
              "BandNews); canais para crianças (Rá-Tim-Bum, Gloob, Cartoon) são infantil; canais de um seriado "
              "só (MacGyver, Chaves) são series.\n"
              "Responda só uma linha por canal, no formato número=tema, sem explicações.\n\n")
_AI_LINE_RE = re.compile(r"(\d+)\s*[=:.)-]\s*([a-zA-Zçãéíóú]+)")
BATCH = 30


class ThemeAI(QThread):
    """Classifica em segundo plano, com o Gemma 3 da placa de vídeo, os canais que as regras não resolveram."""
    classified = pyqtSignal(dict)    # nome normalizado -> tema (um lote)

    def __init__(self, items, parent=None):
        super().__init__(parent)
        self.items = items           # [(chave, nome, grupo)]
        self._stop = threading.Event()

    def stop(self):
        self._stop.set()

    def run(self):
        from . import captions
        if not captions.llm_ready() or not captions.cuda_ready():
            return  # sem a IA ou sem o acelerador da placa de vídeo baixados: fica só com as regras
        try:
            captions._enable_cuda_dirs()
            import ctranslate2
            from tokenizers import Tokenizer
            tok = Tokenizer.from_file(str(captions.LLM_DIR / "tokenizer.json"))
            gen = None
            for compute in ("int8_bfloat16", "int8_float32"):
                try:
                    gen = ctranslate2.Generator(str(captions.LLM_DIR), device="cuda", compute_type=compute)
                    break
                except (ValueError, RuntimeError):
                    continue
            if gen is None:
                return
        except Exception as e:  # noqa: BLE001 — sem placa de vídeo ou sem a IA: fica só com as regras
            log.info("Temas por IA indisponíveis: %s", e)
            return
        log.info("Temas por IA: %d canais para classificar", len(self.items))
        done = 0
        for i in range(0, len(self.items), BATCH):
            if self._stop.is_set():
                break
            batch = self.items[i:i + BATCH]
            lines = [f"{n + 1}. {name}" + (f" (grupo: {group})" if group else "")
                     for n, (_, name, group) in enumerate(batch)]
            prompt = (f"<bos><start_of_turn>user\n{_AI_PROMPT}" + "\n".join(lines) +
                      "<end_of_turn>\n<start_of_turn>model\n")
            try:
                res = gen.generate_batch([tok.encode(prompt, add_special_tokens=False).tokens],
                                         max_length=12 * len(batch) + 20, sampling_topk=1,
                                         include_prompt_in_result=False, end_token=["<end_of_turn>", "<eos>"])
                text = tok.decode(res[0].sequences_ids[0], skip_special_tokens=True)
            except Exception as e:  # noqa: BLE001
                log.warning("Temas por IA: %s", e)
                break
            found = {}
            for n, theme in _AI_LINE_RE.findall(text):
                idx, theme = int(n) - 1, fold(theme).strip()
                if 0 <= idx < len(batch) and theme in THEME_KEYS:
                    found[batch[idx][0]] = theme
            for key, _, _ in batch:
                found.setdefault(key, "variedades")  # não sabe: não pergunta de novo
            done += len(batch)
            self.classified.emit(found)
        del gen
        log.info("Temas por IA: %d classificados", done)
