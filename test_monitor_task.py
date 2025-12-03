#!/usr/bin/env python
import os, sys, django
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "trading_bot"))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from execution.tasks import monitor_positions_task
print("Testing monitor_positions_task...")
try:
    result = monitor_positions_task()
    print(f"✓ Task completed successfully: {result}")
except Exception as e:
    print(f"✗ Task failed: {e}")
    import traceback
    traceback.print_exc()
