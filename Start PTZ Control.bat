@echo off
rem PTZ Control (Windows): installeren en starten in een.
rem - Eerste keer, of na een update met gewijzigde requirements.txt: maakt/werkt de .venv bij.
rem - Daarna start de app en opent de bediening in de browser.
rem Opnieuw installeren afdwingen: "Start PTZ Control.bat" /installeer
rem Overige argumenten gaan door naar ptz_control.py (bijv. --port 9000).
rem Stoppen: dit venster sluiten of Ctrl+C.
setlocal
cd /d "%~dp0"
chcp 65001 >nul
title PTZ Control

set "FORCE="
set "ARGS="
:args
if "%~1"=="" goto :argsdone
if /i "%~1"=="/installeer" ( set "FORCE=1" ) else if /i "%~1"=="--installeer" ( set "FORCE=1" ) else if /i "%~1"=="/install" ( set "FORCE=1" ) else ( set "ARGS=%ARGS% %1" )
shift
goto :args
:argsdone

if defined FORCE (
  echo Opnieuw installeren...
  goto :install
)
if not exist ".venv\Scripts\python.exe" (
  echo Eerste start: installeren ^(een paar minuten^)...
  goto :install
)
if not exist ".venv\requirements.installed" goto :changed
fc /b requirements.txt ".venv\requirements.installed" >nul 2>&1
if errorlevel 1 goto :changed
goto :start

:changed
echo requirements.txt is gewijzigd: packages bijwerken...
goto :install

:install
rem --- Python 3.9+ zoeken: eerst de py-launcher, dan python
set "PY="
where py >nul 2>&1
if not errorlevel 1 (
  py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)" >nul 2>&1
  if not errorlevel 1 set "PY=py -3"
)
if not defined PY (
  python -c "import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)" >nul 2>&1
  if not errorlevel 1 set "PY=python"
)
if not defined PY (
  echo Geen Python 3.9+ gevonden.
  echo Installeer Python via https://www.python.org/downloads/windows/
  echo en vink tijdens de installatie "Add python.exe to PATH" aan.
  echo Of in een terminal: winget install -e --id Python.Python.3.12
  goto :fail
)
for /f "delims=" %%v in ('%PY% --version') do echo ^> %%v (%PY%)

if not exist ".venv\Scripts\python.exe" (
  echo ^> .venv aanmaken...
  %PY% -m venv .venv
  if errorlevel 1 goto :fail
) else (
  echo ^> .venv bestaat al, packages bijwerken...
)
del /q ".venv\requirements.installed" >nul 2>&1

".venv\Scripts\python.exe" -m pip install --upgrade pip
echo ^> Packages installeren (eerste keer kan dit enkele minuten duren)...
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto :fail

echo ^> Controleren en volgmodel klaarzetten...
".venv\Scripts\python.exe" -c "import tracker; print('OpenCV OK' if tracker.available() else tracker.CV_ERROR); d = tracker.Detector(); print('Persoonsdetectie:', d.kind, '- rekent op', d.device)"

rem pas na een geslaagde installatie markeren, zodat een afgebroken installatie de volgende keer opnieuw loopt
copy /y requirements.txt ".venv\requirements.installed" >nul
echo Installatie klaar.
echo.

:start
".venv\Scripts\python.exe" ptz_control.py --open%ARGS%
if errorlevel 1 pause
exit /b

:fail
echo.
echo Installatie mislukt. Zie de melding hierboven.
pause
exit /b 1
