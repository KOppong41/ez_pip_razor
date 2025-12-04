# Scalper Engine – Quick Start Guide

## What Just Happened

You now have a **high-frequency scalper signal engine** that:
- ✅ Scans **M1 candles every 45 seconds**
- ✅ Runs **4 price-action strategies** (pinbar, trend pullback, doji, range reversion)
- ✅ Emits **Signal → Decision → Order** pipeline automatically
- ✅ Integrates with **existing risk management** (kill-switch, trailing stops, position limits)

## One-Minute Setup

### Step 1: Create a Scalper Bot
```bash
python manage.py setup_scalper_bot --symbol XAUUSDm --auto-trade
```

### Step 2: Start Celery (if not running)
```bash
# Terminal 1: Worker
celery -A config worker -l info

# Terminal 2: Beat scheduler
celery -A config beat -l info
```

### Step 3: Watch It Trade
- Open Django Admin → Execution → Signals
- You should see new `scalper_engine` signals appearing every 45-90 seconds
- Check Orders tab to see MT5 orders being placed

## Verify It's Working

### In Django Shell:
```python
from execution.models import Signal, Decision, Order
from datetime import datetime, timedelta

now = datetime.now()
recent = now - timedelta(minutes=5)

# Check signals
signals = Signal.objects.filter(source="scalper_engine", received_at__gte=recent)
print(f"Recent signals: {signals.count()}")

# Check orders
orders = Order.objects.filter(created_at__gte=recent)
print(f"Recent orders: {orders.count()}")

# Sample signal
if signals.exists():
    sig = signals.first()
    print(f"Signal: {sig.symbol} {sig.direction} via {sig.source}")
    print(f"Payload: {sig.payload}")
```

### In Celery Logs:
Look for messages like:
```
[ScalperTrade] bot=1 symbol=XAUUSDm strategies=4 signals=2 decisions=1 orders=1
```

## Configuration

### Bot Settings (Admin or API)

Edit your scalper bot in Django Admin → Bots → Bot to:
- **Enable/disable strategies** – uncheck ones you don't want
- **Adjust risk** – change `risk_max_concurrent_positions`, `kill_switch_max_unrealized_pct`
- **Tune entry/exit** – modify `default_tp_pips`, `default_sl_pips`

### Global Schedule (if you need to change it)

Edit `config/settings.py`, CELERY_BEAT_SCHEDULE section:
```python
"scalper-engine-45s": {
    "task": "execution.tasks.run_scalper_engine_for_all_bots",
    "schedule": 30.0,  # Change to 30 for faster, 60 for slower
    "args": ("1m", 100),  # M1 timeframe, 100-bar lookback
},
```

## What Gets Created

Each time a scalper signal is generated:

1. **Signal** – record of the strategy decision (deduplicated by bar)
2. **Decision** – risk/score validation (passes or fails based on bot config)
3. **Order** – if Decision passes, order created for MT5
4. **Execution** – when MT5 fills the order, price + qty recorded
5. **Position** – open position tracked for trailing stops / kill-switch

All linked together for full audit trail.

## Monitoring Dashboard

### Django Admin Shortcuts:

- **Signals** → Filter by `source="scalper_engine"` to see all strategy output
- **Decisions** → Filter by recent to see which signals passed/failed risk checks
- **Orders** → Filter by `status="filled"` to see executed trades
- **Positions** → Open positions currently being managed
- **Audit Log** → Drill into exact reason a signal was skipped/accepted

### Prometheus Metrics (if enabled):

```
# Signals generated per source
signals_ingested_total{source="scalper_engine"}

# Decisions made
decision_created_total{action="open|ignore"}

# Orders placed
orders_created_total{status="filled"}

# Task failures
task_failures_total{task="trade_scalper_strategies_for_bot"}
```

## Troubleshooting

### "No signals appearing"
1. Check bot `status="active"` ✓
2. Check bot `auto_trade=True` ✓
3. Check `enabled_strategies` is not empty ✓
4. Tail Celery worker: `celery -A config worker -l debug`

### "Signals but no orders"
1. Check Decision reason in Admin → Decisions
2. Likely: failed risk check (balance too low, max positions hit, correlation block)
3. Lower `decision_min_score` on bot or check `risk_max_concurrent_positions`

### "Orders not filling in MT5"
1. Check Order `status="new"` vs `"filled"`
2. Verify MT5 account is funded and logged in
3. Check spreads aren't too wide (> `asset.max_spread`)

## Next: Fine-Tuning

After 24 hours of trading, review:
1. **Win rate** – Desired: 50%+ for scalper
2. **Profit factor** – Desired: 1.5x+
3. **Max drawdown** – Adjust `kill_switch_max_unrealized_pct` if too aggressive
4. **Strategy mix** – Disable underperforming strategies

Adjust bot config and restart Celery Beat to pick up changes (no redeploy needed).

---

## Architecture Overview

```
Celery Beat          ← Runs every 45 seconds
    ↓
run_scalper_engine_for_all_bots()
    ↓ (for each SCALPER bot)
trade_scalper_strategies_for_bot()
    ├─ price_action_pinbar()
    ├─ trend_pullback()
    ├─ doji_breakout()
    └─ range_reversion()
        ↓ (for each match)
    Signal → Decision → Order → MT5 Execution
```

## Key Differences vs. Harami Engine

| Aspect | Harami | Scalper |
|--------|--------|---------|
| **Timeframe** | 5m–4h | 1m (tight) |
| **Signal Frequency** | Every 5 minutes | Every 45 seconds |
| **Strategies** | Pattern-based (candle shapes) | Price-action-based (wick/ema) |
| **Win Rate Target** | 35–45% (broader) | 50–60% (tighter) |
| **Risk per Trade** | 1–2% account | 0.5–1% account |
| **Use Case** | Swing/day trading | Scalping/intraday |

---

## You're All Set! 🚀

The scalper engine is now **live and ready to trade**. Monitor the first 100 trades, then tune based on performance.

Questions? Check `SCALPER_ENGINE_IMPLEMENTATION.md` for detailed docs.

**Happy trading!**
