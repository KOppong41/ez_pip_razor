from django.apps import AppConfig


class BotsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'bots'

    def ready(self):
        # Per-bot config now lives on Bot; no BotConfig signals needed.
        super().ready()

