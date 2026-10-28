"""退出冒烟检查：真实启动完整应用，验证「退出」这条路径确实能让进程结束。

为什么不能放进 tests/：
    1. tests/ 里全是纯逻辑测试，要求**不 import PySide6**、能在裸 Python 下跑；
       而这里必须真实创建 QApplication、真实跑 app.exec() 事件循环。
    2. 更关键的是：离屏（offscreen）无头测试**永远不会调用 app.exec()**，
       而本项目踩过的那个致命 bug（closeEvent 里 ignore() 导致 quit() 静默失效、
       事件循环继续运行、程序退不掉）恰恰只在真实事件循环里才会暴露。
       所以「能否退出」这类问题在单元测试里 100% 漏检，必须独立冒烟。

用法：
    python tools/check_quit.py                # 自动选目标：有 dist 就测 exe，否则测开发期
    python tools/check_quit.py --dev          # 强制测开发期 main.py
    python tools/check_quit.py --exe <路径>   # 强制测指定 exe

退出码：0 = 通过；1 = 失败。
"""

from __future__ import annotations

import argparse
import locale
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# 打包产物（build.spec 里 COLLECT(name=...) 决定）
DIST_EXE = PROJECT_ROOT / "dist" / "桌面宠物计时器" / "桌面宠物计时器.exe"

# 让程序在启动多少毫秒后自动走一遍「退出」流程
QUIT_DELAY_MS = 2000
# 进程必须在这么多秒内结束
TIMEOUT_SECONDS = 5.0
# 程序至少要活这么久才算「真的启动过」。
# 若单实例锁被占用，程序会立刻退出（只弹个提示框），用这个下限把它识别出来。
MIN_UPTIME_SECONDS = 1.2

# main.py 识别的测试钩子环境变量
AUTO_QUIT_ENV = "DPT_AUTO_QUIT_MS"


def resolve_target(force_dev: bool, exe_arg: str | None) -> tuple[list[str], str]:
    """决定跑什么：显式指定的 exe > dist 里的 exe > 开发期 main.py。"""
    if exe_arg:
        path = Path(exe_arg).expanduser().resolve()
        if not path.is_file():
            raise SystemExit(f"[check_quit] 指定的 exe 不存在：{path}")
        return [str(path)], f"指定 exe：{path}"

    if not force_dev and DIST_EXE.is_file():
        return [str(DIST_EXE)], f"打包产物：{DIST_EXE}"

    return [sys.executable, str(PROJECT_ROOT / "main.py")], "开发期 main.py"


def _decode(raw: bytes | None) -> str:
    """把子进程输出容错解码成文本。

    打包后的 exe 在 stdout 被重定向时按 Windows ANSI 代码页（中文系统是 GBK）
    写字节，而开发期的 python 子进程通常写 UTF-8 —— 两者都可能遇到。
    所以依次尝试几种编码，全失败再用替换字符兜底，
    绝不能因为「输出解不出来」就把整个检查带崩。
    """
    if not raw:
        return ""
    candidates = ("utf-8", "mbcs", locale.getpreferredencoding(False))
    for encoding in candidates:
        try:
            return raw.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="replace")


def main() -> int:
    parser = argparse.ArgumentParser(description="退出冒烟检查")
    parser.add_argument("--dev", action="store_true", help="强制测开发期 main.py")
    parser.add_argument("--exe", metavar="PATH", help="强制测指定的 exe")
    args = parser.parse_args()

    try:
        target, label = resolve_target(args.dev, args.exe)
    except SystemExit as exc:
        print(exc)
        return 1

    print("=" * 62)
    print(" 退出冒烟检查")
    print("=" * 62)
    print(f" 目标      : {label}")
    print(f" 触发时机  : 启动后 {QUIT_DELAY_MS} ms 走「退出」流程")
    print(f" 超时上限  : {TIMEOUT_SECONDS} s")
    print("-" * 62)

    env = dict(os.environ)
    env[AUTO_QUIT_ENV] = str(QUIT_DELAY_MS)
    # 用临时 APPDATA，避免污染用户真实配置（事件列表 / 悬浮窗位置）
    temp_appdata = tempfile.mkdtemp(prefix="dpt_quit_check_")
    env["APPDATA"] = temp_appdata

    started = time.monotonic()
    try:
        # 注意：不要用 text=True。打包后的 exe 在 stdout 被重定向时会按系统
        # 本地编码（中文 Windows 是 GBK）写字节，按 UTF-8 解码会抛
        # UnicodeDecodeError，把整个检查带崩。这里收字节再自行容错解码。
        result = subprocess.run(
            target,
            env=env,
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            timeout=TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        elapsed = time.monotonic() - started
        print(f"[FAIL] {elapsed:.1f} 秒内进程没有结束 —— 「退出」失效了。")
        print()
        print("  最可能的原因（按概率排序）：")
        print("   1. 已经有一个实例在运行，本次启动只弹了「程序已经在运行」提示框并卡住。")
        print("      -> 请先在托盘里退出正在运行的实例，再重跑本检查。")
        print("   2. 某个顶层窗口的 closeEvent 里调用了 event.ignore()，")
        print("      导致 Qt 退出流程被中断、app.quit() 静默失效。")
        print("      -> 检查 ui/ 下各窗口的 closeEvent。")
        print()
        print("  提示：直接手工启动程序，点托盘菜单「退出」，看进程是否消失，可以复现。")
        return 1
    except OSError as exc:
        print(f"[FAIL] 无法启动目标进程：{exc}")
        return 1

    elapsed = time.monotonic() - started
    print(f" 实际耗时  : {elapsed:.2f} s")
    print(f" 退出码    : {result.returncode}")

    out_text = _decode(result.stdout)
    err_text = _decode(result.stderr)
    if out_text:
        print(" 子进程输出:")
        for line in out_text.splitlines():
            print(f"   | {line}")
    if err_text:
        print(" 子进程错误输出:")
        for line in err_text.splitlines():
            print(f"   ! {line}")

    print("-" * 62)

    if result.returncode != 0:
        print(f"[FAIL] 退出码非 0（{result.returncode}）。")
        return 1

    if elapsed < MIN_UPTIME_SECONDS:
        print(
            f"[FAIL] 进程只活了 {elapsed:.2f} s，短于 {MIN_UPTIME_SECONDS} s —— "
            "大概率是被单实例锁挡下后立刻退出了，并没有真正跑起来。"
        )
        print("       请先退出正在运行的实例再重跑。")
        return 1

    print("[PASS] 应用完整启动、走完「退出」路径、进程正常结束。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
