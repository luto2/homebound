; Inno Setup script: per-user installer for the PyInstaller folder dist\Homebound.
; Build (from the project root, after PyInstaller):
;     iscc /DAppVersion=0.1.0 packaging\windows\homebound.iss
; Installs to %LOCALAPPDATA%\Programs\Homebound without admin rights. Maps, settings and backups live in
; %APPDATA%\Homebound and stay when Homebound is uninstalled.

#ifndef AppVersion
  #error Pass the version: iscc /DAppVersion=x.y.z
#endif

[Setup]
AppId={{6F1D3C2E-8B4A-4C7E-9A51-2D7E0B3F4A86}
AppName=Homebound
AppVersion={#AppVersion}
AppVerName=Homebound {#AppVersion}
AppPublisher=luto2
AppPublisherURL=https://github.com/luto2/homebound
AppSupportURL=https://github.com/luto2/homebound/issues
DefaultDirName={localappdata}\Programs\Homebound
DisableProgramGroupPage=yes
DisableDirPage=auto
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
LicenseFile=..\..\LICENSE
SetupIconFile=..\icons\homebound.ico
UninstallDisplayIcon={app}\Homebound.exe
UninstallDisplayName=Homebound
OutputDir=..\..\dist
OutputBaseFilename=Homebound-{#AppVersion}-windows-setup
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; Flags: unchecked
Name: "autostart"; Description: "Start Homebound when I &sign in"; Flags: unchecked

[InstallDelete]
; files of the previous version (PyInstaller's folder changes between versions)
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "..\..\dist\Homebound\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\Homebound"; Filename: "{app}\Homebound.exe"; AppUserModelID: "Homebound.Homebound"
Name: "{autodesktop}\Homebound"; Filename: "{app}\Homebound.exe"; AppUserModelID: "Homebound.Homebound"; Tasks: desktopicon

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "Homebound"; ValueData: """{app}\Homebound.exe"""; Flags: uninsdeletevalue; Tasks: autostart

[Run]
Filename: "{app}\Homebound.exe"; Description: "Start Homebound"; Flags: nowait postinstall skipifsilent
