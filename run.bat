@echo off
REM gen-audio 一键启动脚本（Windows）
REM 用法：双击 run.bat，或在命令行 `run`

echo ===================================
echo  gen-audio 启动中...
echo  浏览器打开: http://127.0.0.1:9876
echo  按 Ctrl+C 停止
echo ===================================

REM 检查 Python
where python >nul 2>nul
if errorlevel 1 (
    echo [ERROR] 未找到 python，请先安装 Python 3.12+
    pause
    exit /b 1
)

REM 创建虚拟环境（如果不存在）
if not exist ".venv" (
    echo [INFO] 创建 Python 虚拟环境...
    python -m venv .venv
    if errorlevel 1 (
        echo [ERROR] 虚拟环境创建失败
        pause
        exit /b 1
    )
)

REM 激活虚拟环境
call .venv\Scripts\activate.bat

REM 安装依赖
if exist "requirements-lock.txt" (
    pip install -r requirements-lock.txt
) else (
    pip install -r requirements.txt
)
if errorlevel 1 (
    echo [ERROR] 依赖安装失败
    pause
    exit /b 1
)

REM 启动 uvicorn
uvicorn app.main:app --reload --host 127.0.0.1 --port 9876
pause
