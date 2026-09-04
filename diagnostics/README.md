# Operator diagnostics

These scripts perform opt-in integration checks and some can access the configured
database or MT5 terminal. They were moved out of Django test discovery because
importing an automated test must never submit, close, or modify a trade.

Review a script before running it. Execute from the repository root, for example:

```powershell
python -m diagnostics.harami_check
```
