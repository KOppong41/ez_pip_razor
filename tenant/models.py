from django.db import models
from django.utils.text import slugify
from django.conf import settings


class Tenant(models.Model):
    """Minimal tenant/organization model."""
    name = models.CharField(max_length=100, unique=True)
    slug = models.SlugField(unique=True, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="tenants_created",
    )
    active_plan = models.ForeignKey(
        "subscription.SubscriptionPlan",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="tenants_using_plan",
    )
    subdomain = models.CharField(max_length=100, unique=True, blank=True, null=True)
    url = models.CharField(max_length=200, blank=True)

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)
        if not self.subdomain:
            self.subdomain = self.slug
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name


class TenantAwareModel(models.Model):
    """
    Abstract base model that automatically adds tenant relationship
    to all models that inherit from it.
    """
    tenant = models.ForeignKey(
        "tenant.Tenant",
        on_delete=models.CASCADE,
        related_name="%(class)ss",
    )

    class Meta:
        abstract = True
