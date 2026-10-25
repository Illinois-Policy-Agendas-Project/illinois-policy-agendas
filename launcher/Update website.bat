@echo off
rem Double-click on Windows to open the website control panel.
cd /d "%~dp0"
where py >nul 2>nul && (py -3 start.py) || (python start.py)
pause
