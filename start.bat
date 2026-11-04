@echo off
chcp 65001 >nul
cd /d "%~dp0"
where python >nul 2>nul && (set PY=python) || (set PY=py -3)
echo Uruchamiam ranking faworytow na http://localhost:8000 ...
%PY% app.py --open
pause
