from django.db import migrations


def normalize_forex_units(apps, schema_editor):
    ScalperProfile = apps.get_model("execution", "ScalperProfile")
    Bot = apps.get_model("bots", "Bot")
    expected = {
        "EURUSD": (15, 5),
        "GBPUSD": (18, 6),
    }
    def normalize_config(raw_config):
        config = dict(raw_config or {})
        symbols = dict(config.get("symbols") or {})
        changed = False
        for symbol, (spread_value, slippage_value) in expected.items():
            if symbol not in symbols:
                continue
            entry = dict(symbols[symbol] or {})
            if (
                entry.get("max_spread_unit") == "pips"
                and entry.get("max_spread_points") == spread_value
            ):
                entry["max_spread_unit"] = "points"
                changed = True
            if (
                entry.get("max_slippage_unit") == "pips"
                and entry.get("max_slippage_points") == slippage_value
            ):
                entry["max_slippage_unit"] = "points"
                changed = True
            symbols[symbol] = entry
        if changed:
            config["symbols"] = symbols
            config["execution_unit_schema_version"] = 2
        return config, changed

    for profile in ScalperProfile.objects.all().iterator():
        config, changed = normalize_config(profile.config)
        if changed:
            profile.config = config
            profile.save(update_fields=["config"])

    for bot in Bot.objects.exclude(scalper_params={}).iterator():
        params, changed = normalize_config(bot.scalper_params)
        if changed:
            bot.scalper_params = params
            bot.save(update_fields=["scalper_params"])


class Migration(migrations.Migration):
    dependencies = [("execution", "0053_order_dispatch_publish_failure")]
    operations = [
        migrations.RunPython(normalize_forex_units, migrations.RunPython.noop),
    ]
