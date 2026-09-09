from bots.management.commands.load_assets import Command as LoadAssetsCommand


class Command(LoadAssetsCommand):
    help = "Compatibility alias for load_assets; seeds the canonical recommendations."
