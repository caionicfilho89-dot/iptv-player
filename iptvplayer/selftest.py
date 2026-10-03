"""Autoteste: `IPTV Player.exe --autoteste` confere cada recurso e grava o resultado no registro de erros.

Serve para descobrir, no PC de quem usa, o que não está funcionando (bibliotecas que faltam no
programa empacotado, placa de vídeo, vozes, tradutor…). Recursos cujo modelo ainda não foi baixado
aparecem como "não baixado", sem baixar nada.
"""
import time

from .log import log


def _check(name, fn):
    t = time.monotonic()
    try:
        info = fn()
        log.info("AUTOTESTE ok      %-28s %s (%.1f s)", name, info or "", time.monotonic() - t)
        return True
    except Exception as e:  # noqa: BLE001
        log.error("AUTOTESTE FALHOU  %-28s %s: %s", name, type(e).__name__, e)
        return False


def run():
    import numpy as np

    from . import captions, dub, remote, timeshift
    from .vlcload import vlc
    results = []

    def vlc_ok():
        inst = vlc.Instance(["--quiet", "--vout=dummy", "--aout=dummy"])
        return f"VLC {vlc.libvlc_get_version().decode()}" if inst else "sem VLC"
    results.append(_check("VLC", vlc_ok))

    def ia_legendas():
        missing = captions.missing_deps()
        if missing:
            raise RuntimeError("faltam " + ", ".join(missing))
        import ctranslate2
        return f"CTranslate2 {ctranslate2.__version__}, placa NVIDIA: {captions.gpu_available()}, " \
               f"acelerador baixado: {captions.cuda_ready()}"
    results.append(_check("IA de legendas", ia_legendas))

    def modelos():
        have = [n for n in captions.MODELS if (captions.MODEL_DIR / n / "model.bin").exists()]
        return "baixados: " + (", ".join(have) or "nenhum")
    results.append(_check("Modelos de legenda", modelos))

    def tradutor_local():
        if not captions.nllb_ready():
            return "não baixado"
        return repr(captions.LocalTranslator("cpu").translate("Good evening and welcome.", "en"))
    results.append(_check("Tradutor sem internet", tradutor_local))

    def tradutor_ia():
        if not captions.llm_ready():
            return "não baixado"
        if not captions.gpu_available():
            return "sem placa NVIDIA"
        captions._enable_cuda_dirs()
        t = captions.LLMTranslator()
        return f"{t.compute}: " + repr(t.translate("The match was amazing and the fans kept singing.", "en",
                                                   ["Good evening, here are the sports news."]))
    results.append(_check("Tradutor IA (Gemma 3)", tradutor_ia))

    def vozes_windows():
        # numa tarefa própria, como na dublagem (o COM desta tarefa já foi iniciado por outra biblioteca)
        import threading
        out = []

        def work():
            import comtypes
            comtypes.CoInitialize()
            out.append(", ".join(dub.windows_voices()) or "nenhuma em português")
        t = threading.Thread(target=work)
        t.start()
        t.join(20)
        return out[0] if out else "sem resposta"
    results.append(_check("Vozes do Windows", vozes_windows))

    for key in dub.NEURAL:
        def voz(key=key):
            if not all(p.exists() for p, _ in dub._files(key)):
                return "não baixada"
            kind, name = key.split(":", 1)
            v = (dub._Kokoro if kind == "kokoro" else dub._Piper)(name)
            pcm = v.speak("Teste da dublagem.", 1.1)
            if not len(pcm) or float(np.abs(pcm).max()) < 0.01:
                raise RuntimeError("a voz saiu muda")
            return f"{len(pcm) / dub.OUT_RATE:.1f} s de fala"
        results.append(_check(f"Voz {key}", voz))

    def qr():
        from PyQt6.QtWidgets import QApplication
        app = QApplication.instance() or QApplication([])  # o QPixmap precisa de um QApplication
        assert app
        pm = remote.qr_pixmap("http://192.168.0.1:8765/?k=teste", 120)
        return f"QR {pm.width()}x{pm.height()}, IP da rede: {remote.lan_ip()}"
    results.append(_check("Controle pelo celular", qr))

    def buffer():
        ts = timeshift.Timeshift(1)
        port = ts.port
        ts.shutdown()
        return f"servidor local na porta {port}"
    results.append(_check("Pausar a TV (buffer)", buffer))

    log.info("AUTOTESTE terminou: %d de %d ok", sum(results), len(results))
    return all(results)
