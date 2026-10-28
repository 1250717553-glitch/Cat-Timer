; ==========================================================
;  桌面宠物计时器 · Inno Setup 安装包脚本
; ==========================================================
;  用法：
;    1. 先跑 build.bat 生成 dist\桌面宠物计时器\（免安装版）
;    2. 用 Inno Setup Compiler 打开本文件，按 F9 编译
;    3. 产物：dist\Setup.exe
;
;  说明：本文件以 UTF-8 (BOM) 保存，Inno Setup 6 才能正确读取中文路径。
;        如果编译时报「中文乱码」或找不到 dist 目录，请确认用记事本
;        「另存为 → 编码：UTF-8」重新保存一次。
;
;  关于中文语言包（重要）：
;    Inno Setup 自带语言包里**没有简体中文**（只有 29 种，含日韩但没有中文），
;    所以不能写 "compiler:Languages\ChineseSimplified.isl" —— 那会直接编译报错。
;    本脚本改为引用项目自带的 installer_lang\ChineseSimplified.isl
;    （取自官方仓库 jrsoftware/issrc 的 Files/Languages/，LanguageID=$0804）。
;    如果你已经在 Inno Setup 安装目录的 Languages\ 下放了该文件，
;    也可以把下面 [Languages] 段换成：
;        MessagesFile: "compiler:Languages\ChineseSimplified.isl"
; ==========================================================

#define MyAppName "桌面宠物计时器"
#define MyAppVersion "1.0.0"
#define MyAppPublisher "桌面宠物计时器"
#define MyAppExeName "桌面宠物计时器.exe"
#define MyDistDir "dist\桌面宠物计时器"

[Setup]
; AppId 是应用的唯一标识，升级安装时靠它识别同一个应用。
; 注意：一旦发布就不要再改，改了会被当成另一个程序。
AppId={{8F3C1A42-5D7B-4E96-9C21-7A4E0B6D2F31}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
; {autopf} = Program Files（配合下面的 x64compatible 模式）
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
AllowNoIcons=yes
; 装到 Program Files 需要管理员权限（UAC 提示属正常现象）
PrivilegesRequired=admin
ArchitecturesInstallIn64BitMode=x64compatible
; 输出到 dist\Setup.exe
OutputDir=dist
OutputBaseFilename=Setup
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
; 安装前自动关闭正在运行的实例，避免「文件被占用」而装不上
CloseApplications=yes
RestartApplications=no
UninstallDisplayName={#MyAppName}
; 有图标时取消下一行注释（需要 assets\app.ico 存在）
; SetupIconFile=assets\app.ico

[Languages]
Name: "chinesesimp"; MessagesFile: "installer_lang\ChineseSimplified.isl"

[Tasks]
; 桌面快捷方式做成可选项，默认不勾选（避免污染用户桌面）
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加任务："; Flags: unchecked

[Files]
; 把免安装版整个目录装进去（含 _internal 子目录，必须递归）
Source: "{#MyDistDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
; 开始菜单
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\卸载 {#MyAppName}"; Filename: "{uninstallexe}"
; 桌面（仅在勾选了 desktopicon 任务时创建）
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
; 装完后可直接启动
Filename: "{app}\{#MyAppExeName}"; Description: "立即运行 {#MyAppName}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; 只删程序目录。用户的配置数据在 %AppData%\桌面宠物计时器\，卸载时保留，
; 这样重装后事件列表还在（需要彻底清除请手动删该目录）。
Type: filesandordirs; Name: "{app}"
