#define MyAppName "SuperMart POS"
#define MyAppVersion "3.2.0"
#define MyAppPublisher "Cloud Crafters"
#define MyAppExeName "SuperMartPOS.exe"
#define MyPrinterManagerExeName "SuperMartPrinterManager.exe"
#define MyAppURL "https://cloudcrafters.example"
#define MyAppSupportEmail "support@cloudcrafters.example"
#define MyAppDistRoot "dist"
#define MyAppOneDirFolder "SuperMartPOS"
#define MyInstallerBaseName "SuperMartPOS_Setup_v3.2.0"
#define MyAppId "{{A8AB1C36-4C3F-4E7A-8A5E-4EEA0D4D1B33}}"

#if FileExists(MyAppDistRoot + "\\" + MyAppExeName)
  #define MyBuildSourceMode "onefile"
#elif DirExists(MyAppDistRoot + "\\" + MyAppOneDirFolder)
  #define MyBuildSourceMode "onedir"
#else
  #error "PyInstaller output not found. Build the app first so dist\\SuperMartPOS.exe or dist\\SuperMartPOS\\ exists before compiling this installer."
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
VersionInfoVersion=3.2.0.0
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
Name: "{localappdata}\SuperMart POS"; Permissions: users-modify

[Files]
#if MyBuildSourceMode == "onefile"
Source: "{#MyAppDistRoot}\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#MyAppDistRoot}\{#MyPrinterManagerExeName}"; DestDir: "{app}"; Flags: ignoreversion skipifsourcedoesntexist
#else
Source: "{#MyAppDistRoot}\{#MyAppOneDirFolder}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs; Excludes: "*.sql,*.bat,*.md,.env,.env.*,docs\*,tests\*,reset_admin_password.py"
#endif

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{localappdata}\SuperMart POS"
Name: "{autoprograms}\SuperMart Printer Manager"; Filename: "{app}\{#MyPrinterManagerExeName}"; WorkingDir: "{localappdata}\SuperMart POS"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{localappdata}\SuperMart POS"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName}"; Flags: nowait postinstall skipifsilent; Tasks: launchapp

[UninstallDelete]
Type: filesandordirs; Name: "{app}"

[Code]
procedure InitializeWizard();
begin
  MsgBox(
    '{#MyAppName} stores your business data in ' + ExpandConstant('{localappdata}\SuperMart POS') + #13#10 +
    'Data is preserved by default when uninstalling so backup/restore remains safe after reinstall.',
    mbInformation,
    MB_OK
  );
end;
