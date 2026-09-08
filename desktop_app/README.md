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

## Historical backtesting

The Backtesting workspace has two tabs: **Historical backtests** runs isolated
simulations; **Run evidence** shows the recent live/demo strategy-cycle journal.

In Historical backtests, select a bot (for its instrument identity), one of the
six supported candle strategies, and the CSV timeframe. Import historical **bid**
OHLC candles, then enter the contract size, point size, fixed lot quantity,
initial balance, symbol profit currency, spread, slippage, and round-trip
commission per lot. Broker economics must be supplied explicitly; the replay
does not connect to MT5 to guess them. Currency conversion is not simulated.

CSV columns: `time,open,high,low,close,tick_volume`. MT5 tab-separated exports with
`<DATE>`, `<TIME>`, `<OPEN>`, `<HIGH>`, `<LOW>`, `<CLOSE>`, and `<TICKVOL>` are also
accepted, including UTF-8 and BOM-marked UTF-16 files. Timestamps must increase,
and candles must already be completed. Explicit timestamp offsets are converted
to UTC; for timestamps without offsets, enter the CSV's UTC offset in minutes
(for example, `120` for UTC+2). Gaps are retained and counted. Volume is required
for the volume-dependent breakout and momentum strategies.

Imports are limited to 1 MB / 10,000 candles. Start and end dates are optional
UTC dates; the end date is inclusive. Include sufficient earlier candles for
warmup (100 by default). Very large history-window/date-range combinations are
rejected with a request to reduce the range.

Each run saves its source data hash, CSV, strategy defaults, replay settings,
performance summary, equity curve, skip counts, and simulated trades. Select a
trade for exact prices and cost details, or export all trades to CSV. Saved
backtests are private to their creator. A zero-trade run is a valid result,
with skip reasons explaining why signals did not qualify.

The model evaluates completed candles, enters on the next available open, and
holds one fixed-size position using the selected strategy's SL/TP. Stop gaps
fill at the worse open; when both SL and TP fall within one candle, the chosen
stop-first/target-first policy applies. Short exits use the simulated ask.
Drawdown is measured from candle-close liquidation equity. This is a standalone
strategy simulation, not an exact live-bot replay: live HTF gating, automatic
strategy selection, news, portfolio risk, trailing/partial exits, swap, margin
liquidation, and currency conversion are not included. These assumptions are
also shown with every result.

Apply `python manage.py migrate` when updating an existing source backend.
Restart Flutter after adding the native file-selector plugin; hot reload alone
does not load a new native plugin. The packaged app applies database migrations
on startup. Native CSV dialogs use Flutter's maintained
[file_selector plugin](https://pub.dev/packages/file_selector).
