"""Fail-closed settings for a remotely served Django deployment.

This does not make Linux/Docker a supported MT5 execution host. Live MT5
execution still runs in the dedicated Windows desktop worker.
"""

from .settings import *  # noqa: F403


DEBUG = False

if API_ALLOW_OPEN:  # noqa: F405
    raise RuntimeError("API_ALLOW_OPEN must be false in production")
if not ALLOWED_HOSTS or "*" in ALLOWED_HOSTS:  # noqa: F405
    raise RuntimeError("Production ALLOWED_HOSTS must be explicit")
if len(SECRET_KEY) < 50 or SECRET_KEY.startswith(("change-me", "test-only")):  # noqa: F405
    raise RuntimeError("DJANGO_SECRET_KEY must be a strong production secret")
if not BROKER_CREDS_KEY:  # noqa: F405
    raise RuntimeError("BROKER_CREDS_KEY must be configured in production")

SECURE_SSL_REDIRECT = env.bool("SECURE_SSL_REDIRECT", default=True)  # noqa: F405
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_HSTS_SECONDS = int(env("SECURE_HSTS_SECONDS", default=3600))  # noqa: F405
SECURE_HSTS_INCLUDE_SUBDOMAINS = env.bool(  # noqa: F405
    "SECURE_HSTS_INCLUDE_SUBDOMAINS", default=False
)
SECURE_HSTS_PRELOAD = env.bool("SECURE_HSTS_PRELOAD", default=False)  # noqa: F405
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"
