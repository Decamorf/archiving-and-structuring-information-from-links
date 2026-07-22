; Сборка установщика: открыть этот файл в Inno Setup Compiler → Compile
; Получится Архиватор_setup.exe — обычный установщик с иконкой и удалением.
[Setup]
AppName=Архиватор ссылок
AppVersion=1.7.1
DefaultDirName={autopf}\Архиватор ссылок
DefaultGroupName=Архиватор ссылок
OutputBaseFilename=Архиватор_setup
Compression=lzma2
SolidCompression=yes
ArchitecturesInstallIn64BitMode=x64
WizardStyle=modern

[Languages]
Name: "ru"; MessagesFile: "compiler:Languages\Russian.isl"

[Files]
; папка, собранная PyInstaller (dist\Архиватор\*)
Source: "dist\Архиватор\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Архиватор ссылок"; Filename: "{app}\Архиватор.exe"
Name: "{userdesktop}\Архиватор ссылок"; Filename: "{app}\Архиватор.exe"; Tasks: desktopicon
Name: "{group}\Удалить Архиватор"; Filename: "{uninstallexe}"

[Tasks]
Name: "desktopicon"; Description: "Создать значок на рабочем столе"; GroupDescription: "Дополнительно:"

[Run]
Filename: "{app}\Архиватор.exe"; Description: "Запустить Архиватор"; Flags: nowait postinstall skipifsilent
