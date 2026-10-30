@echo off
rem Builds dist\HOI4Graphs.exe - a single file that runs without Python installed.
rem Keep config.json next to the exe; drag a save folder onto the exe to run it.
cd /d "%~dp0"
where py >nul 2>nul && (set PY=py) || (set PY=python)
%PY% -m pip install --upgrade pyinstaller matplotlib || goto :err
%PY% -m PyInstaller --noconfirm --onefile --console --name HOI4Graphs ^
    --hidden-import hoi4_save_parser --hidden-import punchcard hoi4_graphs.py || goto :err
copy /y config.json dist\config.json >nul
echo.
echo Built dist\HOI4Graphs.exe (config.json copied next to it)
pause
exit /b 0
:err
echo Build failed.
pause
exit /b 1
