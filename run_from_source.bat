@echo off
rem Runs SkinForge without building the .exe (needs Python 3.10+).
rem Installs / updates what it needs first (Pillow, numpy) - quick when already there.
cd /d "%~dp0"
python -m pip install -q -r requirements.txt
python -m skinforge
if errorlevel 1 pause
