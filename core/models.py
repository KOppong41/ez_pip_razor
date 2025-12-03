from django.db import models
from django.contrib.auth import get_user_model
from django.conf import settings

class Audit(models.Model):
    actor = models.ForeignKey(settings.AUTH_USER_MODEL,  null=True, blank=True, on_delete=models.SET_NULL)
    action = models.CharField(max_length=64)             # e.g. "order.transition", "bot.start"
    entity = models.CharField(max_length=64)             # e.g. "Order", "Bot"
    entity_id = models.CharField(max_length=64)
    payload = models.JSONField(default=dict, blank=True)
    ts = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=["action","entity","ts"])]
        ordering = ["-ts"]

    def __str__(self):
        return f"{self.ts} {self.action} {self.entity}#{self.entity_id}"



class WorkerHeartbeat(models.Model):
    name = models.CharField(max_length=64, unique=True, default="default")
    last_seen = models.DateTimeField(auto_now=True)


class CeleryActivity(models.Model):
    """
    Generic event log for Celery: task starts, successes, failures, and worker signals.
    """
    ts = models.DateTimeField(auto_now_add=True)
    level = models.CharField(max_length=16, default="INFO")
    component = models.CharField(max_length=64)          # e.g. task, worker, beat
    message = models.TextField()
    task_name = models.CharField(max_length=128, blank=True)
    task_id = models.CharField(max_length=128, blank=True)
    payload = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-ts"]
        indexes = [
            models.Index(fields=["ts"]),
            models.Index(fields=["task_name", "ts"]),
        ]

    def __str__(self):
        return f"{self.ts} [{self.level}] {self.component} {self.task_name or ''}".strip()
