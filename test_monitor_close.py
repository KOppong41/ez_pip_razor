#!/usr/bin/env python
"""
Test monitor position close flow to verify the fix for pos.orders AttributeError
"""
import os
import sys
import django


sys.path.insert(0, os.path.join(os.path.dirname(__file__), "trading_bot"))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from execution.models import Position  # noqa: E402
from execution.services.monitor import create_close_order  # noqa: E402


print("\n=== Monitor Position Close Test ===\n")

# Get a test position
position = Position.objects.filter(status="open").first()
if not position:
    print("No open positions found to test")
    sys.exit(0)

print(f"Testing with Position {position.id}:")
print(f"  Symbol: {position.symbol}")
print(f"  QTY: {position.qty}")
print(f"  Broker Account: {position.broker_account.id}")

# Test create_close_order function
print("\n1. Creating close order (Decision)...")
try:
    decision = create_close_order(position)
    print("Decision created successfully")
    print(f"  Decision ID: {decision.id}")
    print(f"  Bot: {decision.bot}")
    print(f"  Action: {decision.action}")
    print(f"  Reason: {decision.reason}")
except Exception as e:
    print(f"Failed to create close order: {e}")
    import traceback

    traceback.print_exc()
    sys.exit(1)

print("\nTest completed successfully\n")
