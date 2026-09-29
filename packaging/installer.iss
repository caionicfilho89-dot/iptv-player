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
AppName=IPTV Player
AppVersion={#AppVersion}
AppVerName=IPTV Player {#AppVersion}
AppPublisher=caionicfilho89-dot
AppPublisherURL=https://github.com/caionicfilho89-dot/iptv-player
AppSupportURL=https://github.com/caionicfilho89-dot/iptv-player/issues
AppUpdatesURL=https://github.com/caionicfilho89-dot/iptv-player/releases
DefaultDirName={localappdata}\Programs\IPTV Player
DefaultGroupName=IPTV Player
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

[Icons]
Name: "{group}\IPTV Player"; Filename: "{app}\IPTV Player.exe"
Name: "{group}\Desinstalar IPTV Player"; Filename: "{uninstallexe}"
Name: "{autodesktop}\IPTV Player"; Filename: "{app}\IPTV Player.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\IPTV Player.exe"; Description: "Abrir o IPTV Player agora"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: filesandordirs; Name: "{app}\.cache"
