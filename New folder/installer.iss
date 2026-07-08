
Action: file_editor create /app/installer.iss --file-text "; ============================================================
; Inno Setup script for YT-DLP Downloader
; ============================================================
; Requires Inno Setup (free): https://jrsoftware.org/isdl.php
;
; Before compiling this, first run build_exe.bat so that
; dist\YTDLPDownloader\YTDLPDownloader.exe (and its supporting files)
; already exist -- this script just packages that folder into a
; proper installer.
;
; To compile:
;   1) Open this file in Inno Setup (double-click it), OR
;   2) From command line: \"C:\Program Files (x86)\Inno Setup 6\ISCC.exe\" installer.iss
;
; Output: Output\YTDLPDownloader-Setup.exe
; ============================================================

#define MyAppName \"YT-DLP Downloader\"
#define MyAppVersion \"1.1.0\"
#define MyAppPublisher \"Akash\"
#define MyAppExeName \"YTDLPDownloader.exe\"
#define MySourceDir \"dist\YTDLPDownloader\"

[Setup]
AppId={{8F2C6E6E-6B7A-4E9A-9C4B-9C2F6D3B7A11}}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
; Installs to C:\Program Files\YTDLPDownloader (64-bit) by default
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir=Output
OutputBaseFilename=YTDLPDownloader-Setup
Compression=lzma2
SolidCompression=yes
; Comment out the next line if you don't have icon.ico, or point it
; at the same icon.ico used in build_exe.bat
SetupIconFile=icon.ico
WizardStyle=modern
; Ask for admin rights so it can write to Program Files
PrivilegesRequired=admin
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={app}\{#MyAppExeName}

[Languages]
Name: \"english\"; MessagesFile: \"compiler:Default.isl\"

[Tasks]
; Lets the user opt in/out of a desktop icon during install
Name: \"desktopicon\"; Description: \"Create a &desktop shortcut\"; GroupDescription: \"Additional shortcuts:\"; Flags: unchecked

[Files]
; Pulls in EVERYTHING from the onedir build folder (exe, bundled python
; runtime, yt_dlp code, ffmpeg\ subfolder, etc.) -- recursesubdirs makes
; sure ffmpeg\ffmpeg.exe / ffprobe.exe come along too.
Source: \"{#MySourceDir}\*\"; DestDir: \"{app}\"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
; Start Menu shortcut -> also makes the app show up in Windows Search
Name: \"{group}\{#MyAppName}\"; Filename: \"{app}\{#MyAppExeName}\"
Name: \"{group}\Uninstall {#MyAppName}\"; Filename: \"{uninstallexe}\"
; Desktop shortcut (only if the user ticked the task above)
Name: \"{autodesktop}\{#MyAppName}\"; Filename: \"{app}\{#MyAppExeName}\"; Tasks: desktopicon

[Run]
; Optional \"Launch app now\" checkbox at the end of setup
Filename: \"{app}\{#MyAppExeName}\"; Description: \"Launch {#MyAppName}\"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Cleans up any log/temp files the app may have created inside its own folder
Type: filesandordirs; Name: \"{app}\"
"
Observation: Create successful: /app/installer.iss