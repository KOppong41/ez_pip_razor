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

In Historical backtests, select a bot and a replay mode. Scalper bots default to
**Bot pipeline**, which uses a frozen copy of the bot, scalper profile, execution
settings and account risk limits. It runs the shared live strategy selection,
candidate allocation, decision, order creation, final risk gate and position
management services in a separate process with an in-memory database. Broker
IPC and outbound networking are disabled in that process. **Single strategy**
retains the simpler fixed-size replay of one of the six candle strategies.

Import historical **bid** OHLC candles, then enter contract size, point size,
initial balance, profit currency, spread, slippage and round-trip commission.
Bot pipeline additionally requires broker minimum/maximum volume, volume step,
price digits, minimum stop distance, margin required per lot and account margin
mode (0 netting, 1 exchange, 2 hedging). Opposite scalps require mode 2. Quantity and
score thresholds come from the bot. Single strategy uses the form's fixed
quantity and raw-score threshold. Instrument defaults first reuse your latest completed run
for the same bot and symbol; otherwise, a read-only lookup of the matching
connected MT5 account supplies available contract size, point size, profit
currency, current spread, volume limits and price digits. Margin per lot must be
entered explicitly. This lookup never starts the terminal, logs in,
switches accounts, enables trading, or places orders. If specifications are
unavailable, unknown sizes/currency remain blank for manual entry. The source
and read/save time appear above the fields. Reloading defaults resets instrument
inputs; switching bots also resets them to avoid carrying another symbol's sizes.
Currency conversion is not simulated.

Every economic input has visible help and examples. Point size is the price
change for one broker point (not pip size or cash per point). Spread in points
is `(ask - bid) / point_size`; commission is the opening plus closing fee per
lot in the profit currency. Current broker spread is only a snapshot, not a
historical average. Slippage and commission default to zero unless restored
from your saved run; zero explicitly excludes that cost and must be reviewed.

CSV columns: `time,open,high,low,close,tick_volume`. MT5 tab-separated exports with
`<DATE>`, `<TIME>`, `<OPEN>`, `<HIGH>`, `<LOW>`, `<CLOSE>`, and `<TICKVOL>` are also
accepted, including UTF-8 and BOM-marked UTF-16 files. Timestamps must increase,
and candles must already be completed. Explicit timestamp offsets are converted
to UTC; for timestamps without offsets, enter the CSV's UTC offset in minutes
(for example, `120` for UTC+2). Gaps are retained and counted. Volume is required
for bot pipeline replay and the volume-dependent breakout and momentum strategies.

Upload the original MT5 export. Excel commonly displays a tab-separated MT5
file entirely in column A; this does not damage the file until it is resaved.
The importer accepts BOM-less UTF-16 and can recover rows that Excel wrapped as
one quoted tab-delimited cell. If the separator characters were actually
removed, the prices cannot be split unambiguously and the UI asks for a fresh
MT5 Bars export.

Imports are limited to 25 MB / 150,000 candles, enough for roughly 100,000 M1
candles in a two-month continuous-market export. Importing validates the CSV and
shows its UTC coverage and first tradable candle after warmup (100 by default).
The date pickers default to that first tradable day and the last CSV day
(inclusive), and are bounded by those dates. Earlier CSV candles remain available
for warmup when selecting a narrower period. Dates do not download or generate
additional data. Changing timeframe, strategy, timezone offset or warmup clears
the date preview; use **Use full CSV date range** to validate it again. Running
also revalidates if needed. Replay work is capped at 15 million candle-window
evaluations; with the default 100-bar warmup, all 150,000 imported candles can
be replayed in Single strategy mode. Bot pipeline is capped at 2,000 test
candles and 240 seconds per run. Use a narrower date range with earlier CSV
history retained: the live HTF gate needs at least 30 completed candles in
each configured context frame. Gold uses M15 structural bias and H1 regime,
so include at least 30 hours of contiguous history before its test period. The saved equity curve is sampled to at most 5,000 display points
to keep results responsive, while summary drawdown still evaluates every candle.

Each run saves its source data hash, CSV, strategy defaults, bot snapshot when
applicable, replay settings,
performance summary, equity curve, skip counts, and simulated trades. Select a
trade for exact prices and cost details, or export all trades to CSV. Saved
backtests are private to their creator. A zero-trade run is a valid result,
with skip reasons explaining why signals did not qualify.

Both modes evaluate completed candles and enter on the next available open.
Single strategy holds one fixed-size position using its SL/TP. Bot pipeline
uses the configured sizing mode, broker lot rounding, account exposure and
capital limits, automatic strategy selection, schedule and managed exits.
Stop gaps
fill at the worse open; when both SL and TP fall within one candle, the chosen
stop-first/target-first policy applies. Short exits use the simulated ask.
Drawdown is measured from candle-close equity. Trailing, breakeven and partial
exits run at candle close, with updated stops active on later candles. Fills
still depend on OHLC assumptions; tick sequencing, changing spreads/margin,
currency conversion, swap and broker liquidation are not reconstructed.

Bot pipeline starts the selected bot active on an empty simulated account.
Operational stops, prior loss streaks and cached market context are reset;
numeric risk limits remain in force. Other bots and manual positions are not
included in this single-instrument dataset. Each risk day starts at its first
observed quote. **News not simulated:** replay explicitly disables the economic
calendar because archived events and freshness coverage are unavailable. Live
news filtering is unchanged. These limits and assumptions are saved with every result.

Apply `python manage.py migrate` when updating an existing source backend.
Restart Flutter after adding the native file-selector plugin; hot reload alone
does not load a new native plugin. The packaged app applies database migrations
on startup. Native CSV dialogs use Flutter's maintained
[file_selector plugin](https://pub.dev/packages/file_selector).

## Gold and opposite-scalp contracts

Pin Bar requires the next completed candle to close beyond its trigger without
invalidating the structural stop. Momentum uses the pullback close as its entry
reference. At submission, these strategies calculate their target R from the
current quote and the original structural stop. Market slippage and tick rounding
can still change realized R:R. Gold rejects structural stops outside its configured
0.10?0.30% envelope; it does not move them to fit.

Gold's recommended pool is Trend Pullback, Breakout + Retest, Momentum Ignition,
Pin Bar and Doji Breakout. All configured context frames participate in live and
replay analysis. Missing or conflicting context blocks entries. Gold's default
M15 structure and H1 dominant trend must agree with the setup direction.

Hybrid exits take the asset-configured fraction at TP1, then manage the remaining
position with breakeven/trailing and the strategy's final target. The 1.70R field
is a fallback for externally planned signals, not an override of an engine target.
The editor labels this distinction. Management R uses the actual filled entry.

The schedule editor supports up to eight windows, each with its own timezone and
weekdays. The Gold recommendation includes London and New York metals windows.
A window crossing midnight belongs to its starting weekday. An empty window list
preserves the original single-window schedule. Disabling the schedule disables
both forms. Reapply a preset explicitly to adopt its changed defaults; existing
bots keep their frozen preset and configured schedule.

Opposite scalp requires a confirmed MT5 hedging account, a primary position with
its stop at breakeven or better (or at least +0.5 initial R unrealized), and room
under bot/account/per-symbol position limits. It takes precedence over flipping
when enabled. A primary may have only one filled scalp during its lifetime;
pending or ambiguous requests reserve that allowance. An overlay closes when
its primary closes or when its hard duration limit expires.

`decision_scalp_qty_multiplier` now scales the monetary risk ceiling: 0.30% ?
0.30 = 0.09%. Fixed sizing is reduced once by the same ratio. Final validation
rechecks fresh broker state under the account lock. The default scalp stop is
0.75 ATR (or 0.5 primary R if ATR is unavailable), its target is 1.2R and its hard
limit is 10 minutes, capped at half the main management duration. Broker minimum
stop distance still applies. Advanced per-bot overrides live in
`scalper_params.opposite_scalp`: `sl_atr_multiplier`, `tp_r`, `max_duration_minutes`.
Generic absolute-price scalp offsets are no longer used.

Trade history and saved replay results report opposite-scalp trades, win rate,
profit factor, expectancy, net P/L, costs and realized drawdown attribution
separately. Partial fills count as one trade. Live costs include recorded
commission, fees and swap; spread/slippage are embedded in broker P/L. Drawdown
attribution excludes floating P/L and does not establish strategy alpha.
