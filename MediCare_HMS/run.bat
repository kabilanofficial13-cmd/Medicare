@echo off
cd /d "%~dp0"
echo Installing project dependencies (first time or when updated)...
python -m pip install -r requirements.txt
echo Starting Hospital Management System...
python app.py
pause
