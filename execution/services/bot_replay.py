"""Snapshot and launch the live bot pipeline in a separate, disposable process."""
import json
import os
import subprocess
import sys
import tempfile
from copy import deepcopy
from dataclasses import asdict
from pathlib import Path

from django.conf import settings
from django.core.serializers.json import DjangoJSONEncoder

from execution.models import ExecutionSetting, RiskPolicy, default_scalper_profile_config
from execution.services.historical_backtest import decimal_field, int_field, json_safe
from execution.services.runtime_config import get_runtime_config

MAX_BOT_REPLAY_BARS = 2000


def model_snapshot(instance):
    return {
        field.name: deepcopy(field.value_from_object(instance))
        for field in instance._meta.concrete_fields
        if not field.is_relation and not field.primary_key
        and not getattr(field, "auto_now", False) and not getattr(field, "auto_now_add", False)
    }


def configure_bot_replay(bot, config, data):
    if bot.engine_mode != "scalper":
        raise ValueError("Bot pipeline replay requires a scalper bot.")
    if not bot.broker_account_id:
        raise ValueError("Bot pipeline replay requires an account risk policy.")
    for name in ("volume_min", "volume_max", "volume_step", "margin_per_lot"):
        config[name] = decimal_field(data, name, positive=True)
    config["stops_level_points"] = decimal_field(data, "stops_level_points", "0")
    config["digits"] = int_field(data, "digits", None, 0, 10)
    if config["volume_min"] > config["volume_max"]:
        raise ValueError("Minimum volume cannot exceed maximum volume.")
    if config["timeframe"] not in {"1m", "5m", "15m"}:
        raise ValueError("Bot replay needs 1m, 5m or 15m candles to construct completed 15m context.")
    from execution.services.scalper_config import build_scalper_config, resolve_scalper_execution_timeframe
    resolved = resolve_scalper_execution_timeframe(bot, bot.asset.symbol, config["timeframe"], config=build_scalper_config(bot))
    if resolved != config["timeframe"]:
        raise ValueError("Choose an execution timeframe allowed by this bot and its scalper profile.")
    policy = RiskPolicy.objects.filter(broker_account=bot.broker_account).first() or RiskPolicy()
    execution_settings = ExecutionSetting.objects.first()
    snapshot = {
        "bot": model_snapshot(bot),
        "asset": {key: deepcopy(getattr(bot.asset, key)) for key in ("symbol", "category", "min_qty", "recommended_config", "recommended_config_version")},
        "profile": bot.scalper_profile.get_config() if bot.scalper_profile_id else default_scalper_profile_config(),
        "policy": model_snapshot(policy),
        "runtime": asdict(get_runtime_config()),
        "execution_settings": model_snapshot(execution_settings) if execution_settings else {},
        "broker_timezone": bot.broker_account.timezone,
        "news_enabled": bool(getattr(settings, "ECONOMIC_CALENDAR_ENABLED", False)),
    }
    config["bot_snapshot"] = json.loads(json.dumps(snapshot, cls=DjangoJSONEncoder))
    config["model_version"] = 2
    return config


def run_bot_replay(bars, config, dataset, symbol):
    """No database switching or provider patching takes place in the web process."""
    if dataset["last_index"] - dataset["first_index"] + 1 > MAX_BOT_REPLAY_BARS:
        raise ValueError(f"Bot replay supports at most {MAX_BOT_REPLAY_BARS} test candles per run.")
    payload = json.dumps(json_safe({"bars": bars, "config": config, "dataset": dataset, "symbol": symbol}))
    environment = os.environ.copy()
    environment.update({
        "DJANGO_SETTINGS_MODULE": "config.settings_replay", "EZSCALPER_REPLAY_WORKER": "1",
        "DATABASE_URL": "sqlite:///:memory:", "ALLOW_SQLITE_DESKTOP": "true",
        "DJANGO_DEBUG": "false", "DJANGO_SECRET_KEY": "disposable-replay-only",
        "PYTHONIOENCODING": "utf-8",
    })
    with tempfile.TemporaryDirectory(prefix="eztrade-replay-") as directory:
        input_path, output_path = Path(directory) / "input.json", Path(directory) / "result.json"
        frozen = getattr(sys, "frozen", False)
        if frozen:
            # Windowless packaged Python has no stdin/stdout. The backend's
            # dedicated service branch starts replay before loading live config.
            input_path.write_text(payload, encoding="utf-8")
            command = [sys.executable, "--service", "bot-replay", "--replay-input", str(input_path), "--replay-output", str(output_path)]
        else:
            command = [sys.executable, "-m", "execution.services.bot_replay_worker"]
        completed = subprocess.run(
            command, input=None if frozen else payload, text=True, encoding="utf-8", capture_output=True,
            cwd=str(Path(__file__).resolve().parents[2]), env=environment, timeout=240,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if completed.returncode:
            raise ValueError("Bot replay failed: " + (completed.stderr or "Worker exited without a result")[-1000:])
        return json.loads(output_path.read_text(encoding="utf-8") if frozen else completed.stdout)
