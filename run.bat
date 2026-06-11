@echo off
REM gen-audio 一键启动脚本（Windows）
REM 用法：双击 run.bat，或在命令行 `run`

echo ===================================
echo  gen-audio 启动中...
echo  浏览器打开: http://127.0.0.1:8000
echo  按 Ctrl+C 停止
echo ===================================

REM 检查 Python
where python >nul 2>nul
if errorlevel 1 (
    echo [ERROR] 未找到 python，请先安装 Python 3.11+
    pause
    exit /b 1
)

REM 检查依赖
python -c "import fastapi" >nul 2>nul
if errorlevel 1 (
    echo [INFO] 首次启动，安装依赖...
    pip install -r requirements.txt
    if errorlevel 1 (
        echo [ERROR] 依赖安装失败
        pause
        exit /b 1
    )
)

REM 启动 uvicorn
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
pause
