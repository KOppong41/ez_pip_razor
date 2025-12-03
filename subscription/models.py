from django.db import models
from django.conf import settings
from django.utils import timezone


class SubscriptionPlan(models.Model):
    name = models.CharField(max_length=100, unique=True)
    broker_accounts_limit = models.PositiveIntegerField(default=1)
    bots_limit = models.PositiveIntegerField(default=1)
    duration_days = models.PositiveIntegerField(default=30)
    price = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    description = models.TextField(blank=True, default="")

    def __str__(self):
        return f"{self.name} (brokers: {self.broker_accounts_limit})"


class UserSubscription(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="subscriptions",
    )
    plan = models.ForeignKey(
        SubscriptionPlan,
        on_delete=models.CASCADE,
        related_name="user_subscriptions",
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["user", "is_active"])]

    def __str__(self):
        return f"{self.user} -> {self.plan}"

    def is_current(self) -> bool:
        if not self.is_active:
            return False
        if self.expires_at and self.expires_at <= timezone.now():
            return False
        return True

    def broker_account_limit(self) -> int:
        return self.plan.broker_accounts_limit if self.plan else 1

    def bot_limit(self) -> int:
        return self.plan.bots_limit if self.plan else 1

    def save(self, *args, **kwargs):
        # auto-set expires_at if not provided, based on plan duration
        if self.plan and not self.expires_at and self.plan.duration_days:
            self.expires_at = timezone.now() + timezone.timedelta(days=self.plan.duration_days)
        super().save(*args, **kwargs)


class TenantSubscription(models.Model):
    STATUS_ACTIVE = "active"
    STATUS_PENDING = "pending"
    STATUS_CANCELED = "canceled"
    STATUS_EXPIRED = "expired"

    tenant = models.ForeignKey(
        "tenant.Tenant",
        on_delete=models.CASCADE,
        related_name="subscriptions",
    )
    plan = models.ForeignKey(
        SubscriptionPlan,
        on_delete=models.CASCADE,
        related_name="tenant_subscriptions",
    )
    status = models.CharField(
        max_length=20,
        choices=[
            (STATUS_ACTIVE, "Active"),
            (STATUS_PENDING, "Pending"),
            (STATUS_CANCELED, "Canceled"),
            (STATUS_EXPIRED, "Expired"),
        ],
        default=STATUS_PENDING,
    )
    started_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="tenant_subscriptions_created",
    )

    class Meta:
        ordering = ["-started_at"]
        indexes = [models.Index(fields=["tenant", "status"])]

    def __str__(self):
        return f"{self.tenant} -> {self.plan} ({self.status})"

    def is_current(self) -> bool:
        if self.status != self.STATUS_ACTIVE:
            return False
        if self.expires_at and self.expires_at <= timezone.now():
            return False
        return True

    def activate(self):
        self.status = self.STATUS_ACTIVE
        if self.plan and self.plan.duration_days:
            self.expires_at = timezone.now() + timezone.timedelta(days=self.plan.duration_days)
        self.save(update_fields=["status", "expires_at"])

    def cancel(self):
        self.status = self.STATUS_CANCELED
        self.save(update_fields=["status"])
