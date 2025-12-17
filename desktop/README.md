# EzScalperBot Desktop (experimental)

Additive scaffolding to build a portable Windows desktop bundle without affecting the web/server deployment.

## What’s included
- `launcher.py` — starts Django + Celery worker/beat and opens the web UI in a pywebview window.
- `config.sample.yml` — copied to `%APPDATA%/EzScalperBot/config.yml` on first run.
- `desktop.spec` — PyInstaller template.
- `build_desktop.ps1` — helper script to build the bundle.
- `config/settings_desktop.py` (in `config/`) — settings overlay to allow SQLite/local paths.

## Quick start
```powershell
python -m pip install -r desktop/requirements-desktop.txt
python manage.py collectstatic --noinput
pyinstaller desktop/desktop.spec
```
Run `dist/EzScalperBot/EzScalperBot.exe`.

## Notes
- Data/logs/db live in `%APPDATA%/EzScalperBot`.
- `DJANGO_SETTINGS_MODULE` is set to `config.settings_desktop` by the launcher.
- MT5 terminal path must be set in `config.yml` (or via existing broker creds in DB).
