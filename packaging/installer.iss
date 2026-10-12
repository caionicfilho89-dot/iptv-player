; Instalador do IPTV Player (Inno Setup 6). Gerado por packaging/build.py.
#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#ifndef SourceDir
  #define SourceDir "..\build\IPTV Player"
#endif
#ifndef OutDir
  #define OutDir "..\build"
#endif

[Setup]
AppId={{6F4B3E2A-9C1D-4B7E-A2F0-3D5C8E9B1A77}
AppName=PudimFlix
AppVersion={#AppVersion}
AppVerName=PudimFlix {#AppVersion}
AppPublisher=caionicfilho89-dot
AppPublisherURL=https://github.com/caionicfilho89-dot/iptv-player
AppSupportURL=https://github.com/caionicfilho89-dot/iptv-player/issues
AppUpdatesURL=https://github.com/caionicfilho89-dot/iptv-player/releases
DefaultDirName={localappdata}\Programs\IPTV Player
DefaultGroupName=PudimFlix
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir={#OutDir}
OutputBaseFilename=IPTV-Player-Setup-v{#AppVersion}
SetupIconFile=..\docs\icon.ico
UninstallDisplayIcon={app}\IPTV Player.exe
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
LicenseFile=..\LICENSE

[Languages]
Name: "ptbr"; MessagesFile: "compiler:Languages\BrazilianPortuguese.isl"

[Tasks]
Name: "desktopicon"; Description: "Criar atalho na área de trabalho"; GroupDescription: "Atalhos:"

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[InstallDelete]
; backend OpenSSL do Qt de versões até a 1.8.0: feito para outro OpenSSL, derrubava o programa (ver app.use_windows_tls)
Type: files; Name: "{app}\_internal\PyQt6\Qt6\plugins\tls\qopensslbackend.dll"
; atalhos com o nome antigo (o programa virou PudimFlix na 1.12.0)
Type: files; Name: "{userprograms}\IPTV Player\IPTV Player.lnk"
Type: files; Name: "{userprograms}\IPTV Player\Desinstalar IPTV Player.lnk"
Type: dirifempty; Name: "{userprograms}\IPTV Player"
Type: files; Name: "{autodesktop}\IPTV Player.lnk"

[Icons]
Name: "{group}\PudimFlix"; Filename: "{app}\IPTV Player.exe"
Name: "{group}\Desinstalar PudimFlix"; Filename: "{uninstallexe}"
Name: "{autodesktop}\PudimFlix"; Filename: "{app}\IPTV Player.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\IPTV Player.exe"; Description: "Abrir o PudimFlix agora"; Flags: nowait postinstall skipifsilent
; atualização com um clique (o app roda o instalador com /SILENT /RELAUNCH=1): reabre sozinho no fim
Filename: "{app}\IPTV Player.exe"; Flags: nowait; Check: Relaunch

[UninstallDelete]
Type: filesandordirs; Name: "{app}\.cache"

[Code]
function Relaunch: Boolean;
begin
  Result := ExpandConstant('{param:RELAUNCH|0}') = '1';
end;
