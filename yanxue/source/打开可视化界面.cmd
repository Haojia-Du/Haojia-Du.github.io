@echo off
chcp 65001 >nul
set "PYTHONIOENCODING=utf-8"
cd /d "%~dp0"
if exist "%~dp0.venv\Scripts\python.exe" (
  "%~dp0.venv\Scripts\python.exe" -m course_notes.launcher --project "%~dp0."
) else (
  python -m course_notes.launcher --project "%~dp0."
)
if errorlevel 1 pause

