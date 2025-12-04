import os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
import django
django.setup()

from django.conf import settings
from bots.models import Bot
from execution.models import Signal, Decision, Order, Position
from datetime import timedelta
from django.utils import timezone

print('=' * 70)
print('SCALPER BOT DIAGNOSTIC REPORT')
print('=' * 70)

# 1. Check scheduler
print('\n1. CELERY BEAT SCHEDULE:')
schedule = settings.CELERY_BEAT_SCHEDULE
if 'scalper-engine-45s' in schedule:
    print('   ✓ scalper-engine-45s IS in schedule')
    print(f'     Task: {schedule["scalper-engine-45s"]["task"]}')
    print(f'     Schedule: {schedule["scalper-engine-45s"]["schedule"]}s')
else:
    print('   ✗ scalper-engine-45s NOT in schedule')

# 2. Check bot config
print('\n2. SCALPER BOT CONFIGURATION:')
bots = Bot.objects.filter(engine_mode='scalper')
if not bots.exists():
    print('   ✗ NO SCALPER BOTS FOUND')
else:
    for bot in bots:
        print(f'\n   Bot: {bot.name} (ID={bot.id})')
        print(f'     Status: {bot.status}')
        print(f'     Auto-trade: {bot.auto_trade}')
        print(f'     Asset: {bot.asset.symbol if bot.asset else "MISSING"}')
        print(f'     Strategies enabled: {len(bot.enabled_strategies) if bot.enabled_strategies else 0}')
        if bot.enabled_strategies:
            for s in bot.enabled_strategies:
                print(f'       - {s}')
        print(f'     Broker account: {bot.broker_account}')
        if bot.broker_account:
            print(f'       Account active: {bot.broker_account.is_active}')
            print(f'       Broker: {bot.broker_account.broker}')
        print(f'     Decision min score: {bot.decision_min_score}')
        print(f'     Max concurrent positions: {bot.risk_max_concurrent_positions}')
        print(f'     Max positions per symbol: {bot.risk_max_positions_per_symbol}')

# 3. Check recent signals
print('\n3. RECENT SIGNALS (last 24 hours):')
recent = Signal.objects.filter(received_at__gte=timezone.now() - timedelta(hours=24)).order_by('-received_at')[:10]
scalper_signals = recent.filter(source='scalper_engine')
print(f'   Total signals (all sources): {recent.count()}')
print(f'   Scalper engine signals: {scalper_signals.count()}')
if recent.exists():
    print(f'   Latest signal source: {recent.first().source}')
    print(f'   Latest signal symbol: {recent.first().symbol}')

# 4. Check recent decisions
print('\n4. RECENT DECISIONS (last 24 hours):')
decisions = Decision.objects.filter(decided_at__gte=timezone.now() - timedelta(hours=24)).order_by('-decided_at')[:10]
print(f'   Total decisions: {decisions.count()}')
if decisions.exists():
    open_decisions = decisions.filter(action='open').count()
    ignored_decisions = decisions.filter(action='ignore').count()
    print(f'   - Opened: {open_decisions}')
    print(f'   - Ignored: {ignored_decisions}')
    print('\n   Recent decisions:')
    for d in decisions[:5]:
        print(f'     Signal {d.signal.id}: {d.signal.source} {d.signal.symbol} {d.signal.direction}')
        print(f'       Action: {d.action}, Reason: {d.reason}')

# 5. Check recent orders
print('\n5. RECENT ORDERS (last 24 hours):')
orders = Order.objects.filter(created_at__gte=timezone.now() - timedelta(hours=24)).order_by('-created_at')[:10]
print(f'   Total orders: {orders.count()}')
if orders.exists():
    filled = orders.filter(status='filled').count()
    new = orders.filter(status='new').count()
    ack = orders.filter(status='ack').count()
    print(f'   - Filled: {filled}')
    print(f'   - Acknowledged: {ack}')
    print(f'   - New: {new}')

# 6. Check recent positions
print('\n6. OPEN POSITIONS:')
positions = Position.objects.filter(status='open')
print(f'   Total open: {positions.count()}')
if positions.exists():
    for p in positions[:5]:
        print(f'     {p.symbol} {p.qty} @ {p.avg_price} (opened {p.created_at})')

# 7. Check if any candle fetching issues
print('\n7. CANDLE DATA CHECK:')
print('   Testing candle fetch for each scalper bot...')
from execution.services.marketdata import get_candles_for_account
for bot in bots:
    try:
        if bot.asset and bot.broker_account:
            print(f'\n   Bot: {bot.name}')
            print(f'     Symbol: {bot.asset.symbol}')
            print(f'     Broker: {bot.broker_account}')
            candles = get_candles_for_account(
                broker_account=bot.broker_account,
                symbol=bot.asset.symbol,
                timeframe='1m',
                n_bars=10
            )
            if candles:
                print(f'     ✓ Got {len(candles)} candles')
                print(f'       Latest close: {candles[-1]["close"]}')
                print(f'       Latest time: {candles[-1]["time"]}')
            else:
                print(f'     ✗ Got 0 candles (None returned)')
    except Exception as e:
        print(f'     ✗ Error: {str(e)[:100]}')

print('\n' + '=' * 70)
print('DIAGNOSTIC COMPLETE')
print('=' * 70)
