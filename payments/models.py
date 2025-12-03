import uuid
from django.db import models
from django.utils import timezone
from tenant.models import TenantAwareModel
from subscription.models import TenantSubscription


class Invoice(TenantAwareModel):
    STATUS_DRAFT = "draft"
    STATUS_PENDING = "pending"
    STATUS_PAID = "paid"
    STATUS_FAILED = "failed"

    STATUS_CHOICES = [
        (STATUS_DRAFT, "Draft"),
        (STATUS_PENDING, "Pending"),
        (STATUS_PAID, "Paid"),
        (STATUS_FAILED, "Failed"),
    ]

    tenant_subscription = models.ForeignKey(
        TenantSubscription,
        on_delete=models.PROTECT,
        related_name="invoices",
        null=True,
        blank=True,
    )
    description = models.CharField(max_length=255, blank=True)
    invoice_number = models.CharField(max_length=20, unique=True, editable=False)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    due_date = models.DateField(default=timezone.now)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=STATUS_DRAFT)
    reference = models.CharField(max_length=64, blank=True, default="")
    pdf_file = models.FileField(upload_to="invoices/", null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"INV-{self.invoice_number} ({self.status})"

    def save(self, *args, **kwargs):
        if not self.invoice_number:
            self.invoice_number = uuid.uuid4().hex[:12].upper()
        if not self.reference:
            self.reference = uuid.uuid4().hex[:16]
        super().save(*args, **kwargs)


class Payment(TenantAwareModel):
    STATUS_PENDING = "pending"
    STATUS_SUCCESS = "success"
    STATUS_FAILED = "failed"

    METHOD_CARD = "card"
    METHOD_MOMO = "mobile_money"
    METHOD_BANK = "bank"

    METHOD_CHOICES = [
        (METHOD_CARD, "Card"),
        (METHOD_MOMO, "Mobile Money"),
        (METHOD_BANK, "Bank Transfer"),
    ]

    invoice = models.ForeignKey(Invoice, on_delete=models.PROTECT, related_name="payments")
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    method = models.CharField(max_length=20, choices=METHOD_CHOICES)
    transaction_ref = models.CharField(max_length=100, unique=True)
    status = models.CharField(max_length=20, default=STATUS_PENDING)
    raw_response = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.get_method_display()} - {self.amount} ({self.status})"
