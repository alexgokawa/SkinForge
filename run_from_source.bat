@echo off
rem Runs SkinForge without building the .exe (needs Python 3.10+ with Pillow 11.2+).
cd /d "%~dp0"
python -m skinforge
if errorlevel 1 pause
