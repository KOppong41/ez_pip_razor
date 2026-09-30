"""Upgrade unchanged v5 recommendations while preserving admin edits and bot snapshots."""

import hashlib
import json
from copy import deepcopy

from django.db import migrations


# SHA-256 of the canonical v5 JSON for each catalog symbol. A changed admin
# recommendation is left untouched rather than silently replaced.
V5_HASHES = {
    "AUDCADm": "cf7078baa43fbe7c2ad34c580f35def3cc9774381a52955999c6293c5420b862",
    "AUDJPYm": "78da8803c6a315d591b2523acc2feeb21e72834d555cd8cc09a204ee4272b761",
    "AUDNZDm": "9d5d0837e7a47ce226367de6c391f78b1010c18463d53ee81c9c3541ef9cedfe",
    "AUDUSDm": "17053a0f3e272b3644a58d52df18a666cfbf0d0f5aef23880b5ef4546e15c075",
    "BTCUSDm": "92e0770618463c92ff86ca232f640d95c4ca696c5cb0043277587e483001f3d3",
    "CADCHFm": "e86400e8fe789893732f9c7a60c8f2ec96ffd539e7d177ce4d8c04568beaac4c",
    "CADJPYm": "85949e30fe320e2cd22c94a1fd93ce77737166e60cf7eec97d3651ea710214f0",
    "ETHUSDm": "c063d39a370a481277f146d4959266e5cecd97fd78a8c59886a2bb998eba7e8a",
    "EURAUDm": "e1e3be5c2db8a03def7b0abc3adc7634623d8aefd9205b24f47fb987a433d612",
    "EURCADm": "c56173b3e8d9843ca50dbcb8109bc761e199dcdd7951ba0c9c771b3bf06103af",
    "EURCHFm": "c0deb69f4a2177025a415ed8201d8f2feea2931ee7bde49724c6eddeaf1c4df9",
    "EURGBPm": "c0d535bbbb289f7d83835df78e31e533d5454ead55826b85410026534167ac2f",
    "EURJPYm": "6e772d99d7fffbd5a1b8ed47a9f6a1ab0b0c787aab6c4467b7983833fa5026eb",
    "EURNZDm": "13c9cd6ed32386e11b9a59a5ce62fc1a7a310f1a557da1a083281bcb6009eac5",
    "EURUSDm": "8a6f08130cef771daea6e4aee0635f58b541d48428f49a54ca49d9ae9acfb9d6",
    "GBPCHFm": "e746ff02740c5690515d75bb7edfce8bd8b3acc8e432cf1fbf5d2c27fa7f1337",
    "GBPJPYm": "f423c4744eec49cf9bc983ec4a946977aa165b5baa04512f050ec9ed81418ed0",
    "GBPUSDm": "5e4f19add989110a17666e6b6e81003e6f7aeeda7007a017d458b96f5142b63f",
    "GER40m": "5a093bc338b2e3245e2fc4b1af78ac9fd4a449b22bd1e5e906acd88cdb468dff",
    "NAS100m": "5db189f7a3d8cb3dd6a768d887a3f7f469a57ab0ffaac72fac855c6886a72132",
    "NZDCADm": "2bb88184435ef14a664f6f39ccb1aaadea8e7d5a32e90ee21cbc40c2ab46d477",
    "NZDCHFm": "1c173729d63e70580c9a022e381473a9ca5208a4db62cc5c142ddefeb3a4be6b",
    "NZDJPYm": "c90fa78b7c6e2133af512792f161077f5d762a1cfaa7d63ce6451ca5d119c296",
    "NZDUSDm": "4933f14a80c7485f24c353eff48965d630eb086e13977c2873971ae2252974aa",
    "UK100m": "c9a466ca9490eb1a1bafb2e2b64b5c1ae137ae162317e3622530d8ebbc136153",
    "UKOILm": "6ac537a6788c008da7bc7c213bcc395f7f0f095c001321c396ad0946ba32d6fc",
    "US30m": "0955baa48e664769d57af52ca36da2d6f8f80515ead7541b7272e246b4315cf9",
    "US500m": "8527f77a0f2f556474ca6f8c98addae8f04d4e627a7acf32399830de7d4f65a6",
    "USDCADm": "c4ae837f8c971554adff52d7a89c2fc8bf4daceae2d24330cf71b605564f5bfe",
    "USDCHFm": "02663371dca643b5546e4f444282ff5fc7ee71540414c785cdef8819b9cf2ca8",
    "USDJPYm": "d7ea5bc4dd28fa4a3f671e2440c59517a7828dbd15533512581056d2f29e2033",
    "USOILm": "6ac537a6788c008da7bc7c213bcc395f7f0f095c001321c396ad0946ba32d6fc",
    "XAGUSDm": "65e3f256fb258288f8ace3e66c57fcb3c5851be39ebd5681abebb458422272cc",
    "XAUUSDm": "2765bcd712eedc4c51bebaf1fe5d97b5680f259c1d3a387951e05f54908d8aa1",
}

CATALOG_CATEGORIES = {
    **dict.fromkeys((
        "AUDCADm", "AUDJPYm", "AUDNZDm", "AUDUSDm", "CADCHFm", "CADJPYm",
        "EURAUDm", "EURCADm", "EURCHFm", "EURGBPm", "EURJPYm", "EURNZDm",
        "EURUSDm", "GBPCHFm", "GBPJPYm", "GBPUSDm", "NZDCADm", "NZDCHFm",
        "NZDJPYm", "NZDUSDm", "USDCADm", "USDCHFm", "USDJPYm",
    ), "forex"),
    **dict.fromkeys(("UKOILm", "USOILm", "XAGUSDm", "XAUUSDm"), "commodities"),
    **dict.fromkeys(("GER40m", "NAS100m", "UK100m", "US30m", "US500m"), "indices"),
    **dict.fromkeys(("BTCUSDm", "ETHUSDm"), "crypto"),
}

QUALITY = {
    "momentum_ignition": {
        "min_relative_volume": 1.0,
        "volume_lookback": 20,
        "require_confirmation": True,
    },
    "breakout_retest": {
        "min_relative_volume": 1.0,
        "volume_lookback": 20,
        "require_retest_rejection": True,
    },
}


def upgrade_unchanged_recommendations(apps, schema_editor):
    Asset = apps.get_model("bots", "Asset")
    database = schema_editor.connection.alias
    for asset in Asset.objects.using(database).filter(
        symbol__in=V5_HASHES, recommended_config_version=5,
    ):
        if asset.category != CATALOG_CATEGORIES[asset.symbol]:
            continue
        old = asset.recommended_config or {}
        digest = hashlib.sha256(
            json.dumps(old, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        if digest != V5_HASHES[asset.symbol]:
            continue
        preset = deepcopy(old)
        if asset.symbol not in {"XAUUSDm", "BTCUSDm"}:
            overrides = preset.setdefault("strategy_overrides", {})
            for strategy, values in QUALITY.items():
                overrides.setdefault(strategy, {}).update(values)
            if asset.category != "forex":
                overrides.setdefault("trend_pullback", {})["require_confirmation"] = True
        asset.recommended_config = preset
        asset.recommended_config_version = 6
        asset.save(using=database, update_fields=["recommended_config", "recommended_config_version"])
    # Bot snapshots deliberately remain unchanged until an explicit apply.


class Migration(migrations.Migration):
    dependencies = [("bots", "0058_bot_pause_reason")]
    operations = [migrations.RunPython(upgrade_unchanged_recommendations, migrations.RunPython.noop)]
