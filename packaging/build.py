"""Gera a versão portátil (.zip) e o instalador (.exe) do IPTV Player.

Uso:  python packaging/build.py
Requer: pyinstaller, VLC 64 bits instalado e Inno Setup 6.
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from iptvplayer import APP_VERSION  # noqa: E402

BUILD = ROOT / "build"
APP_DIR = BUILD / "IPTV Player"
VLC_SRC = Path(r"C:\Program Files\VideoLAN\VLC")
# módulos do VLC que o app não usa (interface, visualizações, scripts…)
VLC_SKIP = {"gui", "lua", "visualization", "services_discovery", "control", "video_splitter",
            "meta_engine", "logger"}
ISCC = [Path(os.environ.get("LOCALAPPDATA", "")) / "Programs/Inno Setup 6/ISCC.exe",
        Path(r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe"), Path(r"C:\Program Files\Inno Setup 6\ISCC.exe")]


def run(cmd, **kw):
    print(">", " ".join(str(c) for c in cmd), flush=True)
    subprocess.run(cmd, check=True, **kw)


def make_version_file():
    """Gera o recurso de versão do Windows a partir do modelo, preenchendo a versão."""
    nums = (APP_VERSION.split("+")[0].split("-")[0].split(".") + ["0", "0", "0", "0"])[:4]
    ver_tuple = ", ".join(str(int(n)) for n in nums)
    template = (ROOT / "packaging/version_info.txt").read_text(encoding="utf-8")
    out = BUILD / "version_info_gen.txt"
    out.write_text(template.replace("__VER_TUPLE__", ver_tuple).replace("__VER_STR__", APP_VERSION),
                   encoding="utf-8")
    return out


# --- Assinatura de código (opcional) ---------------------------------------
# Para assinar, configure as variáveis de ambiente antes de rodar o build:
#   Certificado em token/SimplySign (Certum) ou já instalado no Windows:
#     set IPTV_SIGN_THUMBPRINT=<impressão digital do certificado>   (sem espaços)
#   OU arquivo .pfx/.p12:
#     set IPTV_SIGN_PFX=C:\caminho\cert.pfx
#     set IPTV_SIGN_PASSWORD=<senha do pfx>
#   Opcional: set IPTV_SIGN_TS=<servidor de carimbo de tempo>  (padrão: Certum)
# Sem nenhuma dessas variáveis, o build roda normalmente, sem assinar.
SIGN_TS = os.environ.get("IPTV_SIGN_TS", "http://time.certum.pl")


def find_signtool():
    """Localiza o signtool.exe mais recente do Windows SDK."""
    if shutil.which("signtool"):
        return "signtool"
    bases = [Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Windows Kits/10/bin",
             Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Windows Kits/10/bin"]
    cands = [p for b in bases if b.exists() for p in b.glob("*/x64/signtool.exe")]
    return str(sorted(cands)[-1]) if cands else None


def sign(path):
    """Assina um arquivo se um certificado estiver configurado; caso contrário, apenas avisa."""
    thumb = os.environ.get("IPTV_SIGN_THUMBPRINT")
    pfx = os.environ.get("IPTV_SIGN_PFX")
    if not thumb and not pfx:
        return  # assinatura não configurada: build segue sem assinar
    tool = find_signtool()
    if not tool:
        print("AVISO: signtool não encontrado (instale o Windows SDK). Arquivo NÃO assinado:", path)
        return
    cmd = [tool, "sign", "/fd", "sha256", "/tr", SIGN_TS, "/td", "sha256",
           "/d", "IPTV Player"]
    if thumb:
        cmd += ["/sha1", thumb]
    else:
        cmd += ["/f", pfx]
        if os.environ.get("IPTV_SIGN_PASSWORD"):
            cmd += ["/p", os.environ["IPTV_SIGN_PASSWORD"]]
    run(cmd + [str(path)])
    print("Assinado:", path)


def main():
    if BUILD.exists():
        shutil.rmtree(BUILD)
    BUILD.mkdir(parents=True)
    version_file = make_version_file()
    run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--onedir", "--windowed",
         "--name", "IPTV Player", "--icon", str(ROOT / "docs/icon.ico"),
         "--version-file", str(version_file),
         "--distpath", str(BUILD), "--workpath", str(BUILD / "work"), "--specpath", str(BUILD / "work"),
         *[a for m in ("PyQt6.QtWebEngineCore", "PyQt6.QtQml", "PyQt6.QtQuick", "PyQt6.QtPdf",
                       "PyQt6.QtMultimedia", "PyQt6.Qt3DCore", "tkinter") for a in ("--exclude-module", m)],
         # legendas por IA: DLLs do CTranslate2, modelo VAD do faster-whisper e cabeçalho COM do soundcard
         "--collect-binaries", "ctranslate2", "--collect-data", "faster_whisper", "--collect-data", "soundcard",
         # bibliotecas pesadas que só entrariam por importações opcionais (conversores de modelos etc.)
         *[a for m in ("torch", "torchvision", "torchaudio", "transformers", "tensorflow", "scipy", "pandas",
                       "sklearn", "matplotlib", "PIL", "botocore", "boto3", "grpc", "sympy", "IPython",
                       "ctranslate2.converters") for a in ("--exclude-module", m)],
         str(ROOT / "iptv_player.py")])

    for f in ROOT.glob("*.m3u"):
        shutil.copy2(f, APP_DIR)
    shutil.copy2(ROOT / "LICENSE", APP_DIR)
    shutil.copy2(ROOT / "packaging/LEIA-ME.txt", APP_DIR)

    vlc_dst = APP_DIR / "vlc"
    vlc_dst.mkdir()
    for name in ("libvlc.dll", "libvlccore.dll", "COPYING.txt"):
        shutil.copy2(VLC_SRC / name, vlc_dst)
    for d in (VLC_SRC / "plugins").iterdir():
        if d.is_dir() and d.name not in VLC_SKIP:
            shutil.copytree(d, vlc_dst / "plugins" / d.name)
    cache_gen = VLC_SRC / "vlc-cache-gen.exe"
    if cache_gen.exists():  # índice dos plugins: o VLC abre mais rápido
        run([str(cache_gen), str(vlc_dst / "plugins")])

    # assina o executável do app antes de empacotar (zip e instalador levam o exe assinado)
    sign(APP_DIR / "IPTV Player.exe")

    zip_base = BUILD / f"IPTV-Player-v{APP_VERSION}-Portatil"
    shutil.make_archive(str(zip_base), "zip", BUILD, "IPTV Player")
    print("ZIP:", f"{zip_base}.zip")

    iscc = next((p for p in ISCC if p.exists()), None)
    if iscc:
        run([str(iscc), f"/DAppVersion={APP_VERSION}", f"/DSourceDir={APP_DIR}", f"/DOutDir={BUILD}",
             str(ROOT / "packaging/installer.iss")])
        sign(BUILD / f"IPTV-Player-Setup-v{APP_VERSION}.exe")  # assina o instalador final
    else:
        print("Inno Setup não encontrado: instalador não gerado.")


if __name__ == "__main__":
    main()
