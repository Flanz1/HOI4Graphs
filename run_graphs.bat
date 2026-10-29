@echo off
rem Drag a folder of HOI4 saves (or a .zip of them) onto this file,
rem or double-click it to pick the folder in a dialog.
cd /d "%~dp0"
where py >nul 2>nul && (set PY=py) || (set PY=python)
%PY% -c "import matplotlib" 2>nul || %PY% -m pip install matplotlib
%PY% hoi4_graphs.py %*
pause
