from rest_framework.views import exception_handler as drf_handler
from core.metrics import task_failures_total  # reuse counter for API errors too

def custom_exception_handler(exc, context):
    resp = drf_handler(exc, context)
    if resp is None:
        # unhandled → 500
        task_failures_total.labels(task="api").inc()
    return resp
