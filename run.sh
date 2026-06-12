#!/usr/bin/env bash
# gen-audio 一键启动脚本（macOS / Linux）
# 用法：./run.sh

set -e

echo "==================================="
echo " gen-audio 启动中..."
echo " 浏览器打开: http://127.0.0.1:8000"
echo " 按 Ctrl+C 停止"
echo "==================================="

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# 检查 uv
if command -v uv &>/dev/null; then
    UV="uv"
elif command -v pip3 &>/dev/null; then
    UV=""
else
    echo "[ERROR] 未找到 uv 或 pip3，请先安装其一"
    exit 1
fi

# 创建虚拟环境（如果不存在）
if [ ! -d ".venv" ]; then
    echo "[INFO] 创建 Python 虚拟环境..."
    if [ -n "$UV" ]; then
        uv venv .venv --python 3.12
    else
        python3 -m venv .venv
    fi
fi

# 激活虚拟环境
source .venv/bin/activate

# 安装依赖（首次或 lock 文件有更新时）
if [ -n "$UV" ]; then
    if [ -f "requirements-lock.txt" ]; then
        uv pip install -r requirements-lock.txt
    else
        uv pip install -r requirements.txt
    fi
else
    if [ -f "requirements-lock.txt" ]; then
        pip install -r requirements-lock.txt
    else
        pip install -r requirements.txt
    fi
fi

# 启动 uvicorn
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
