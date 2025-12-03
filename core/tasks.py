from celery import shared_task
from core.utils import structured_log
from django.utils import timezone


@shared_task(bind=True, autoretry_for=(Exception,), retry_backoff=True, retry_backoff_max=300, retry_jitter=True, retry_kwargs={"max_retries": 3})
def reconcile_trades_task(self):
    """
    Periodic dry-run reconciliation to detect missing executions/positions.
    """
    from execution.services.reconcile import reconcile_orders_and_positions

    result = reconcile_orders_and_positions(apply=False)
    structured_log("reconcile_trades", **result)
    return result


@shared_task(name="core.tasks.worker_heartbeat_task")
def worker_heartbeat_task(name: str = "default"):
    """
    Lightweight heartbeat to record that a worker is alive.
    """
    from core.models import WorkerHeartbeat

    WorkerHeartbeat.objects.update_or_create(name=name, defaults={"last_seen": timezone.now()})
    structured_log("worker_heartbeat", worker=name, at=str(timezone.now()))
