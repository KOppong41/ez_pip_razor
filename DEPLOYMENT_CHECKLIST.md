# Deployment and Live-Trading Approval Checklist

## Current status: NOT APPROVED FOR UNATTENDED LIVE TRADING

Passing unit tests is a software-quality gate, not evidence that a strategy is
profitable or safe with real money. Do not infer win rate, expected return, or
drawdown from this repository's tests.

## Supported topology

- Django/Waitress desktop backend: Windows host.
- MetaTrader 5 terminal and Python package: the same Windows host and user session.
- Exactly one Celery worker may consume `mt5_execution`; it must use
  `--pool=solo --concurrency=1 --prefetch-multiplier=1`.
- Redis/Postgres/general workers may run as local services or in the development
  Docker stack. The Docker stack is not an MT5 execution host.
- cTrader and Exness Web connectors are disabled; only `mt5_local` and `paper`
  are selectable.

## Software release gate

- [ ] `python manage.py check` passes.
- [ ] `python manage.py makemigrations --check --dry-run` reports no changes.
- [ ] Full Django test discovery passes.
- [ ] Production settings pass `check --deploy` with production-only secrets.
- [ ] Database backup and tested restore procedure exist.
- [ ] Migrations are reviewed and applied before workers start.
- [ ] Redis persistence/monitoring and Postgres backup/monitoring are configured.
- [ ] No credentials are committed; `.env` uses strict `KEY=VALUE` lines.

## Broker/demo validation gate

- [ ] Account identity, server, currency, leverage, and demo/live mode are shown correctly.
- [ ] Algo trading is enabled manually in MT5; the application does not silently enable it.
- [ ] Symbol mappings, digits, point, volume min/max/step, stop level, freeze level,
      trade mode, and supported filling mode are verified for every enabled symbol.
- [ ] Disabled, long-only, short-only, close-only, and full symbol modes are tested.
- [ ] Entry, partial fill, rejection, cancellation, modify SL/TP, exact-ticket close,
      reconnect, and reconciliation are exercised on demo.
- [ ] Kill switch is tested with flattening both disabled and enabled.
- [ ] Manual/external positions are proven immune to automated close/modify actions.
- [ ] Queue outage produces a persisted publish failure and recovers without duplicate orders.
- [ ] Requested price, fill price, adverse slippage, latency, and spread are reviewed.

## Strategy/risk evidence gate

- [ ] Backtest includes spread, commission, swaps, slippage, latency, rejected orders,
      missing/stale data, and broker trading-hour constraints.
- [ ] Out-of-sample and walk-forward results are recorded; no test data is reused for tuning.
- [ ] Demo forward test covers the intended sessions and volatile/news conditions.
- [ ] Maximum per-trade risk, daily loss, account drawdown, concurrent exposure,
      symbol concentration, and allowed sessions have explicit signed-off values.
- [ ] Results remain acceptable under worse-cost and delayed-execution stress scenarios.
- [ ] Operator understands that stop-loss orders can fill beyond the requested price.

## Live activation

- [ ] Start at the smallest broker-supported size.
- [ ] `entries_enabled` is explicitly enabled for the intended account.
- [ ] Live-money account confirmation is explicit and recorded.
- [ ] Operator is present for the first sessions and can use the emergency control.
- [ ] Alerting covers worker heartbeat, MT5 disconnect, stale ticks, reconciliation
      failures, rejected orders, publish failures, and drawdown/kill-switch events.
- [ ] A rollback is performed with `git revert`; do not use destructive history rewrites.

See [LIVE_TRADING_RUNBOOK.md](LIVE_TRADING_RUNBOOK.md) for startup, shutdown, and
incident behavior.
