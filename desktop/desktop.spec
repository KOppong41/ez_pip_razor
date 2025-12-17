# PyInstaller spec template for EzScalperBot desktop build.
# Run from repo root: pyinstaller desktop/desktop.spec

import os
from pathlib import Path

# When PyInstaller executes a spec, __file__ is not set. Use CWD (repo root) instead.
project_root = Path.cwd()
desktop_dir = project_root / "desktop"

def gather_datas():
    datas_local = []
    templates_dir = project_root / "templates"
    static_dir = project_root / "static"
    if templates_dir.exists():
        for f in templates_dir.rglob("*"):
            if f.is_file():
                datas_local.append((str(f), str(f.relative_to(project_root))))
    if static_dir.exists():
        for f in static_dir.rglob("*"):
            if f.is_file():
                datas_local.append((str(f), str(f.relative_to(project_root))))
    # Include desktop config sample
    datas_local.append((str(desktop_dir / "config.sample.yml"), "desktop"))
    return datas_local

datas = gather_datas()

block_cipher = None

a = Analysis(
    [str(desktop_dir / "launcher.py")],
    pathex=[str(project_root)],
    binaries=[],
    datas=datas,
hiddenimports=[
    "MetaTrader5",
    "celery",
    "django",
    "jaraco.functools",
    "jaraco.context",
    "jaraco.text",
],
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    name="EzScalperBot",
    debug=False,
    strip=False,
    upx=True,
    console=False,
    icon=None,
)
