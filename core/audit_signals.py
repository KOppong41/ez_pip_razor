import os
import socket
import sys
import traceback
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.contrib.auth import get_user_model

from core.utils import audit_log
from bots.models import Bot


def _common_payload(instance):
    """
    Capture process context so unexpected inserts can be traced.
    Keep the stack short to avoid bloating the audit payload.
    """
    return {
        "pid": os.getpid(),
        "hostname": socket.gethostname(),
        "argv": sys.argv,
        "model": f"{instance.__class__.__module__}.{instance.__class__.__name__}",
        "stack": traceback.format_stack(limit=5),
    }


@receiver(post_save, sender=get_user_model())
def audit_user_created(sender, instance, created, **kwargs):
    if not created:
        return
    payload = {"username": instance.username, **_common_payload(instance)}
    audit_log("user.created", "User", instance.id, payload)


@receiver(post_save, sender=Bot)
def audit_bot_created(sender, instance, created, **kwargs):
    if not created:
        return
    payload = {
        "name": instance.name,
        "owner_id": instance.owner_id,
        **_common_payload(instance),
    }
    audit_log("bot.created", "Bot", instance.id, payload)
