@echo off
rem Maakt .venv aan en installeert requirements.txt (Windows). Dubbelklik, of: Installeer.bat
setlocal
cd /d "%~dp0"
chcp 65001 >nul
set "NOPAUSE="
if /i "%~1"=="/nopause" set "NOPAUSE=1"

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

".venv\Scripts\python.exe" -m pip install --upgrade pip
echo ^> Packages installeren (eerste keer kan dit enkele minuten duren)...
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto :fail

echo ^> Controleren en volgmodel klaarzetten...
".venv\Scripts\python.exe" -c "import tracker; print('OpenCV OK' if tracker.available() else tracker.CV_ERROR); d = tracker.Detector(); print('Persoonsdetectie:', d.kind, '- rekent op', d.device)"

echo.
echo Klaar. Start de app met "Start PTZ Control.bat".
if not defined NOPAUSE pause
exit /b 0

:fail
echo.
echo Installatie mislukt. Zie de melding hierboven.
if not defined NOPAUSE pause
exit /b 1
