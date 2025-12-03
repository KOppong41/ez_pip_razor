from django.db import models
from bots.models import Bot

class TelegramSource(models.Model):
    chat_id = models.BigIntegerField(db_index=True, unique=True)
    title = models.CharField(max_length=255, blank=True)
    is_enabled = models.BooleanField(default=True)

    # Routing
    bot = models.ForeignKey(Bot, null=True, blank=True, on_delete=models.SET_NULL)

    # Security
    allow_users = models.JSONField(default=list, blank=True)  # Telegram user_ids
    shared_secret = models.CharField(max_length=128, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.title or self.chat_id} -> {self.bot_id or '-'}"
