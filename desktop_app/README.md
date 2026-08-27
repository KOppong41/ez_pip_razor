# EZ Trade Flutter desktop UI

Run from this directory with `flutter run -d windows`. The production entry
point automatically starts the local backend supervisor and waits for the API.
Widget tests use `const EzTradeApp()` without a supervisor so they remain
isolated.

Source runs use `http://127.0.0.1:8001` by default so they cannot silently
attach to an installed desktop backend and its separate local database. Set
`EZTRADE_BACKEND_PORT` to override the development port when needed.

Do not distribute the raw Flutter `Release` directory by itself. Use
`../desktop/build_desktop.ps1`, which bundles the Python backend and creates the
complete `dist/EzTradeDesktop.zip` package.
