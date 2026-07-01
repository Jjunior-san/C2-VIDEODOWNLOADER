; IMPORTANTE:
; Nao compile este arquivo diretamente antes de gerar o executavel.
; Use COMPILAR_INSTALADOR.cmd ou build_installer.ps1 na raiz do projeto.
; O arquivo dist\C2VideoDownloader.exe e os componentes runtime_seed sao gerados antes desta etapa.

#define MyAppName "C² - Video Downloader"
#ifndef MyAppVersion
#define MyAppVersion "1.2.0"
#endif
#define MyAppPublisher "C2 Sistemas"
#define MyAppURL "https://github.com/Jjunior-san/C2-VIDEODOWNLOADER"
#define MyAppExeName "C2VideoDownloader.exe"

[Setup]
AppId={{B8E9AE44-7D69-4B89-A3A5-C847A10A0C22}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}/issues
AppUpdatesURL={#MyAppURL}/releases/latest
DefaultDirName={autopf}\C2 Sistemas\C2 Video Downloader
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir=installer
OutputBaseFilename=C2VideoDownloaderSetup
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
SetupIconFile=assets\c2.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=yes
RestartApplications=no
AppMutex=C2VideoDownloaderMutex
ChangesAssociations=no
UsePreviousAppDir=yes
VersionInfoVersion={#MyAppVersion}
VersionInfoCompany={#MyAppPublisher}
VersionInfoDescription={#MyAppName}
VersionInfoProductName={#MyAppName}
VersionInfoProductVersion={#MyAppVersion}

[Languages]
Name: "brazilianportuguese"; MessagesFile: "compiler:Languages\BrazilianPortuguese.isl"

[Tasks]
Name: "desktopicon"; Description: "Criar atalho na área de trabalho"; GroupDescription: "Atalhos:"; Flags: unchecked

[Files]
Source: "dist\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion restartreplace
Source: "runtime_seed\yt-dlp.exe"; DestDir: "{app}\runtime"; Flags: ignoreversion restartreplace
Source: "runtime_seed\deno.exe"; DestDir: "{app}\runtime"; Flags: ignoreversion restartreplace

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; Tasks: desktopicon

[Registry]
Root: HKLM; Subkey: "Software\Microsoft\Windows\CurrentVersion\App Paths\{#MyAppExeName}"; ValueType: string; ValueName: ""; ValueData: "{app}\{#MyAppExeName}"; Flags: uninsdeletekey
Root: HKLM; Subkey: "Software\Microsoft\Windows\CurrentVersion\App Paths\{#MyAppExeName}"; ValueType: string; ValueName: "Path"; ValueData: "{app}"; Flags: uninsdeletekey

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Abrir {#MyAppName}"; Flags: nowait postinstall skipifsilent
