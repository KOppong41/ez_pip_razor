from django.db import models
from bots.models import Bot
from brokers.models import BrokerAccount

class Follower(models.Model):
    ALLOC_MODEL = [
        ("proportional", "Proportional"),
        ("fixed", "Fixed Size"),
        ("equity_pct", "Equity Percent"),
    ]
    master_bot = models.ForeignKey(Bot, on_delete=models.CASCADE, related_name="followers")
    broker_account = models.ForeignKey(BrokerAccount, on_delete=models.CASCADE, related_name="followers")
    model = models.CharField(max_length=20, choices=ALLOC_MODEL, default="proportional")
    params = models.JSONField(default=dict)  # e.g. {"multiplier": 1.0} or {"fixed_qty":"0.10"} or {"equity_pct":1}
    min_balance = models.DecimalField(max_digits=20, decimal_places=2, default=0)
    is_enabled = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("master_bot", "broker_account")

    def __str__(self):
        return f"{self.master_bot.name} -> {self.broker_account.name} ({self.model})"
