; Inno Setup 스크립트: 윈도우 설치 파일 (PyInstaller가 만든 "PDF 번역기" 폴더를 설치)
; 실행: iscc /DAppVersion=0.2.0 packaging\installer.iss
#ifndef AppVersion
  #define AppVersion "0.2.0"
#endif
#define AppName "PDF 번역기"

[Setup]
AppId={{558F904D-A750-4431-9106-6B9CCDD2FA2C}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=galaxygamja
DefaultDirName={localappdata}\Programs\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\build\installer
OutputBaseFilename=PDF-Translator-Setup-{#AppVersion}
SetupIconFile=..\build\icon.ico
UninstallDisplayIcon={app}\{#AppName}.exe
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Tasks]
Name: "desktopicon"; Description: "바탕 화면에 바로 가기 만들기"; Flags: unchecked

[Files]
Source: "..\build\dist\{#AppName}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppName}.exe"
Name: "{userdesktop}\{#AppName}"; Filename: "{app}\{#AppName}.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppName}.exe"; Description: "{#AppName} 실행"; Flags: nowait postinstall skipifsilent
