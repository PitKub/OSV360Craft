; OSV360Craft installer (Inno Setup 6). build.bat compiles it after building dist\.
;   ISCC.exe /DAppVersion=1.0.0 installer\OSV360Craft.iss
;
; Express setup (the default) installs OSV360Craft with the bundled OpenOSV stitching
; engine and FFmpeg, so it works straight away. Custom setup lets you leave either out
; and use your own copies (set their paths in OSV360Craft > Settings).
;
; Like ProxyCraft, it asks whether to install for the current user (no administrator
; rights, default folder %LOCALAPPDATA%\Programs\OSV360Craft) or for all users
; (C:\Program Files\OSV360Craft), and lets the user choose another folder.

#ifndef AppVersion
  #define AppVersion "1.0.0"
#endif

[Setup]
; Never change AppId: Windows uses it to recognise OSV360Craft for upgrades and uninstalling.
AppId={{6E0D3658-E7D2-4388-BEE5-006FFFA192EA}
AppName=OSV360Craft
AppVersion={#AppVersion}
AppVerName=OSV360Craft {#AppVersion}
AppPublisher=Piotr Kubicki
AppPublisherURL=https://github.com/PitKub/OSV360Craft
AppSupportURL=https://github.com/PitKub/OSV360Craft/issues
AppUpdatesURL=https://github.com/PitKub/OSV360Craft/releases
AppCopyright=Copyright (c) 2026 Piotr Kubicki. MIT License.
VersionInfoVersion={#AppVersion}.0
DefaultDirName={autopf}\OSV360Craft
DefaultGroupName=OSV360Craft
DisableProgramGroupPage=yes
DisableDirPage=no
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
LicenseFile=..\LICENSE
OutputDir=..\dist
OutputBaseFilename=OSV360Craft-{#AppVersion}-Setup
SetupIconFile=..\assets\OSV360Craft.ico
UninstallDisplayIcon={app}\OSV360Craft.exe
UninstallDisplayName=OSV360Craft {#AppVersion}
WizardStyle=modern
Compression=lzma2/ultra64
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
ShowLanguageDialog=auto
ChangesAssociations=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "polish"; MessagesFile: "compiler:Languages\Polish.isl"

[Types]
Name: "express"; Description: "Express setup (recommended) - everything needed to stitch"
Name: "custom"; Description: "Custom setup"; Flags: iscustom

[Components]
Name: "app"; Description: "OSV360Craft"; Types: express custom; Flags: fixed
Name: "openosv"; Description: "OpenOSV 0.6.4 stitching engine (Apache-2.0)"; Types: express
Name: "ffmpeg"; Description: "FFmpeg video encoder and ffprobe (GPL-3.0)"; Types: express

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "..\dist\OSV360Craft.exe"; DestDir: "{app}"; Components: app; Flags: ignoreversion
Source: "..\dist\README.md"; DestDir: "{app}"; Components: app; Flags: ignoreversion
Source: "..\dist\USER_GUIDE.md"; DestDir: "{app}"; Components: app; Flags: ignoreversion
Source: "..\dist\LICENSE.txt"; DestDir: "{app}"; Components: app; Flags: ignoreversion
Source: "..\dist\THIRD_PARTY_NOTICES.md"; DestDir: "{app}"; Components: app; Flags: ignoreversion
Source: "..\dist\licenses\*"; DestDir: "{app}\licenses"; Components: app; Flags: ignoreversion
Source: "..\dist\external\OpenOSV\*"; DestDir: "{app}\external\OpenOSV"; Components: openosv; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\dist\external\ffmpeg\*"; DestDir: "{app}\external\ffmpeg"; Components: ffmpeg; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\OSV360Craft"; Filename: "{app}\OSV360Craft.exe"
Name: "{autodesktop}\OSV360Craft"; Filename: "{app}\OSV360Craft.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\OSV360Craft.exe"; Description: "{cm:LaunchProgram,OSV360Craft}"; Flags: nowait postinstall skipifsilent

[Code]
{ OpenOSV's osvtool.exe needs the Microsoft Visual C++ 2015-2022 Redistributable (x64).
  Setup only checks for it and offers to open Microsoft's download page; it never
  downloads or runs anything by itself. }
function VCRedistInstalled(): Boolean;
var
  Installed: Cardinal;
begin
  Result := RegQueryDWordValue(HKLM64, 'SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64', 'Installed', Installed)
            and (Installed = 1);
  if not Result then
    Result := FileExists(ExpandConstant('{sys}\vcruntime140.dll')) and FileExists(ExpandConstant('{sys}\msvcp140.dll'));
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  ErrorCode: Integer;
begin
  if (CurStep = ssPostInstall) and WizardIsComponentSelected('openosv') and (not VCRedistInstalled()) and (not WizardSilent()) then
  begin
    if MsgBox('The OpenOSV engine needs the Microsoft Visual C++ 2015-2022 Redistributable (x64), which was not found.' + #13#10 + #13#10 +
              'Open Microsoft''s download page now? (You can also install it later; OSV360Craft''s Hardware Analyzer shows whether it is present.)',
              mbConfirmation, MB_YESNO) = IDYES then
      ShellExec('open', 'https://aka.ms/vs/17/release/vc_redist.x64.exe', '', '', SW_SHOWNORMAL, ewNoWait, ErrorCode);
  end;
end;
