@echo off
rem PTZ Control starten (Windows). Eerste keer: installeert automatisch de .venv.
rem Stoppen: dit venster sluiten of Ctrl+C.
setlocal
cd /d "%~dp0"
chcp 65001 >nul
title PTZ Control
if not exist ".venv\Scripts\python.exe" (
  echo Eerste start: .venv wordt geinstalleerd...
  call "%~dp0Installeer.bat" /nopause
  if errorlevel 1 (
    pause
    exit /b 1
  )
)
".venv\Scripts\python.exe" ptz_control.py --open %*
if errorlevel 1 pause
