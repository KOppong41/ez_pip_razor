# Build a portable desktop bundle using PyInstaller.
# Usage: from repo root -> powershell -ExecutionPolicy Bypass -File desktop/build_desktop.ps1

param(
    [string]$Python = "python"
)

Push-Location (Join-Path $PSScriptRoot "..")

Write-Host "Installing desktop requirements..."
& $Python -m pip install -r desktop/requirements-desktop.txt

Write-Host "Collecting static assets..."
& $Python manage.py collectstatic --noinput

Write-Host "Building desktop executable..."
& $Python -m PyInstaller desktop/desktop.spec

Pop-Location
