#!/bin/bash
# 桌面宠物计时器 · macOS / Linux 启动器
# 双击运行（或终端 bash 启动.sh）
#
# 它做三件事：
#   1. 第一次运行时自动创建虚拟环境并装依赖（约 1-2 分钟）
#   2. 之后直接启动，不再重复安装
#   3. 崩溃信息写进 启动错误.log

cd "$(dirname "$0")" || exit 1

VENV=".venv"
PY="$VENV/bin/python"

# ---------- 环境检查 ----------
if [ ! -x "$PY" ]; then
    echo "首次运行：正在创建 Python 虚拟环境..."
    # 优先 python3，其次 python
    if command -v python3 >/dev/null 2>&1; then
        PYTHON=python3
    elif command -v python >/dev/null 2>&1; then
        PYTHON=python
    else
        echo "[错误] 没有找到 Python。"
        echo "请先从 https://www.python.org/downloads/ 安装 Python 3.10 以上版本，"
        echo "安装后在终端里执行：python3 --version  确认可用，再重新双击本文件。"
        echo
        read -n 1 -s -r -p "按任意键关闭..."
        exit 1
    fi

    "$PYTHON" -m venv "$VENV" || {
        echo "[错误] 创建虚拟环境失败。"
        read -n 1 -s -r -p "按任意键关闭..."
        exit 1
    }
fi

# ---------- 依赖检查 ----------
if ! "$PY" -c "import PySide6" >/dev/null 2>&1; then
    echo "正在安装依赖（PySide6 约 100MB，请耐心等待）..."
    "$PY" -m pip install --upgrade pip --quiet
    "$PY" -m pip install -r requirements.txt || {
        echo "[错误] 依赖安装失败，请检查网络。"
        read -n 1 -s -r -p "按任意键关闭..."
        exit 1
    }
fi

# ---------- 启动 ----------
rm -f "启动错误.log"
"$PY" main.py 2>>"启动错误.log"
CODE=$?

if [ $CODE -ne 0 ]; then
    echo
    echo "[程序异常退出，退出码 $CODE]"
    echo "错误信息已写入 启动错误.log，内容如下："
    echo "----------------------------------------"
    tail -20 "启动错误.log" 2>/dev/null
    echo "----------------------------------------"
    read -n 1 -s -r -p "按任意键关闭..."
fi
