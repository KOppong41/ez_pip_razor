# Windows MT5 Live-Trading Runbook

## Safety model

Broker-side SL and TP are primary protection. Background trailing, stale-exit,
reconciliation, and kill-switch jobs are secondary controls and depend on the
Windows host, MT5 terminal, database, queue transport, and worker all remaining healthy.

An emergency stop performs this sequence on the serialized MT5 worker:

1. Disable new entries and stop the account's bots.
2. Cancel local entries that were never submitted.
3. Request broker cancellation for submitted/partial entry orders.
4. For each bot with `close_positions_on_emergency_stop` enabled, create
   exact-ticket exits for its durably attributed `ez_trade` positions.

Manual and unknown positions are never modified or flattened automatically.

Each bot's `kill_switch_enabled` and `kill_switch_max_unrealized_pct` independently
enforce its floating-loss stop. Fresh broker profit plus swap is compared with
the configured allocation, or broker account balance if the allocation is zero.
A breach stops that bot, cancels its outstanding entries, and closes its owned
positions regardless of the separate account-emergency flatten option. The stop
is latched so failed exits can retry even if prices recover. Only an explicit
operator Start clears the latch; schedule guards cannot restart it.

Outstanding entry volume remains reserved until broker terminal evidence is
available. Partial fills reserve the unfilled remainder as well as any fill not
yet represented by a broker position. Ambiguous submissions and missing position
snapshots do not expire out of the hard exposure limits. An unresolved order
may therefore block new entries until reconciliation proves its outcome.

## Start

From an activated project `.venv` on the Windows MT5 host:

```powershell
python manage.py migrate
python manage.py check
python -m celery -A config worker --loglevel=info --queues=celery
python -m celery -A config worker --loglevel=info --queues=mt5_execution --pool=solo --concurrency=1 --prefetch-multiplier=1
python -m celery -A config beat --loglevel=info
```

The desktop launcher starts the Waitress backend and its dedicated workers for
the supported personal-desktop topology. Never start a second consumer of the
`mt5_execution` queue.

## Before enabling entries

1. Confirm the displayed login/server/account mode matches the intended account.
2. Enable MT5 Algo Trading manually when ready and confirm expected symbols are
   visible. The app never sends Ctrl+E or changes that switch, including after
   reconnection. Read-only monitoring remains available when trading is disabled.
3. Confirm fresh ticks and a healthy `mt5_execution` worker heartbeat.
4. Review open broker positions and ownership classification.
5. Confirm daily-loss and drawdown baselines are locked from valid broker data.
6. Review each bot's `close_positions_on_emergency_stop` setting and floating-loss
   limit independently of the account's capital/exposure limits.

## Incident response

- Use emergency stop first. It is prioritized ahead of ordinary order work.
- Inspect cancellation failures and ambiguous execution attempts before retrying.
- Reconcile broker orders/deals/positions; broker state is authoritative.
- If Redis publishing fails, the order retains `new` status, clears the queued
  timestamp, and records `dispatch_publish_failed_at` plus the error.
- Do not manually resubmit an ACK, partial, or ambiguous order. Reconcile it.
- If the host is unhealthy, use the broker/MT5 terminal directly to manage risk.

## Recovery

Keep entries disabled until MT5 connectivity, fresh ticks, outstanding-order
reconciliation, broker-position reconciliation, and worker uniqueness are all
verified. Re-enable entries explicitly; recovery must never auto-enable trading.

## Release verification

Safety CI runs the complete backend suite on SQLite and PostgreSQL, including
real concurrent database transactions for exposure admission. Passing CI verifies
these implementation contracts; it does not approve unattended live trading.
That release gate remains closed pending supervised demo validation of broker
partial fills, lost connections, cancellation/flatten retries, and host recovery.
