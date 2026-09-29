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


def main():
    if BUILD.exists():
        shutil.rmtree(BUILD)
    run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--onedir", "--windowed",
         "--name", "IPTV Player", "--icon", str(ROOT / "docs/icon.ico"),
         "--distpath", str(BUILD), "--workpath", str(BUILD / "work"), "--specpath", str(BUILD / "work"),
         *[a for m in ("PyQt6.QtWebEngineCore", "PyQt6.QtQml", "PyQt6.QtQuick", "PyQt6.QtPdf",
                       "PyQt6.QtMultimedia", "PyQt6.Qt3DCore", "tkinter") for a in ("--exclude-module", m)],
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

    zip_base = BUILD / f"IPTV-Player-v{APP_VERSION}-Portatil"
    shutil.make_archive(str(zip_base), "zip", BUILD, "IPTV Player")
    print("ZIP:", f"{zip_base}.zip")

    iscc = next((p for p in ISCC if p.exists()), None)
    if iscc:
        run([str(iscc), f"/DAppVersion={APP_VERSION}", f"/DSourceDir={APP_DIR}", f"/DOutDir={BUILD}",
             str(ROOT / "packaging/installer.iss")])
    else:
        print("Inno Setup não encontrado: instalador não gerado.")


if __name__ == "__main__":
    main()
