@echo off
cd /d "%~dp0"
set FPL_OWNER=1
python -m streamlit run app.py
pause
