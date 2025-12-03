from django.core.management.base import BaseCommand
from execution.services.runtime_config import get_runtime_config


class Command(BaseCommand):
    help = "Print the current runtime config values (after DB + settings merge)."

    def handle(self, *args, **options):
        cfg = get_runtime_config()
        for field, value in cfg.__dict__.items():
            self.stdout.write(f"{field}: {value}")
