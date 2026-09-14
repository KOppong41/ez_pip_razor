"""Settings used only by a disposable historical replay subprocess."""
import os

if os.environ.get("EZSCALPER_REPLAY_WORKER") != "1":
    raise RuntimeError("Replay settings require the isolated replay worker")

from .settings import *  # noqa: F403,E402

DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}
CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
CELERY_BROKER_URL = "memory://"
CELERY_RESULT_BACKEND = "cache+memory://"
CELERY_TASK_EAGER_PROPAGATES = True
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
LOGGING = {"version": 1, "disable_existing_loggers": True, "handlers": {"null": {"class": "logging.NullHandler"}}, "root": {"handlers": ["null"]}}
TESTING = True
