#!/usr/bin/env python
import os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
import django
django.setup()

from bots.models import Bot
from execution.models import ScalperProfile

print('='*70)
print('FIXING SCALPER BOT - REMOVING PROFILE CONSTRAINTS')
print('='*70)

# Get the scalper bot
bot = Bot.objects.filter(engine_mode='scalper').first()
if not bot:
    print('No scalper bot found')
    exit(1)

print(f'\nBot: {bot.name}')
print(f'Current scalper_profile: {bot.scalper_profile}')

if bot.scalper_profile:
    print(f'\nREMOVING scalper profile to allow all trades...')
    bot.scalper_profile = None
    bot.save()
    print(f'✓ Scalper profile removed')
    print(f'  Bot will now process ALL signals without scalper-specific risk blocks')
else:
    print('\n✓ No scalper profile attached (already unblocked)')

# Verify the change
bot.refresh_from_db()
print(f'\nVerification:')
print(f'  Scalper profile: {bot.scalper_profile}')
print(f'  Status: {bot.status}')
print(f'  Auto-trade: {bot.auto_trade}')

print('\n' + '='*70)
print('DONE - Bot will now process signals normally')
print('Existing ignored decisions may pass on next cycle')
print('='*70)
