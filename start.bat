@echo off
chcp 65001 >nul
echo ========================================
echo    AI Factory · 本地服务启动
echo ========================================
echo.

:: 检查 Python
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [错误] 未找到 Python，请先安装 Python 3.10+
    pause
    exit /b 1
)

:: 检查依赖
echo [检查] 安装依赖...
pip install -r "%~dp0requirements.txt" -q

echo.
echo [启动] Web 服务...
python "%~dp0main.py" --no-browser

pause