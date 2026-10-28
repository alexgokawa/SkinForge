# Builds dist\SkinForge.exe (single file, no console window).
# Run from this folder:  powershell -ExecutionPolicy Bypass -File .\build_exe.ps1
$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

# 1. find a Python 3.10+
$py = $null
if (Get-Command py -ErrorAction SilentlyContinue) { $py = @("py", "-3") }
elseif (Get-Command python -ErrorAction SilentlyContinue) { $py = @("python") }
if (-not $py) {
    Write-Host "Python not found. Install Python 3.12 from python.org (tick 'Add to PATH') and run this again." -ForegroundColor Red
    exit 1
}
$exe = $py[0]
$pre = @()
if ($py.Count -gt 1) { $pre = $py[1..($py.Count - 1)] }
& $exe @pre -c "import sys; assert sys.version_info >= (3,10), sys.version"
if ($LASTEXITCODE -ne 0) { Write-Host "Python 3.10 or newer needed." -ForegroundColor Red; exit 1 }

# 2. private build environment (doesn't touch your normal Python / conda)
if (-not (Test-Path ".venv\Scripts\python.exe")) {
    Write-Host "Creating build environment..." -ForegroundColor Cyan
    & $exe @pre -m venv .venv
}
$vpy = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
& $vpy -m pip install --upgrade pip --quiet
& $vpy -m pip install --upgrade "pillow>=11.2" pyinstaller --quiet
if ($LASTEXITCODE -ne 0) { Write-Host "pip install failed." -ForegroundColor Red; exit 1 }

# 3. build
Write-Host "Building SkinForge.exe..." -ForegroundColor Cyan
& $vpy -m PyInstaller --noconfirm --clean --onefile --windowed `
    --name SkinForge `
    --icon "skinforge\web\skinforge.ico" `
    --add-data "skinforge\web;skinforge\web" `
    --hidden-import tkinter --hidden-import tkinter.filedialog `
    SkinForge.py
if ($LASTEXITCODE -ne 0) { Write-Host "PyInstaller failed." -ForegroundColor Red; exit 1 }

Write-Host ""
Write-Host "Done: $PSScriptRoot\dist\SkinForge.exe" -ForegroundColor Green
Write-Host "You can copy that one file anywhere and share it."
