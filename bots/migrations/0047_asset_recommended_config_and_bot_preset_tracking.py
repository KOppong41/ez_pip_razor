from django.conf import settings
import django.core.validators
from django.db import migrations, models
from decimal import Decimal


def seed_asset_recommendations(apps, schema_editor):
    Asset = apps.get_model("bots", "Asset")
    from core.asset_trading_presets import (
        ASSET_CATALOG,
        ASSET_PRESET_VERSION,
        ASSET_TRADING_PRESETS,
    )

    for symbol, metadata in ASSET_CATALOG.items():
        defaults = {
            "display_name": metadata["display_name"],
            "category": metadata["category"],
            "min_qty": metadata["min_qty"],
            "recommended_qty": metadata["recommended_qty"],
            "recommended_config": ASSET_TRADING_PRESETS[symbol],
            "recommended_config_version": ASSET_PRESET_VERSION,
            "is_active": True,
        }
        Asset.objects.update_or_create(symbol=symbol, defaults=defaults)


class Migration(migrations.Migration):
    dependencies = [("bots", "0046_bot_execution_risk_controls")]

    operations = [
        migrations.AddField(
            model_name="asset",
            name="recommended_config",
            field=models.JSONField(blank=True, default=dict, help_text="Administratively adjustable recommended settings for new bots using this asset."),
        ),
        migrations.AddField(
            model_name="asset",
            name="recommended_config_version",
            field=models.PositiveIntegerField(default=1, help_text="Version of the recommended bot configuration stored for this asset."),
        ),
        migrations.AddField(
            model_name="bot",
            name="trading_timezone",
            field=models.CharField(default=settings.TIME_ZONE, help_text="IANA timezone used for this bot's trading window, including DST transitions.", max_length=64),
        ),
        migrations.AddField(
            model_name="bot",
            name="asset_preset_version_applied",
            field=models.PositiveIntegerField(blank=True, help_text="Asset recommendation version explicitly applied to this bot.", null=True),
        ),
        migrations.AddField(
            model_name="bot",
            name="asset_preset_applied_at",
            field=models.DateTimeField(blank=True, help_text="When asset recommendations were last explicitly applied.", null=True),
        ),
        migrations.AlterField(
            model_name="bot",
            name="max_spread_points",
            field=models.DecimalField(decimal_places=4, default=Decimal("0"), help_text="Optional maximum entry spread in raw broker/MT5 points. Set to 0 to inherit the asset recommendation.", max_digits=12, validators=[django.core.validators.MinValueValidator(Decimal("0"))]),
        ),
        migrations.AlterField(
            model_name="bot",
            name="allowed_deviation_points",
            field=models.PositiveIntegerField(default=0, help_text="Optional maximum MT5 order deviation in raw broker points. Set to 0 to inherit the asset recommendation."),
        ),
        migrations.AlterField(
            model_name="bot",
            name="enabled_strategies",
            field=models.JSONField(blank=True, default=list, help_text="Allowed strategy pool in manual and automatic modes. In automatic mode the selector may choose only from this list. Empty means inherit the asset recommendations."),
        ),
        migrations.RunPython(seed_asset_recommendations, migrations.RunPython.noop),
    ]
