from pathlib import Path
import environ
from decimal import Decimal
import sys
from celery.schedules import crontab

BASE_DIR = Path(__file__).resolve().parent.parent

env = environ.Env(
    DJANGO_DEBUG=(bool, False),
    ALLOW_SQLITE_DESKTOP=(bool, False),
)
environ.Env.read_env(BASE_DIR / ".env")

SECRET_KEY = env("DJANGO_SECRET_KEY", default="dev-key")
DEBUG = env("DJANGO_DEBUG")
ALLOWED_HOSTS = [h.strip() for h in env("ALLOWED_HOSTS", default="*").split(",")]

API_ALLOW_OPEN = env.bool("API_ALLOW_OPEN", default=bool(env("DJANGO_DEBUG", default=False)))
BROKER_CREDS_KEY = env("BROKER_CREDS_KEY", default=None)

# Optional HMAC for dedupe hashing
EXECUTION_ALERT_SECRET = env("EXECUTION_ALERT_SECRET", default=None)


# Optional Sentry DSN (future)
SENTRY_DSN = env("SENTRY_DSN", default=None)

ALLOWED_HOSTS += ["localhost", "127.0.0.1", "ecologic-noncontemporaneously-phillip.ngrok-free.dev"]
# add your ngrok subdomain **after you see it**
CSRF_TRUSTED_ORIGINS = [
    "https://localhost",
    "https://127.0.0.1",
    "https://ecologic-noncontemporaneously-phillip.ngrok-free.dev",
]



INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "corsheaders",


    "subscription",
    "tenant",
    "payments",
    "core",
    "bots",
    "brokers",
    "execution",
    "copytrade",
    "rest_framework_simplejwt",
    "telegrambot",
    "notifications",

    
]

MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"


TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / "templates"],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

DATABASE_URL = env("DATABASE_URL", default=None)
if DATABASE_URL:
    DATABASES = {"default": env.db()}
else:
    # Require explicit Postgres settings; no SQLite fallback.
    DATABASES = {
        "default": {
            "ENGINE": env("DB_ENGINE", default="django.db.backends.postgresql"),
            "NAME": env("DB_NAME"),
            "USER": env("DB_USER"),
            "PASSWORD": env("DB_PASSWORD"),
            "HOST": env("DB_HOST"),
            "PORT": env("DB_PORT"),
        }
    }

# Hard guard: never allow sqlite in production stacks.
if "sqlite" in DATABASES["default"]["ENGINE"] and not env.bool("ALLOW_SQLITE_DESKTOP"):
    raise RuntimeError("SQLite is disabled for this project. Set DATABASE_URL or DB_* env vars for Postgres.")

LANGUAGE_CODE = "en-us"
TIME_ZONE = "Africa/Accra"
USE_I18N = True
USE_TZ = True




REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.SessionAuthentication",
        "rest_framework.authentication.BasicAuthentication",
         "rest_framework_simplejwt.authentication.JWTAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.AllowAny"
        if API_ALLOW_OPEN
        else "rest_framework.permissions.IsAuthenticated",
    ],
    

}

CORS_ALLOW_ALL_ORIGINS = API_ALLOW_OPEN
if not API_ALLOW_OPEN:
    CORS_ALLOWED_ORIGINS = env.list("CORS_ALLOWED_ORIGINS", default=[])

# Allow bulk admin deletes of large CeleryActivity logs
DATA_UPLOAD_MAX_NUMBER_FIELDS = int(env("DATA_UPLOAD_MAX_NUMBER_FIELDS", default=200000))

# Celery
CELERY_BROKER_URL = env("CELERY_BROKER_URL", default="redis://localhost:6379/0")
CELERY_RESULT_BACKEND = env("CELERY_RESULT_BACKEND", default="redis://localhost:6379/1")
CELERY_TIMEZONE = TIME_ZONE
CELERY_TASK_ALWAYS_EAGER = False  # For development/testing, run tasks immediately
CELERY_BEAT_SCHEDULE = {
    "monitor-positions-every-60s": {
        "task": "execution.tasks.monitor_positions_task",
        "schedule": 60.0,
    },
    "trail-positions-every-60s": {
        "task": "execution.tasks.trail_positions_task",
        "schedule": 60.0,
    },
    "reconcile-daily-23-55": {
        "task": "execution.tasks.reconcile_daily_task",
        "schedule": crontab(hour=23, minute=55),
    },
    "worker-heartbeat-30s": {
        "task": "core.tasks.worker_heartbeat_task",
        "schedule": 30.0,
    },
    "scalper-engine-45s": {
        "task": "execution.tasks.run_scalper_engine_for_all_bots",
        "schedule": 45.0,
        "args": ("1m", 100),
    },
    "harami-engine-5m": {
        "task": "execution.tasks.run_harami_engine_for_all_bots",
        "schedule": 300.0,
        "args": ("5m", 200),
    },
    "kill-switch-monitor-60s": {
        "task": "execution.tasks.kill_switch_monitor_task",
        "schedule": 60.0,
    },
    "reconcile-orders-hourly": {
        "task": "core.tasks.reconcile_trades_task",
        "schedule": 3600.0,
    },
    "cancel-stale-orders-60s": {
        "task": "execution.tasks.cancel_stale_orders_task",
        "schedule": 60.0,
    },
    "reconcile-broker-positions-5m": {
        "task": "execution.tasks.reconcile_broker_positions_task",
        "schedule": 300.0,
    },
    "market-hours-guard-5m": {
        "task": "execution.tasks.market_hours_guard_task",
        "schedule": 300.0,
    },
}


#TradingView
ALERT_WEBHOOK_TOKEN = env("ALERT_WEBHOOK_TOKEN", default=None)
ALERT_WEBHOOK_SECRET = env("ALERT_WEBHOOK_SECRET", default=None)
# EXECUTION_ALERT_SECRET is defined earlier; avoid a duplicate assignment here.

# Paper trading simulation
PAPER_START_BALANCE = Decimal(str(env("PAPER_START_BALANCE", default="100000")))

# MT5 defaults / health checks
MT5_DEFAULT_CONTRACT_SIZE = int(env("MT5_DEFAULT_CONTRACT_SIZE", default=100000))
MT5_HEALTHCHECK_SYMBOLS = env.list("MT5_HEALTHCHECK_SYMBOLS", default=["EURUSDm", "EURUSD"])
# Per-broker overrides, e.g. {"fbs": ["EURUSD", "XAUUSD"], "exness_mt5": ["EURUSDm", "XAUUSDm"]}
MT5_HEALTHCHECK_SYMBOLS_MAP = env.json(
    "MT5_HEALTHCHECK_SYMBOLS_MAP",
    default={"fbs": ["EURUSD", "XAUUSD"], "exness_mt5": ["EURUSDm", "XAUUSDm"]},
)
ADMIN_DISABLE_MT5_LOGIN = env.bool("ADMIN_DISABLE_MT5_LOGIN", default=False)

# Decision guardrails
DECISION_MIN_SCORE = float(env("DECISION_MIN_SCORE", default=0.5))
DECISION_FLIP_SCORE = float(env("DECISION_FLIP_SCORE", default=0.8))
DECISION_ALLOW_HEDGING = env.bool("DECISION_ALLOW_HEDGING", default=False)
DECISION_FLIP_COOLDOWN_MIN = int(env("DECISION_FLIP_COOLDOWN_MIN", default=15))
DECISION_MAX_FLIPS_PER_DAY = int(env("DECISION_MAX_FLIPS_PER_DAY", default=3))
DECISION_ORDER_COOLDOWN_SEC = int(env("DECISION_ORDER_COOLDOWN_SEC", default=60))
DECISION_SCALP_SL_OFFSET = Decimal(str(env("DECISION_SCALP_SL_OFFSET", default="0.0003")))
DECISION_SCALP_TP_OFFSET = Decimal(str(env("DECISION_SCALP_TP_OFFSET", default="0.0005")))
DECISION_SCALP_QTY_MULTIPLIER = Decimal(str(env("DECISION_SCALP_QTY_MULTIPLIER", default="0.3")))
ORDER_ACK_TIMEOUT_SECONDS = int(env("ORDER_ACK_TIMEOUT_SECONDS", default=180))
EARLY_EXIT_MAX_UNREALIZED_PCT = Decimal(str(env("EARLY_EXIT_MAX_UNREALIZED_PCT", default="0.02")))
TRAILING_TRIGGER = Decimal(str(env("TRAILING_TRIGGER", default="0.0005")))
TRAILING_DISTANCE = Decimal(str(env("TRAILING_DISTANCE", default="0.0003")))
MAX_ORDER_LOT = Decimal(str(env("MAX_ORDER_LOT", default="0.05")))
MAX_ORDER_NOTIONAL = Decimal(str(env("MAX_ORDER_NOTIONAL", default="5000")))

# Test mode flag
TESTING = "test" in sys.argv

# Allow paper broker accounts in non-test envs
ALLOW_PAPER_BROKERS = env.bool("ALLOW_PAPER_BROKERS", default=False)


# TradingView IMAP settings
# These settings are used to fetch alerts from TradingView via IMAP
TV_IMAP_HOST = env("TV_IMAP_HOST", default=None)
TV_IMAP_PORT = env.int("TV_IMAP_PORT", default=993)
TV_IMAP_USER = env("TV_IMAP_USER", default=None)
TV_IMAP_PASSWORD = env("TV_IMAP_PASSWORD", default=None)
TV_IMAP_FOLDER = env("TV_IMAP_FOLDER", default="INBOX")
TV_ALLOWED_FROM = env("TV_ALLOWED_FROM", default="")
TV_SUBJECT_CONTAINS = env("TV_SUBJECT_CONTAINS", default="TradingView")


# Telegram Bot settings
# These settings are used to configure the Telegram bot for alerts and notifications
TELEGRAM_BOT_TOKEN = env("TELEGRAM_BOT_TOKEN", default=None)
TELEGRAM_WEBHOOK_SECRET = env("TELEGRAM_WEBHOOK_SECRET", default=None)

TELEGRAM_TOKEN = env("TOKEN")
TELEGRAM_SECRET = env("SECRET")
TELEGRAM_WEBHOOK_URL = env("URL")

# Do not print secrets to stdout in production. Use secure logging if needed.


# Static files (CSS, JS, images)
STATIC_URL = '/static/'

STATICFILES_DIRS = [
    BASE_DIR / "static",   # where your editable static files live
]

# ✅ this is what Django uses when you run collectstatic
STATIC_ROOT = BASE_DIR / "staticfiles"   # Django will create this folder
