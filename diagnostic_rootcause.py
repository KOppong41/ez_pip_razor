import os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
import django
django.setup()

from execution.models import Signal, Decision, Position
from bots.models import Bot

print('=' * 70)
print('ROOT CAUSE ANALYSIS - WHY NO TRADES')
print('=' * 70)

# Get bot
bot = Bot.objects.filter(engine_mode='scalper').first()
print(f'\nBot: {bot.name}')
print(f'  Scalper profile: {bot.scalper_profile}')
print(f'  Scalper params: {bot.scalper_params}')
print(f'  Decision min score: {bot.decision_min_score}')

# Get latest decisions and their reasons
print('\n' + '=' * 70)
print('RECENT DECISIONS AND THEIR REASONS:')
print('=' * 70)

decisions = Decision.objects.order_by('-decided_at')[:20]
print(f'\nTotal decisions analyzed: {decisions.count()}\n')

reason_counts = {}
action_counts = {}

for d in decisions:
    action = d.action
    reason = d.reason or 'no reason'
    
    action_counts[action] = action_counts.get(action, 0) + 1
    reason_counts[reason] = reason_counts.get(reason, 0) + 1

print('ACTION SUMMARY:')
for action, count in sorted(action_counts.items()):
    print(f'  {action}: {count}')

print('\nREJECTION REASONS (most common):')
for reason, count in sorted(reason_counts.items(), key=lambda x: x[1], reverse=True):
    print(f'  {reason}: {count}')

# Get open position
print('\n' + '=' * 70)
print('CURRENT OPEN POSITION:')
print('=' * 70)

positions = Position.objects.filter(status='open')
if positions.exists():
    for pos in positions:
        print(f'\n{pos.symbol} {pos.qty}')
        print(f'  Price: {pos.avg_price}')
        print(f'  SL: {pos.sl}')
        print(f'  TP: {pos.tp}')
        print(f'  Updated: {pos.updated_at}')
else:
    print('\nNo open positions')

# Check if scalper_profile is blocking
print('\n' + '=' * 70)
print('SCALPER PROFILE CHECK:')
print('=' * 70)

if bot.scalper_profile:
    print(f'\nProfile exists: {bot.scalper_profile.name}')
    print(f'Config: {bot.scalper_profile.config}')
    
    # Check if the profile has daily symbol cap
    config = bot.scalper_profile.config or {}
    print(f'\nDaily symbol cap: {config.get("daily_symbol_cap")}')
    print(f'Scale-in allowed: {config.get("allow_scale_in")}')
    print(f'Timeframe blocks: {config.get("timeframe_blocks")}')
else:
    print('\nNO SCALPER PROFILE ASSIGNED!')
    print('This likely means all scalper-specific risk checks are being applied with defaults')
    print('which may be very restrictive.')

print('\n' + '=' * 70)
