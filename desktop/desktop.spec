"""PyInstaller onedir build for the backend bundled beside the Flutter app."""

from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules


project_root = Path.cwd()
desktop_dir = project_root / "desktop"

project_packages = [
    "config",
    "core",
    "bots",
    "brokers",
    "execution",
    "copytrade",
    "subscription",
    "tenant",
    "payments",
    "telegrambot",
    "notifications",
]

dynamic_runtime_packages = [
    "celery",
    "kombu",
    "billiard",
    "amqp",
    "corsheaders",
    "rest_framework",
    "rest_framework_simplejwt",
]


def gather_datas():
    collected = []
    for directory_name in ("templates", "static"):
        directory = project_root / directory_name
        if directory.exists():
            for file_path in directory.rglob("*"):
                if file_path.is_file():
                    collected.append(
                        (str(file_path), str(file_path.parent.relative_to(project_root)))
                    )
    collected.append((str(desktop_dir / "config.sample.yml"), "desktop"))
    for package in project_packages:
        collected.extend(collect_data_files(package, include_py_files=False))
    return collected


def is_runtime_module(module_name):
    parts = module_name.split(".")
    return not any(part == "tests" or part.startswith("test_") for part in parts)


hiddenimports = [
    "MetaTrader5",
    "celery",
    "django",
    "rest_framework",
    "rest_framework_simplejwt",
    "waitress",
    "jaraco.functools",
    "jaraco.context",
    "jaraco.text",
]
for package in project_packages:
    hiddenimports.extend(collect_submodules(package, filter=is_runtime_module))
for package in dynamic_runtime_packages:
    hiddenimports.extend(collect_submodules(package, filter=is_runtime_module))


a = Analysis(
    [str(desktop_dir / "backend_launcher.py")],
    pathex=[str(project_root)],
    binaries=[],
    datas=gather_datas(),
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=[
        "IPython",
        "jedi",
        "nbformat",
        "notebook",
        "parso",
        "pytest",
        "tkinter",
        "webview",
        "django.db.backends.mysql",
        "django.db.backends.oracle",
    ],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="eztrade_backend",
    debug=False,
    strip=False,
    upx=True,
    console=False,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    name="backend",
)
