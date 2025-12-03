from django.db import models, connections, DEFAULT_DB_ALIAS
from django.conf import settings
from django.apps import apps
from .security import encrypt_secret, decrypt_secret
from subscription.utils import get_broker_account_limit
from django.core.exceptions import ValidationError

DEFAULT_BROKER_CHOICES = [
    ("mt5", "MetaTrader 5"),
    ("exness_mt5", "Exness/MT5 (legacy)"),
    ("icmarket_mt5", "IC Markets/MT5 (legacy)"),
    ("binance", "Binance"),
    ("paper", "Paper"),
    ("fbs", "FBS"),
]


def get_broker_choices():
    """
    Resolve broker choices in priority:
    1) settings.BROKER_CHOICES (static override)
    2) DB-backed Broker rows (active only)
    3) DEFAULT_BROKER_CHOICES fallback

    Using the DB lets admins add/update brokers without code changes.
    """
    configured = getattr(settings, "BROKER_CHOICES", None)
    if configured:
        return configured

    try:
        Broker = apps.get_model("brokers", "Broker")
        # Skip DB hits if table not created yet (e.g., during initial migrate)
        table_names = connections[DEFAULT_DB_ALIAS].introspection.table_names()
        if Broker._meta.db_table not in table_names:
            return DEFAULT_BROKER_CHOICES

        qs = Broker.objects.filter(is_active=True).order_by("name", "code")
        choices = [(b.code, b.name or b.code) for b in qs]
        return choices or DEFAULT_BROKER_CHOICES
    except Exception:
        # During early migrations or if DB unavailable, fall back to defaults
        return DEFAULT_BROKER_CHOICES


class Broker(models.Model):
    code = models.CharField(max_length=32, unique=True)
    name = models.CharField(max_length=128)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name", "code"]

    def __str__(self):
        return f"{self.name} ({self.code})"

    @classmethod
    def choices(cls):
        return [(b.code, b.name or b.code) for b in cls.objects.filter(is_active=True).order_by("name", "code")]

    def get_connection_status(self):
        return "connected" if self.is_active else "disconnected"


class BrokerAccount(models.Model):
    name = models.CharField(max_length=100)
    broker = models.CharField(max_length=20)
    account_ref = models.CharField(max_length=128)  # e.g. login id or API key label
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="broker_accounts",
    )
    mt5_login = models.CharField(max_length=64, blank=True, default="")
    mt5_server = models.CharField(max_length=128, blank=True, default="")
    mt5_path = models.CharField(
        max_length=512,
        blank=True,
        default=r"C:\Program Files\MetaTrader 5\terminal64.exe",
    )
    mt5_password_enc = models.TextField(blank=True, default="")
    base_ccy = models.CharField(max_length=10, default="USD")
    leverage = models.IntegerField(default=100)
    is_active = models.BooleanField(default=True)
    is_verified = models.BooleanField(
        default=False,
        help_text="Set to True after credentials are validated/authorized.",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("broker", "account_ref")

    def __str__(self):
        return f"{self.name} [{self.broker}]"

    def get_creds(self) -> dict:
        """Return decrypted MT5 credentials."""
        return {
            "login": self.mt5_login,
            "server": self.mt5_server,
            "path": self.mt5_path,
            "password": decrypt_secret(self.mt5_password_enc),
        }

    def set_mt5_password(self, raw: str):
        self.mt5_password_enc = encrypt_secret(raw or "")

    def get_mt5_password(self) -> str:
        return decrypt_secret(self.mt5_password_enc)

    def get_mt5_creds(self) -> dict:
        """
        Return normalized MT5 credentials.
        """
        return self.get_creds()

    @staticmethod
    def available_brokers():
        """Expose current broker choices for forms/admin without hard-coding."""
        return get_broker_choices()

    def get_connection_status(self):
        if self.is_active and self.is_verified:
            return "connected"
        if self.is_active and not self.is_verified:
            return "verifying"
        return "disconnected"

    def clean(self):
        owner = self.owner
        testing = getattr(settings, "TESTING", False)

        if not owner and not testing:
            raise ValidationError("Owner is required for broker accounts.")

        # Enforce allowed brokers if configured in settings (keeps admin forms in sync without migrations).
        allowed_brokers = [code for code, _ in get_broker_choices()] or []
        if allowed_brokers and self.broker not in allowed_brokers:
            raise ValidationError(
                {"broker": f"Broker '{self.broker}' is not in allowed list: {', '.join(allowed_brokers)}"}
            )

        if owner:
            limit = get_broker_account_limit(owner)
            existing = (
                self.__class__.objects.filter(owner=owner)
                .exclude(pk=self.pk if self.pk else None)
                .count()
            )
            if existing >= limit:
                raise ValidationError(
                    f"Broker account limit reached ({limit}). Upgrade subscription to add more."
                )

        # Disallow creating paper accounts unless explicitly enabled or in tests
        if self.broker == "paper" and not testing:
            if not getattr(settings, "ALLOW_PAPER_BROKERS", True):
                raise ValidationError(
                    "Paper broker accounts are disabled. Set ALLOW_PAPER_BROKERS=1 to enable."
                )

        # Enforce single active MT5-type account per owner (one terminal session per user).
        mt5_codes = {"mt5", "exness_mt5", "icmarket_mt5"}
        if not testing and self.is_active and self.broker in mt5_codes and owner:
            active_conflict = (
                self.__class__
                .objects
                .filter(owner=owner, broker__in=mt5_codes, is_active=True)
                .exclude(pk=self.pk if self.pk else None)
                .exists()
            )
            if active_conflict:
                raise ValidationError(
                    "Only one active MT5 account is allowed per user. Deactivate the other MT5 account first."
                )

        # Optional: only if verification is mandatory, block unverified accounts.
        require_verification = getattr(settings, "BROKER_REQUIRE_VERIFICATION", False)
        if not testing and require_verification and not self.is_verified:
            raise ValidationError("Broker account must be verified before use.")

        # IMPORTANT: do not hard-block on missing password or other MT5 creds.
        # Incomplete credentials simply mean the account should not be treated as verified.
        # Admin / services should rely on is_verified plus health checks instead.

        return super().clean()

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)
