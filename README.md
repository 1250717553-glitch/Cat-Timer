# 桌面宠物计时器

[English](README.en.md) | 简体中文

一只橘猫陪在桌面上，帮你盯着喝水、久坐和重要事项。

![主界面](docs/screenshots/main-expanded.png)

## 功能

- **循环提醒** — 每 N 分钟 + 每天 N 次 + 跨天自动重置 + 工作时间段（如 09:00–18:00）
- **三种场景** — 喝水、久坐运动、一次性重要事项
- **桌宠交互** — 呼吸、点击弹跳、歪头跟随、点猫折叠卡片
- **全屏时自动隐藏** — 三档可见范围（仅桌面 / 窗口可见 / 始终可见）
- **到点提示音** — 无 WAV 素材时自动回落到系统提示音

## 截图

| 展开 | 折叠 |
|---|---|
| ![展开](docs/screenshots/main-expanded.png) | ![折叠](docs/screenshots/main-collapsed.png) |

| 管理窗口 | 托盘菜单 |
|---|---|
| ![管理](docs/screenshots/manager.png) | ![托盘](docs/screenshots/tray-menu.png) |

## 安装

下载 `Setup.exe` 双击安装，**无需管理员权限** —— 装在用户目录，不写系统级注册表。

## 开发

```bat
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
启动.bat
```

改完代码后双击 `启动.bat` 看效果。它用 `pythonw` 启动，没有控制台窗口；如果程序崩溃，错误信息会写进 `启动错误.log`。

## 技术栈

Python 3.12 · PySide6 · PyInstaller · Inno Setup

## 许可

[MIT](LICENSE)
