# Windows MT5 Live-Trading Runbook

## Safety model

Broker-side SL and TP are primary protection. Background trailing, stale-exit,
reconciliation, and kill-switch jobs are secondary controls and depend on the
Windows host, MT5 terminal, database, Redis, and worker all remaining healthy.

An emergency stop performs this sequence on the serialized MT5 worker:

1. Disable new entries and stop the account's bots.
2. Cancel local entries that were never submitted.
3. Request broker cancellation for submitted/partial entry orders.
4. If `emergency_close_owned_positions` is enabled, create exact-ticket exits
   for open positions whose ownership is `ez_trade`.

Manual and unknown positions are never modified or flattened automatically.

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
2. Confirm MT5 AutoTrading is enabled and the expected symbols are visible.
3. Confirm fresh ticks and a healthy `mt5_execution` worker heartbeat.
4. Review open broker positions and ownership classification.
5. Confirm daily-loss and drawdown baselines are locked from valid broker data.
6. Confirm the emergency flatten policy is set to the intended value.

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
