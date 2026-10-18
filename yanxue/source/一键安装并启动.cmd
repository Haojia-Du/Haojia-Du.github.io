@echo off
chcp 65001 >nul
set "PYTHONIOENCODING=utf-8"
cd /d "%~dp0"
python -c "import sys; assert sys.version_info >= (3,11)" >nul 2>&1
if errorlevel 1 (
  echo 请先安装 Python 3.11 或更新版本，并勾选 Add Python to PATH。
  start "" https://www.python.org/downloads/windows/
  pause
  exit /b 1
)
if not exist ".venv\Scripts\python.exe" python -m venv .venv
if errorlevel 1 goto failed
".venv\Scripts\python.exe" -m pip install ".[documents,recorder]"
if errorlevel 1 goto failed
".venv\Scripts\python.exe" -m playwright install chromium
if errorlevel 1 goto failed
call "打开可视化界面.cmd"
exit /b 0
:failed
echo 安装未完成，请检查网络或上方提示后重试。
pause
exit /b 1
