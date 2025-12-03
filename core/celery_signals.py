from celery.signals import task_failure, task_prerun, task_postrun
from core.utils import audit_log, celery_log
from core.metrics import task_failures_total


@task_prerun.connect
def on_task_start(sender=None, task_id=None, args=(), kwargs=None, **extras):
    task = getattr(sender, "name", "unknown")
    arg_repr = [repr(a) for a in args]
    kwarg_repr = {k: repr(v) for k, v in (kwargs or {}).items()}
    payload = {"task_id": task_id, "args": arg_repr, "kwargs": kwarg_repr}
    audit_log("task.start", "CeleryTask", task, payload)
    celery_log(
        component="task",
        message="start",
        task_name=task,
        task_id=task_id,
        payload=payload,
    )


@task_postrun.connect
def on_task_success(sender=None, task_id=None, retval=None, state=None, **extras):
    if state != "SUCCESS":
        return
    task = getattr(sender, "name", "unknown")
    payload = {"task_id": task_id, "result": repr(retval)}
    audit_log("task.success", "CeleryTask", task, payload)
    celery_log(
        component="task",
        message="success",
        task_name=task,
        task_id=task_id,
        payload=payload,
    )


@task_failure.connect
def on_task_failure(sender=None, exception=None, args=(), kwargs=None, **extras):
    task = getattr(sender, "name", "unknown")
    payload = {
        "args": [repr(a) for a in args],
        "kwargs": {k: repr(v) for k, v in (kwargs or {}).items()},
        "exc": str(exception),
    }
    audit_log("task.failure", "CeleryTask", task, payload)
    celery_log(
        component="task",
        message="failure",
        level="ERROR",
        task_name=task,
        task_id=extras.get("task_id"),
        payload=payload,
    )
    task_failures_total.labels(task=task).inc()
