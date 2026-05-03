#define MyAppName "Garage Management System"
#define MyAppVersion "3.3.0"
#define MyAppPublisher "Cloud Crafters"
#define MyAppExeName "GarageManagementSystem.exe"
#define MyPrinterManagerExeName "SuperMartPrinterManager.exe"
#define MyAppURL "https://cloudcrafters.example"
#define MyAppSupportEmail "support@cloudcrafters.example"
#define MyAppDistRoot "dist"
#define MyAppOneDirFolder "GarageManagementSystem"
#define MyInstallerBaseName "GarageManagementSystem_Setup_v3.3.0"
#define MyAppId "{{A8AB1C36-4C3F-4E7A-8A5E-4EEA0D4D1B33}}"

#if FileExists(MyAppDistRoot + "\\" + MyAppExeName)
  #define MyBuildSourceMode "onefile"
#elif DirExists(MyAppDistRoot + "\\" + MyAppOneDirFolder)
  #define MyBuildSourceMode "onedir"
#else
  #error "PyInstaller output not found. Build the app first so dist\\GarageManagementSystem.exe or dist\\GarageManagementSystem\\ exists before compiling this installer."
#endif

[Setup]
AppId={#MyAppId}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}
AppContact={#MyAppSupportEmail}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
AllowNoIcons=yes
PrivilegesRequired=admin
PrivilegesRequiredOverridesAllowed=dialog
DisableProgramGroupPage=yes
WizardStyle=modern
Compression=lzma
SolidCompression=yes
SetupIconFile=static\icons\icon.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
OutputDir=release
OutputBaseFilename={#MyInstallerBaseName}
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
VersionInfoVersion=3.3.0.0
VersionInfoProductName={#MyAppName}
VersionInfoDescription={#MyAppName} Setup
VersionInfoCompany={#MyAppPublisher}
VersionInfoCopyright=Copyright (C) 2026 {#MyAppPublisher}
VersionInfoTextVersion={#MyAppVersion}
ChangesEnvironment=no
MinVersion=10.0
CloseApplications=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional shortcuts:"
Name: "launchapp"; Description: "Launch {#MyAppName}"; GroupDescription: "After installation:"; Flags: checkedonce

[Dirs]
Name: "{localappdata}\Garage Management System"; Permissions: users-modify

[Files]
#if MyBuildSourceMode == "onefile"
Source: "{#MyAppDistRoot}\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#MyAppDistRoot}\{#MyPrinterManagerExeName}"; DestDir: "{app}"; Flags: ignoreversion skipifsourcedoesntexist
#else
; CRITICAL: supermart.db is explicitly excluded — customer databases must NEVER
; be overwritten by the installer.  All persistent data lives in AppData (see
; [Dirs] above).  The only DB that may ship is the optional empty seed DB below.
Source: "{#MyAppDistRoot}\{#MyAppOneDirFolder}\*"; DestDir: "{app}"; \
  Flags: ignoreversion recursesubdirs createallsubdirs; \
  Excludes: "*.sql,*.bat,*.md,.env,.env.*,docs\*,tests\*,reset_admin_password.py,supermart.db"
#endif

; Optional empty seed database — shipped inside _internal\seed\ so it is
; never confused with a live customer DB.  The application copies it to AppData
; only on first run when no DB already exists there.
Source: "{#MyAppDistRoot}\{#MyAppOneDirFolder}\_internal\seed\supermart_seed.db"; \
  DestDir: "{app}\_internal\seed"; \
  Flags: ignoreversion skipifsourcedoesntexist

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{localappdata}\Garage Management System"
Name: "{autoprograms}\Garage Printer Manager"; Filename: "{app}\{#MyPrinterManagerExeName}"; WorkingDir: "{localappdata}\Garage Management System"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{localappdata}\Garage Management System"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName}"; Flags: nowait postinstall skipifsilent; Tasks: launchapp

[UninstallDelete]
; Only the application install folder is removed on uninstall.
; Customer data in {localappdata}\Garage Management System is NEVER deleted automatically.
Type: filesandordirs; Name: "{app}"

[Code]
procedure InitializeWizard();
begin
  MsgBox(
    '{#MyAppName} stores your business data in:' + #13#10 +
    ExpandConstant('{localappdata}\Garage Management System') + #13#10#13#10 +
    'This folder is NOT deleted during uninstall or reinstall.' + #13#10 +
    'Your database, activation key, settings and backups are always preserved.',
    mbInformation,
    MB_OK
  );
end;

function InitializeUninstall(): Boolean;
begin
  MsgBox(
    'Uninstalling {#MyAppName}.' + #13#10#13#10 +
    'Your business data in:' + #13#10 +
    ExpandConstant('{localappdata}\Garage Management System') + #13#10 +
    'will NOT be deleted.' + #13#10#13#10 +
    'You may safely reinstall at any time and all data will be restored.',
    mbInformation,
    MB_OK
  );
  Result := True;
end;
