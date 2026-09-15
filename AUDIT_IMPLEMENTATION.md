# Consolidated audit implementation

This implements the general, opposite-scalp and Gold audit list. Source changes
are in the working tree. Database migrations are not applied to the live account.

The preceding follow-up fixes are also retained: the bot schedule is authoritative,
engine signals bypass strategy re-planning, scores use normalized weights, full
applied asset recommendations are frozen, scalp sizing uses broker point/digits,
and bot replay runs the shared decision, risk and position-management pipeline.

| Audit item | Implementation |
| --- | --- |
| Flutter / Safety CI | Latest published Safety CI was green at `05668d54`. Updated desktop suite passes all 34 tests, including compact layouts, multiple windows and scalp-limit warnings. |
| Scalp monetary risk | `decision_scalp_qty_multiplier` reduces the risk percentage; final submission caps it again. 0.30% × 0.30 = 0.09%. Fixed sizing receives the ratio once. |
| Hedging account | Decision and final submission require MT5 retail hedging mode, using `margin_mode`, not demo/live `trade_mode`. Explicit broker prohibition also blocks entry. |
| Pin Bar entry | The immediately following completed candle must close beyond the trigger without invalidating the structural stop. The trigger and R contract travel with the signal. |
| Momentum entry / R:R | Risk starts at the pullback close; the target is recalculated from the current submission quote and original structural stop. Nonpositive risk is rejected. |
| Gold strategy pool | Preset v3 includes Trend Pullback, Breakout + Retest, Momentum Ignition, Pin Bar and Doji Breakout. Existing bots adopt it only on explicit reapplication. |
| Gold context | Configured frames drive live and replay analysis. The smallest is structural bias; the largest is dominant regime. M15/H1 must agree with Gold's setup direction. Missing/conflicting context blocks entry. |
| Gold structural SL | The configured 0.10–0.30% envelope validates the detector stop during routing and final submission. A supplied structural stop is never moved to fit. |
| Protected primary | Fresh broker stop must be at breakeven or better, or the primary must have at least +0.5 initial R unrealized. A closing or missing primary blocks the scalp. |
| Parent / child | Durable decision metadata contains `is_opposite_scalp` and `primary_position_id`; broker positions retain it through their originating order. The child closes if its primary closes. |
| One scalp per primary | A filled child consumes the allowance for the primary's lifetime. Pending and ambiguous orders reserve it, with final checks serialized by the existing account lock. |
| Dedicated scalp exits | Default stop is 0.75 ATR, falling back to 0.5 primary R; target is 1.2R. The hard 10-minute limit is capped at half the main management duration. Broker minimum stop distance applies. |
| Replay news | Explicitly disabled and labelled **News not simulated** in input, saved configuration and results. Live news policy remains enabled as configured. |
| Hybrid exits | TP1 is asset management policy. The runner retains the strategy-derived final target. The editor identifies 1.70R as the external-signal fallback. Management uses the actual fill for initial R. |
| Scalp limit warning | Enabling the feature warns if bot, account or per-symbol position limits are 1. Limits are never silently raised. |
| Scalp analytics | History and replay expose separate trade counts, win rate, PF, expectancy, P/L, costs and realized drawdown attribution. Partial fills are grouped per position. |
| Multiple Gold windows | Up to eight windows, each with timezone and weekdays. Gold recommends London and New York metals windows. Midnight crossings belong to the starting day. |
| Generated C# files | Removed the 12 tracked `CTraderGateway/obj` files; `bin` and `obj` are ignored. |
| Main protection | Applied required `backend-safety` and `desktop-client` checks from the GitHub Actions app, strict/up-to-date checking, administrator enforcement, and blocked force push/deletion. Declarative request: `.github/branch-protection.json`. |

## Validation and adoption

Validation on 2026-09-15: the full Django suite passed **299 tests**. Two
subsequently added regressions also passed: zero base risk remains disabled,
and analytics omits positions whose realized exit P/L is unavailable. Flutter
passed **34 tests** and `flutter analyze` reported no issues. Django system
checks and `makemigrations --check --dry-run` passed, with no missing migrations.
A final rerun of all **71 tests** covering Gold, overlays, live risk, pipeline
replay and scalper contracts passed with both late fixes included.
The latest published Safety CI is green at `05668d54`; these additional working
tree changes have been validated locally and have not been pushed.

Backend validation uses isolated in-memory databases. The Gold contract fixture
uses synthetic bid candles with completed M15/H1 context and checks entry, stop,
TP1 and runner behavior. It is a functional replay, not historical profitability
evidence or a forward-demo trial. Its captured result is
`.runtime/gold-reference-replay.json`.

Run `python manage.py migrate` for source deployments. The desktop package runs
migrations at startup. Existing frozen bot presets remain unchanged; explicitly
reapply Gold's recommendation to adopt its new strategy pool and schedule.

Forward-demo testing on the intended broker remains necessary before treating
the Gold pipeline as proven for real execution or extending its parameters to
other assets. No live/demo orders were placed for this implementation.

Replay uses completed OHLC candles, constant execution costs and one simulated
bot. Tick order, changing spreads, conversions and broker liquidation are not
reconstructed. Market slippage and price rounding can change realized R:R from
the submission quote. Drawdown attribution is realized cash flow, excluding
floating P/L; subtracting scalp P/L does not establish causal alpha.

The mode check follows MT5's separate account properties documented in the
[MetaTrader account API](https://www.mql5.com/en/docs/python_metatrader5/mt5accountinfo_py).
Protection uses GitHub's
[branch-protection API](https://docs.github.com/en/rest/branches/branch-protection).
