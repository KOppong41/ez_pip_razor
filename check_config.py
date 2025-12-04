import os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
import django
django.setup()

from bots.models import Bot
from execution.services.scalper_config import build_scalper_config

bot = Bot.objects.filter(engine_mode='scalper').first()
if not bot:
    print('No scalper bot found')
    exit(1)

print('Bot:', bot.name)
print('Asset:', bot.asset.symbol)
print()

# Build the config
config = build_scalper_config(bot)
print('Scalper Config:')
print('  Default strategy profile:', config.default_strategy_profile)
print()

# Get XAUUSDm symbol config
symbol_cfg = config.resolve_symbol('XAUUSDm')
if symbol_cfg:
    print(f'Symbol config for XAUUSDm:')
    print(f'  Key: {symbol_cfg.key}')
    print(f'  Execution timeframes: {symbol_cfg.execution_timeframes}')
    print(f'  Allow countertrend: {symbol_cfg.allow_countertrend}')
else:
    print('No symbol config found for XAUUSDm!')
    
# Check what's in strategy_profiles
print()
print('Available strategy profiles:')
for name, prof in config.strategy_profiles.items():
    print(f'  {name}')
    if hasattr(prof, 'execution_timeframes'):
        print(f'    Execution TFs: {prof.execution_timeframes}')
