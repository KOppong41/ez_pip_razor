import os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
import django
django.setup()

from django.conf import settings
from bots.models import Bot
from execution.models import Signal, Decision, Order

print('=' * 70)
print('SCALPER ENGINE QUICK DIAGNOSTIC (no MT5 calls)')
print('=' * 70)

# 1. Check if scalper-engine-45s is in schedule
print('\n1. CELERY BEAT SCHEDULE CHECK:')
if 'scalper-engine-45s' in settings.CELERY_BEAT_SCHEDULE:
    print('   ✓ scalper-engine-45s is IN the schedule')
    cfg = settings.CELERY_BEAT_SCHEDULE['scalper-engine-45s']
    print(f'     Task: {cfg.get("task")}')
    print(f'     Interval: {cfg.get("schedule")} seconds')
    print(f'     Args: {cfg.get("args")}')
else:
    print('   ✗ scalper-engine-45s is NOT in the schedule!')
    print('   Available tasks:')
    for key in settings.CELERY_BEAT_SCHEDULE.keys():
        print(f'     - {key}')

# 2. Check scalper bots exist and are configured
print('\n2. SCALPER BOT CHECK:')
bots = Bot.objects.filter(engine_mode='scalper', status='active', auto_trade=True)
print(f'   Active scalper bots with auto_trade=True: {bots.count()}')
for bot in bots:
    print(f'\n   Bot: {bot.name} (ID={bot.id})')
    print(f'     Asset: {bot.asset.symbol if bot.asset else "MISSING!"}')
    print(f'     Broker: {bot.broker_account}')
    print(f'     Strategies: {bot.enabled_strategies}')
    print(f'     Strategies count: {len(bot.enabled_strategies) if bot.enabled_strategies else 0}')

# 3. Check if ANY signals have been created (from any source)
print('\n3. SIGNAL GENERATION CHECK:')
total_signals = Signal.objects.count()
scalper_signals = Signal.objects.filter(source='scalper_engine').count()
print(f'   Total signals ever created: {total_signals}')
print(f'   Scalper engine signals: {scalper_signals}')

if total_signals > 0:
    latest = Signal.objects.latest('received_at')
    print(f'   Latest signal: {latest.source} {latest.symbol} {latest.direction} @ {latest.received_at}')

# 4. Check if any decisions have been made
print('\n4. DECISION CHECK:')
total_decisions = Decision.objects.count()
open_decisions = Decision.objects.filter(action='open').count()
print(f'   Total decisions: {total_decisions}')
print(f'   Decisions with action=open: {open_decisions}')

# 5. Check if any orders have been placed
print('\n5. ORDER CHECK:')
total_orders = Order.objects.count()
filled_orders = Order.objects.filter(status='filled').count()
print(f'   Total orders: {total_orders}')
print(f'   Filled orders: {filled_orders}')

# 6. Check why decisions are being ignored
print('\n6. DECISION ANALYSIS:')
ignored = Decision.objects.filter(action='ignore')
print(f'   Ignored decisions: {ignored.count()}')
if ignored.exists():
    print('   Reasons for ignoring:')
    reasons = {}
    for d in ignored:
        reason = d.reason or 'no reason given'
        reasons[reason] = reasons.get(reason, 0) + 1
    for reason, count in sorted(reasons.items(), key=lambda x: x[1], reverse=True):
        print(f'     - {reason}: {count}')

print('\n' + '=' * 70)
