# Consolidated audit implementation

This records the general, opposite-scalp and Gold audit implementation, merged
in `3aa3fba`, and its subsequent working-tree fixes. Database migrations are not
applied to the live account by this continuation.

The preceding follow-up fixes are also retained: the bot schedule is authoritative,
engine signals bypass strategy re-planning, scores use normalized weights, full
applied asset recommendations are frozen, scalp sizing uses broker point/digits,
and bot replay runs the shared decision, risk and position-management pipeline.

| Audit item | Implementation |
| --- | --- |
| Flutter / Safety CI | The original audit recorded green Safety CI at `05668d54` and 34 passing desktop tests, including compact layouts, multiple windows and scalp-limit warnings. |
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
Those results describe the original audit validation. The follow-up passes
below have separate local validation.

## Gold and flip follow-up

- Gold auto-selection ranks Momentum Ignition, Breakout + Retest and Trend
  Pullback for high volatility, ATR expansion or a strong higher-timeframe trend.
  Moderate volatility favors Trend Pullback, Breakout + Retest and Pin Bar.
  Quiet conditions retain Pin Bar, Doji Breakout and Trend Pullback even when
  the required directional context is available. The configured strategy pool
  still limits selection, and wide spreads retain the precise-setup preference.
- Trend Pullback, Breakout + Retest and Doji Breakout now emit their entry price
  and configured target R multiple. These fields pass through the signal and
  decision to final execution, where targets use the current submission quote
  and retain the structural stop. Broker price rounding still applies.
- A qualifying high-score reversal takes precedence over an opposite scalp.
  Lower scores and scalper cooldowns still use the validated scalp path.
  A later daily-entry or trade-interval rejection cannot close the primary.
- The admin diagnostics summarize entry-rejection reasons over the existing
  24-hour window. The shared selector's Harami caller now supplies its analyzed
  higher-timeframe regime without referencing an undefined variable.

Continuation validation on 2026-09-15: the complete Django suite passed **314
tests** in 269 seconds. Django system checks, `makemigrations --check --dry-run`
and `git diff --check` passed. The full suite includes pipeline replay and the
new selector, flip and submission-target regressions. Captured output is in
`.runtime/continuation-full-django-tests.log`.

Regression tests first reproduced the quiet-regime, wide-spread and rejected-
replacement problems before their fixes. All execution checks use synthetic
broker data and isolated in-memory databases. These follow-up changes were
committed as `62db999` and published in PR #2; no live/demo orders were placed.

## Score contract and deferred reversal workflow

The next review pass standardizes all five Gold detectors on
`setup_quality_v1`. Each valid setup starts at 0.50. Quality components measure
bounded progress beyond each setup's requirements; their weighted average
adds at most another 0.50. Trend Pullback assigns 30% of its quality weight to
trend strength, so even an arbitrarily strong slope cannot produce a perfect
score by itself. Pin Bar now uses range, wick/body, level, trend and confirmation
quality instead of candle range alone. Momentum, Breakout and Doji use the same
aggregation contract and publish their component scores in run diagnostics.

These scores express observed setup quality, not calibrated win probabilities.
Historical or forward-demo outcomes are still required for empirical calibration.
Synthetic integration fixtures exercise simultaneous valid Gold setups and the
same candidate ranking function used by the live allocator.

Flip execution now occurs only when the selected replacement order is
dispatched. Signal evaluation records its intended position IDs and performs no
closes. The replacement must pass fanout, local guards, and an MT5 dry run of
the normal entry path, including final risk and `order_check`, before any close.
The dry run projects capacity without the exact owned replacement group, keeps
current free margin as a conservative constraint, and rolls back its database
changes. It sends no order and cannot resize fixed sizing twice.

The serialized executor closes a linked opposite-scalp child first, then its
primary, confirms the group is flat, and reruns market-sensitive validation
without any exposure exemptions before sending the full-size replacement.
Manual, unrelated or changed groups are rejected. The workflow stores close
order IDs and phase history, reconciles ambiguous closes on retry, and counts
a primary/child group as one reversal for the daily flip cap. A definitive
failure after flattening records `flip_reverse_aborted_after_close`; ambiguous
reverse submissions remain pending for reconciliation. Paper simulation and
isolated replay use the deferred workflow as well.

Validation for this pass on 2026-09-15: all **328 Django tests passed** in
167 seconds, including the strategy, connector, preflight and replay tests.
System checks, `makemigrations --check --dry-run`, and `git diff --check` passed.
Captured output: `.runtime/score-flip-final-tests.log`. Gold risk, stop envelope,
timeframes, five-strategy pool, hybrid exits and trading windows are unchanged.
The subsequent refinement pass below implements relative volume and configured
spread selection. Historical news replay remains deferred.

Operational status: a requested Safety CI retry still failed before either job
started because GitHub reports an account billing lock. That external issue
must be resolved before protected CI can pass and PR #2 can be merged. Once
merged, verify the personal bot's frozen preset explicitly before starting the
separate forward-demo baseline with opposite scalp disabled. Review score
distributions, selection/rejection frequency and realized per-strategy outcomes
before a separate scalp A/B trial. This implementation places no demo/live orders
and does not change the personal bot's settings.

### Gold volume and spread refinements

Gold Momentum Ignition and Breakout Retest now require signal tick volume to be
at least the median of the preceding 20 candles (`min_relative_volume=1`). The
signal candle and its pullback/retest candle are excluded from that baseline.
The volume component of setup quality uses the same ratio, so multiplying all
tick counts by a feed-specific constant preserves eligibility and score.
Missing, negative or nonfinite volume, insufficient history and zero-median
baselines produce explicit skips. Successful and low-volume decisions retain
the ratio, median, lookback and threshold for review.

Gold's relative-volume rule is enabled by the explicitly applied detector
overrides. Existing frozen presets retain their absolute-volume thresholds
until recommendations are reapplied; the shared builder does not silently
upgrade their behavior. Legacy absolute volume fields are inactive in relative
mode. Other assets retain their existing absolute-volume defaults. Both the
backtest API's saved detector configuration and full bot replay use the same
builder.
Migration `0053_gold_relative_volume` records these defaults in Gold's asset
recommendation, preserving explicit relative-volume overrides and all frozen
bot settings. The catalog version becomes 4; other asset recommendations keep
their previous values. The migration has only been run in isolated test databases.

Gold's selector now treats spread as wide at 80% of the effective allowance:
the minimum positive bot limit and active symbol-profile limit, converted to
price using broker point/digits and the current quote. The run context records
the allowance and ratio. Gold no longer guesses an allowance from 0.10% of
price when configuration is unavailable. Existing execution spread checks
remain authoritative; strategy preference does not permit execution above a
limit. Other assets retain their existing selector behavior.

This pass leaves risk, structural stops, strategy weights, timeframes, strategy
pool, exits, trading windows and personal bot settings unchanged. No demo or
live orders were placed. The 1x volume and 80% spread settings are transparent
defaults, not empirical profitability calibration. Historical USD news remains
`not_simulated`; forward-demo outcomes and a separate opposite-scalp comparison
remain follow-up work.

Validation on 2026-09-15: all **338 Django tests passed** in 173 seconds.
System checks, `makemigrations --check --dry-run`, and whitespace checks passed.
Coverage includes feed-scale invariance, rolling-median outlier handling,
unusable volume data, saved Gold replay settings, the actual scalper selector
context, spread-unit conversion and stricter bot/profile limits. The first full
run exposed a catalog/migration mismatch; migration 0053 fixed it before this
successful rerun. Output: `.runtime/gold-volume-spread-final-tests.log`.

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

## Trade History filters and saved performance baselines

Trade History now offers account selection, All/Gold/BTC/ETH/Forex tabs, bot,
symbol, strategy, UTC close-date and entry-preset filters. Summary and strategy
breakdown totals cover the entire matching recorded history; pagination only
limits the displayed rows. Opposite-scalp totals use the same selection.
An empty sample has undefined win rate and profit factor. Gold views include
all five strategies even before their first closed outcome.

Saved baselines preserve a name, UTC start time and account/owner-scoped filter
set. Baseline results require a recorded entry time at or after the marker,
excluding preexisting positions that close later. Additional filters can narrow
a baseline but cannot broaden its saved scope. Saving a marker does not delete
history, reset account state, change bot settings or start trading. The marker
name is user supplied; it is not verification of the running code revision.

New automated entry orders snapshot strategy, applied preset version and scalp
status. Exit attribution follows the originating entry/position, never the
bot's current preset. Older versions without a snapshot remain unknown; known
historical entry strategies remain available. Multiple entries sharing a
position receive mixed attribution. Realized exits are grouped by position
ticket; known open positions and positions with missing recorded exit results
are excluded. Legacy outcomes without a position record explicitly show
unverified completion. P/L uses the existing realized ledger; missing broker
charges are not estimated by this view.

Migration `execution.0061_performance_history_baselines` adds entry attribution
and baseline storage without backfilling current versions onto historical
orders. It has only been exercised in isolated test databases. This work does
not pause BTC/ETH/EURUSD, enable Gold, disable scalp on a running bot, create a
personal baseline, or start demo/live orders. Those account operations remain
separate from this code change. No Gold strategy or risk parameters changed.

Validation completed across 2026-09-15/16: the full **348-test Django suite**
passed, then the **11 final history regressions** passed after the final missing
result and attribution adjustments. All **38 Flutter tests** passed, including
compact layouts and filter/baseline state; Flutter analysis reported no issues.
Django system checks, migration checks and whitespace checks passed. Logs:
`.runtime/performance-history-backend-tests.log`,
`.runtime/performance-history-final-regressions.log`, and
`.runtime/performance-history-flutter-tests.log`.

## Entry configuration and build attribution

New automated entries record a versioned configuration snapshot and SHA-256
fingerprint, execution timeframe, recommendation state, and startup build
identity. The fingerprint covers the bot's strategy selection, detector tuning,
scalper profile, schedule and bot risk settings. Numeric representations and
unordered bot allowlists are normalized. Display names, current prices, P/L,
bot start/stop state and recommendation catalog updates are excluded. Shared
account policies, external services and broker conditions are not represented
by this bot configuration fingerprint.

Source processes capture their revision once when settings load. Only a clean
repository rooted at the application directory produces a revision; dirty or
unavailable checkouts remain unknown. Desktop packaging writes the same identity
to a bundled manifest, which packaged processes read without consulting Git on
the destination machine. Restart source processes after code changes; this is
startup attribution, not a continuous integrity check or signed build attestation.

History uses the entry snapshot after bot edits, process restarts and preset
changes. Legacy records are not backfilled; positions with mixed entries have
unknown configuration/build attribution. Configuration and build filters apply
to the entire report and can be pinned in saved baselines. Invalid fingerprints
and revisions are rejected by both history and baseline APIs.

The desktop history screen exposes both filters and the full entry hashes in
trade tooltips. Selecting a bot shows its current configuration as a preview
for future entries. Use current configuration selects that bot's primary symbol,
fingerprint and verified revision, when available. If the build is unknown, it
explicitly leaves the build filter at All builds. Baselines store the selected
filters; their names do not verify a revision. Preview settings never replace
historical snapshots.

Validation on 2026-09-21: all **365 Django tests** and **41 Flutter tests**
passed; Flutter analysis reported no issues. Django system checks, migration
checks, desktop build-spec syntax and whitespace checks passed. The full
Django run emitted fixed-time-default warnings for the existing 18:00 trading
window fields at startup; the separate system check reported no issues.
The focused identity/history suite passed 27 tests, including manifest handling,
frozen attribution, malformed configurations and baseline scope. Logs are
`.runtime/identity-full-backend-tests.log`,
`.runtime/identity-full-flutter-tests.log`, and
`.runtime/identity-regression-tests.log`. No distributable was built and no
demo/live orders were submitted during validation.

## Gold scan diagnostics and trading-window enforcement

The 21 September investigation found that Gold's 18 September scans stopped
on neutral or conflicting M15/H1 direction. Valid neutral analysis was reported
as `htf_bias_unavailable`, and scans outside the configured trading windows
could report detector rejections before the decision layer checked the schedule.

The context analyzer now retains every configured frame's analysis and reports
valid neutral direction as `htf_bias_neutral`. Missing or malformed analysis
remains `htf_bias_unavailable`; conflicting directions remain
`htf_context_conflict`. Run summaries distinguish `neutral`, `unavailable`,
`conflict`, and unsupported context timeframes. All configured frames still
need an agreeing directional bias before detector evaluation.

Scans use the existing timezone-aware bot schedule before fetching candles or
evaluating detectors. A closed window records `outside_trading_window`, the
checked time, and the configured schedule, with higher-timeframe and strategy
evaluation marked as not performed. The decision-stage schedule check remains
in place. The same shared task is used by isolated bot replay. Risk settings,
strategy thresholds and configured trading hours are not altered by this fix.

Focused validation covers the recorded neutral-H1 case, missing context,
conflicting directions, all-frame evidence, Gold's post-close restart time,
the next London window, and disabled schedule enforcement. Source desktop
workers must restart to load these changes.

Validation on 2026-09-21: all **46 focused regressions** and the complete
**372-test Django suite** passed. Django system checks, migration checks and
whitespace checks passed. Tests used an isolated in-memory database. Logs:
`.runtime/gold-gate-fix-focused.log` and `.runtime/gold-gate-fix-full.log`.

## Automatic trading-window pauses (2026-09-22)

The schedule guard checks active bots every 30 seconds. Outside a bot's configured
windows it sets `status=paused` and `schedule_paused=true`. It resumes only pauses
owned by the schedule, inside an allowed window, with an active asset/account,
an open market calendar, no outstanding cooldown, and no account entry block or
emergency stop. Disabling the schedule releases its own pause on the next check.
Manual pauses/stops, app shutdown, account stops and loss-streak pauses cancel
automatic resuming. Stopped bots are never started by the schedule guard.

Single and multiple windows share overnight weekday handling; an overnight
session belongs to its starting day. Each window uses its configured IANA
timezone, including daylight-saving changes. The market-hours guard respects
these windows and user controls, preserves cooldowns, and runs on the serialized
MT5 queue. Position monitoring, trailing stops and exits continue while entries
are paused. Risk percentages, presets and trading windows are not rewritten.

The desktop shows **SCHEDULE PAUSED**, refreshes the bot list every 15 seconds,
and offers **Keep paused** to cancel automatic resuming. Settings updates lock and
re-read the bot so a stale form cannot overwrite a concurrent pause. Migration
`bots.0054_bot_schedule_paused` initializes existing bots without changing status.

Validation: the complete Django suite passed (391 tests), followed by 42 focused
schedule, control, settings-race and preset tests after the final fixes. All 42
Flutter tests and Flutter analysis passed. Migration and whitespace checks passed.
Logs: `.runtime/schedule-full-tests.log`, `.runtime/schedule-final-focused-tests.log`,
`.runtime/schedule-flutter-tests.log`, `.runtime/schedule-flutter-analyze.log`.

Runtime adoption: migration applied and all four supervised backend children
reloaded while Flutter and MT5 stayed open. At 22:53:04 UTC the periodic task
paused Gold with no errors. Gold retained its approved 3% risk; Bitcoin remained
stopped at 0.25%. The before/after receipts are
`.runtime/schedule-before-reload.json` and `.runtime/schedule-after-reload.json`.
The running Flutter app was hot reloaded; its VM confirmed that the schedule
label, Keep paused control and automatic refresh code were loaded. Later
30-second guard runs were idempotent and reported no errors.

## BTC entry quality and structural protection (2026-09-22)

Review of the nine recorded filled BTC entries found stop distances of about
0.048%–0.127%, below the bot's existing 0.35% minimum. Detector signals bypassed
the planning path, and the decision/submission structural-stop checks applied
only to Gold. Both paths now enforce BTC's configured envelope (currently
0.35%–0.90%), using the current quote again at submission. Invalid structural
stops cause a rejection; they are never moved to manufacture an eligible trade.
The sample includes winners as well as losses and does not establish that
tight stops caused every loss.

BTC preset version 5 requires the next completed directional candle to close
beyond a momentum/trend pullback's extreme. A confirmation candle that touches
the original stop invalidates the setup even if its close recovers. The setup's
structural stop and quality score are retained; its target is recalculated from
the confirmed entry at the strategy's reward/risk multiple. The trigger is
checked again against the live quote before submission. Breakout retests require
a directional breakout and recovery candle, and their stops protect both the
breakout and retest wicks. Momentum and breakout volume use the median of 20
preceding completed bars, excluding the impulse/retest/confirmation bars as
appropriate, instead of the broker-dependent absolute count of 80 ticks.

BTC automatic selection now uses directional H1 structure/slope or ATR expansion
to enable momentum and breakouts. Quiet conditions retain confirmed trend
pullbacks. Near the effective spread allowance, selection excludes momentum and
breakouts. If the configured pool has no suitable strategies, the task records
`no_suitable_strategies`; fallback cannot restore the excluded detectors.
The M15/H1 agreement requirement, unrestricted BTC schedule, score threshold,
loss-streak controls, position management and risk percentage remain in force.
Gold detector defaults and its applied snapshot are unchanged.

Migration `bots.0055_btc_entry_quality` adds the new BTC recommendation fields
without replacing custom tuning or any bot's applied snapshot. Existing bots
need explicit adoption of the new detector flags. Other asset recommendations
receive only the catalog version increment; their configuration is unchanged.

An offline detector scan used 3,000 saved completed M5 candles (2,901 rolling
100-bar evaluations per strategy). Raw momentum setups fell from 727 to 71,
breakout retests from 69 to 33, and trend pullbacks from 163 to 41. After the
unchanged score threshold and stop envelope, the new detectors yielded 16, 10
and 1 candidates respectively. These are detector observations, not executed
trades or profitability results; context, sizing, spreads and position state
can reject them later. Evidence is in `.runtime/btc-entry-audit-before.json`
and `.runtime/btc-entry-audit-proposed.json`.

Read-only broker verification found equity of $475.94 and a 0.25% BTC budget
of $1.18985. At the observed quote, the 0.01 minimum lot would risk about $3.01
at the 0.35% stop floor. Entries may therefore be correctly rejected even with
a valid setup. Risk and stops must not be changed to force a minimum lot.
The estimate uses MetaTrader's account-currency
[order_calc_profit](https://www.mql5.com/en/docs/python_metatrader5/mt5ordercalcprofit_py),
with no orders submitted. Broker evidence is `.runtime/btc-broker-evidence.json`.

Validation: all **411 tests in the complete Django suite** passed, plus **9 bot
control/client API tests** located outside normal discovery. Django system,
migration and whitespace checks passed. The tests include confirmed buy/sell
entries, stop-breach recovery rejection, relative-volume scale invariance,
breakout wick protection, selector fallback prevention, decision and quote-time
stop validation, minimum-lot budget rejection, preset migration preservation,
and unchanged Gold/position-management behavior. Logs are
`.runtime/btc-full-tests.log` and `.runtime/btc-bot-api-tests.log`.

Runtime adoption completed on **2026-09-23 at 00:09 UTC**: migration 0055 is
applied, and only the new BTC detector tuning and its snapshot version were
adopted for bot 7. The four supervised backend children reloaded while Flutter
and MT5 remained open. MT5 reconnected and the scheduled BTC scan completed,
recording `htf_bias_neutral`; no qualifying live entry was forced for validation.
BTC remained active at 0.25% risk; Gold retained its schedule-owned pause and
3% risk. There were no open positions or pending entries at reload. Receipts:
`.runtime/btc-adoption-applied.json`, `.runtime/btc-before-reload.json`,
`.runtime/btc-after-reload.json`, and `.runtime/btc-health-after-reload.json`.

## Remaining safety audit findings (2026-09-23)

The failed implementation findings from the supplied checklist are addressed:

| Finding | Implemented behavior |
| --- | --- |
| Desktop enables MT5 Algo Trading | Removed keyboard/window automation and its enabling setting. Entry submission checks the operator's switch; login and read-only monitoring do not change it. |
| Per-bot floating-loss settings unused | Fresh, exact-ticket owned profit plus swap is checked against the allocation, or account balance when allocation is zero. A breach latches the bot stopped, cancels its entries and requests owned exits. Explicit Start clears the latch; scheduling cannot. |
| Partial and ambiguous volume escapes limits | One shared exposure calculation reserves pending remainders and unsynchronized fills without a time expiry. Missing positions retain capacity until broker evidence resolves them. |
| Aggregate lots can exceed the configured ceiling | Final admission serializes different bots on the account row and includes durable reservations alongside known positions. Concurrent PostgreSQL tests enforce both lot and position caps. |
| Performance identity omits effective controls | Schema 2 includes static account RiskPolicy limits and runtime execution controls. Final admission refreshes the snapshot and retains the original decision fingerprint when it differs. Filled history is preserved. |
| Ineffective controls fragment performance | Disabled loss thresholds and unused runtime offsets/early-exit controls are excluded. Enabled per-bot loss controls now have runtime enforcement. |
| CI lacks PostgreSQL locking coverage | CI runs complete backend suites on both databases. The existing required `backend-safety` check requires both jobs to succeed; the PostgreSQL job fails if it silently uses SQLite. |
| Runbook uses an obsolete flatten setting | The runbook uses `close_positions_on_emergency_stop` and explains the separate bot loss stop, manual Algo Trading switch, recovery and release conditions. |

Cancellation acknowledgement alone does not release capacity: removal can race
another fill. Exact-ticket terminal order history must account for all filled
volume first. The distinction follows MetaTrader's documented
[trade return codes](https://www.mql5.com/en/docs/constants/errorswarnings/enum_trade_return_codes)
and [order states](https://www.mql5.com/en/docs/constants/tradingconstants/orderproperties).
Reservations survive delayed deal reporting, and repeated reconciliation can
release a confirmed canceled remainder without resending the cancellation.

Runtime inspection also found 16 old positions marked `missing`. Read-only MT5
verification at 08:24 UTC showed no open positions or pending orders and matched
complete entry/exit history for all 16 tickets. Reconciliation now retries missing
records, repairs already-recorded closes without duplicate fills, and records
balanced broker-history evidence for positions whose originating bot was deleted.
It does not attribute those deleted bots' trades to current bots. Missing history,
partial closure and netting reversal history continue to reserve exposure.
Evidence: `.runtime/audit-broker-history.json`.
Historical fills retain their broker execution timestamp. Importing an earlier
day's result, or a result preceding an already-recorded exit, does not rewrite
the bot's current loss streak or cooldown.

Migration `bots.0056_bot_kill_switch_triggered_at` was applied by the desktop
launcher at 01:02 UTC. It adds only the persistent loss-stop timestamp. Runtime
inspection confirmed Gold at 3% risk and BTC at 0.25%; both were active within
their windows. No risk or MT5 Algo Trading setting was changed for this audit.

Validation: the complete PostgreSQL backend and additional bot API suite passed
**448 tests**, including real concurrent admission transactions. Following the
final historical-fill timestamp/state change, **93 SQLite** and **96 PostgreSQL**
regression tests passed. The earlier complete SQLite suite passed 432 tests
(two PostgreSQL-only skips), followed by 167 audit/control regression tests
(the same two expected skips). System, migration and whitespace checks passed;
the CI YAML and required-check dependencies were validated. Logs:
`.runtime/audit-complete-postgres.log`, `.runtime/audit-final-sqlite.log`,
`.runtime/audit-history-final-sqlite.log`, and
`.runtime/audit-history-final-postgres.log`. Deployment verification receipts
are written to `.runtime/audit-before-reload.json`,
`.runtime/audit-after-reload.json`, and `.runtime/audit-reconciliation-result.json`.

The unattended live-trading release gate remains **not approved**. Automated
tests and read-only broker-history reconciliation do not establish broker fault
recovery under active trading. The supervised demo partial-fill, connection-loss,
cancellation/flatten-retry and host-recovery checks in `LIVE_TRADING_RUNBOOK.md`
remain required before that separate release decision.
