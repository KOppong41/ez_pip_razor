import os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
import django
django.setup()

from execution.models import Signal, Decision, Position
from datetime import timedelta
from django.utils import timezone

print('=' * 70)
print('SCALPER ENGINE DEEP DIVE - SIGNALS vs DECISIONS')
print('=' * 70)

# Get all recent signals and their corresponding decisions
print('\nRecent Signals and their Decisions:')
print('-' * 70)

signals = Signal.objects.filter(source='scalper_engine').order_by('-received_at')[:20]
print(f'Total signals to analyze: {signals.count()}\n')

for sig in signals:
    decisions = Decision.objects.filter(signal=sig)
    if not decisions.exists():
        print(f'Signal {sig.id}: {sig.symbol} {sig.direction}')
        print(f'  Received: {sig.received_at}')
        print(f'  ✗ NO DECISION CREATED')
    else:
        for dec in decisions:
            print(f'Signal {sig.id}: {sig.symbol} {sig.direction}')
            print(f'  Received: {sig.received_at}')
            print(f'  Decision: {dec.action}')
            print(f'  Reason: {dec.reason}')
            print(f'  Score: {dec.score}')

print('\n' + '=' * 70)
print('OPEN POSITIONS CHECK:')
print('=' * 70)

positions = Position.objects.filter(status='open')
print(f'\nTotal open positions: {positions.count()}')
for pos in positions:
    print(f'\n  {pos.symbol} {pos.qty} (side: {"long" if pos.qty > 0 else "short"})')
    print(f'    Entry: {pos.avg_price} @ {pos.updated_at}')
    print(f'    SL: {pos.sl}, TP: {pos.tp}')
    print(f'    Created by: {pos.bot if pos.bot else "manual"}')

print('\n' + '=' * 70)
print('SCALPER PROFILE CHECK:')
print('=' * 70)

from execution.models import ScalperProfile

profiles = ScalperProfile.objects.all()
print(f'\nScalper profiles: {profiles.count()}')
for prof in profiles:
    print(f'\n  {prof.name}')
    print(f'    Config: {prof.config}')
