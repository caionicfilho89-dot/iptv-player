@echo off
echo Escolha uma opção para assistir:
echo.
echo [1] Todos os canais (Melhor qualidade)
echo [2] Filmes
echo [3] Séries
echo [4] Esportes
echo [5] Notícias
echo [6] Documentários
echo [7] Música
echo [8] Infantil
echo [9] Canais em Português
echo [0] Sair
echo.
set /p opcao="Digite o número da opção desejada: "

if "%opcao%"=="1" (
    start "" "C:\Program Files\VideoLAN\VLC\vlc.exe" "%~dp0melhor_iptv.m3u"
) else if "%opcao%"=="2" (
    start "" "C:\Program Files\VideoLAN\VLC\vlc.exe" "%~dp0filmes.m3u"
) else if "%opcao%"=="3" (
    start "" "C:\Program Files\VideoLAN\VLC\vlc.exe" "%~dp0series.m3u"
) else if "%opcao%"=="4" (
    start "" "C:\Program Files\VideoLAN\VLC\vlc.exe" "%~dp0esportes.m3u"
) else if "%opcao%"=="5" (
    start "" "C:\Program Files\VideoLAN\VLC\vlc.exe" "%~dp0notícias.m3u"
) else if "%opcao%"=="6" (
    start "" "C:\Program Files\VideoLAN\VLC\vlc.exe" "%~dp0documentários.m3u"
) else if "%opcao%"=="7" (
    start "" "C:\Program Files\VideoLAN\VLC\vlc.exe" "%~dp0música.m3u"
) else if "%opcao%"=="8" (
    start "" "C:\Program Files\VideoLAN\VLC\vlc.exe" "%~dp0infantil.m3u"
) else if "%opcao%"=="9" (
    start "" "C:\Program Files\VideoLAN\VLC\vlc.exe" "%~dp0português.m3u"
) else if "%opcao%"=="0" (
    exit
) else (
    echo Opção inválida!
    timeout /t 2 >nul
    cls
    call %0
)

exit
