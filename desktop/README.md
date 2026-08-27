# EZ Trade self-contained Windows app

The Flutter executable starts and owns a bundled local backend. Users do not
need Python, Redis, PostgreSQL, VS Code, or a separate server installation.

## Development

Install the Python requirements once, then launch Flutter normally:

```powershell
python -m pip install -r desktop/requirements-desktop.txt
cd desktop_app
flutter run -d windows
```

In a source checkout, Flutter finds `desktop/backend_launcher.py` and starts it
with Python. The launcher starts:

- the local Django API on `http://127.0.0.1:8001` (kept separate from an
  installed build on port `8000`);
- one general Celery worker;
- one dedicated MT5 worker; and
- Celery beat.

The app waits for the API before showing sign-in. Closing Flutter first invokes
the authenticated bot-stop safety endpoint, then shuts down all owned backend
processes. The supervisor also watches Flutter's process ID so it cleans up
after a crash.

## Build the distributable package

From the repository root:

```powershell
powershell -ExecutionPolicy Bypass -File desktop/build_desktop.ps1
```

The script builds the PyInstaller backend, builds Flutter for Windows, combines
both, and creates `dist/EzTradeDesktop.zip`. Ship that zip as a unit. Users must
extract the full folder and run `ez_trade_desktop.exe`; the executable cannot be
moved away from its `data`, DLL, and `backend` folders.

Build-machine requirements are Python, Flutter, and Visual Studio's Desktop
development with C++ workload. End users need Windows x64 and their own
MetaTrader 5 terminal.

## First launch and local data

On a new installation the launcher automatically runs Django migrations. The
Flutter sign-in screen switches to first-run setup so the owner can create the
only initial local administrator. The user then adds their MT5 login, password,
server, and `terminal64.exe` path from Settings and tests the connection.

Per-user state is stored outside the installed application:

- database: `%APPDATA%/EzScalperBot/db.sqlite3`;
- configuration: `%APPDATA%/EzScalperBot/config.yml`;
- optional overrides: `%APPDATA%/EzScalperBot/.env`;
- logs: `%APPDATA%/EzScalperBot/logs`;
- broker encryption key: `%LOCALAPPDATA%/EzScalperBot/broker_creds.key`.

The desktop settings use SQLite and Celery's filesystem transport by default.
An explicit database configuration in the per-user `.env` still takes priority.
The local setup endpoint is available only in desktop mode, only from loopback,
and permanently refuses account creation after the first user exists.

Starting the application starts the scheduling infrastructure, but does not
itself enable entries. Live trading still requires a connected MT5 account,
explicit live-trading confirmation, and the user's Start command.
