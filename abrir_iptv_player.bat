@echo off
cd /d "%~dp0"
python -c "import PyQt6, vlc" 2>/dev/null || (
    echo Instalando dependencias pela primeira vez...
    python -m pip install -r requirements.txt
)
start "" pythonw "%~dp0iptv_player.py"
