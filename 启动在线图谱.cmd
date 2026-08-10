@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "RISKMAP_PYTHON=%~dp0.venv\Scripts\python.exe"
if not exist "%RISKMAP_PYTHON%" set "RISKMAP_PYTHON=%~dp0..\.venv\Scripts\python.exe"
if not exist "%RISKMAP_PYTHON%" set "RISKMAP_PYTHON=python"
"%RISKMAP_PYTHON%" server.py
pause
