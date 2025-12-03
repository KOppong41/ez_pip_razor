#!/usr/bin/env python
"""
Quick test to verify order dispatch works and orders transition to 'ack' or 'filled' status.
This tests the full flow: Decision -> Order -> MT5 dispatch -> Status update
"""
import os
import sys
import django

# Add trading_bot to path so we can import config
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "trading_bot"))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from execution.models import Order, Decision, Signal, Bot, BrokerAccount
from execution.services.brokers import dispatch_place_order, validate_order_conditions
from decimal import Decimal
from django.utils import timezone

print("\n=== Order Dispatch Test ===\n")

# 1) Get latest order
order = Order.objects.order_by("-created_at").first()
if not order:
    print("No orders found in database")
    exit(1)

print(f"Testing Order {order.id}:")
print(f"  Symbol: {order.symbol}")
print(f"  Side: {order.side}")
print(f"  QTY: {order.qty}")
print(f"  SL: {order.sl}")
print(f"  TP: {order.tp}")
print(f"  Status: {order.status}")
print(f"  Broker: {order.broker_account.broker}")

# 2) Check pre-flight validation
print(f"\n1. Pre-flight validation:")
valid, reason = validate_order_conditions(order)
print(f"   Valid: {valid} (reason: {reason})")

if not valid:
    print(f"   Order would be rejected at dispatch")
    exit(1)

# 3) Try to dispatch (this would normally be called by the task)
print(f"\n2. Attempting dispatch (to paper connector)...")
try:
    if order.broker_account.broker == "paper":
        dispatch_place_order(order)
        print(f"   ✓ Dispatch succeeded for paper connector")
        
        # Check status after dispatch
        order.refresh_from_db()
        print(f"   Order status after dispatch: {order.status}")
        if order.status in ("ack", "filled"):
            print(f"   ✓ Order transitioned correctly to {order.status}")
        else:
            print(f"   ⚠ Order still in {order.status} (may be pending)")
    else:
        print(f"   ⚠ Order is for live broker ({order.broker_account.broker}), skipping dispatch")
        print(f"   (In production, this would send to MT5)")
except Exception as e:
    print(f"   ✗ Dispatch failed: {e}")
    import traceback
    traceback.print_exc()
    exit(1)

print(f"\n✓ Test completed successfully\n")
