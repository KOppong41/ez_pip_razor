"""
Minimal payment stubs for tenant subscriptions.
Replace with real gateway integration when ready.
"""
from django.db import transaction

from payments.models import Invoice, Payment
from subscription.models import TenantSubscription


def initiate_payment(tenant_subscription: TenantSubscription) -> dict:
    if not tenant_subscription or not tenant_subscription.plan:
        raise ValueError("Subscription with plan required")

    amount = tenant_subscription.plan.price or 0
    with transaction.atomic():
        invoice = Invoice.objects.create(
            tenant=tenant_subscription.tenant,
            tenant_subscription=tenant_subscription,
            amount=amount,
            status=Invoice.STATUS_PENDING,
            description=f"Subscription for {tenant_subscription.plan.name}",
        )
    return {
        "status": "pending",
        "reference": invoice.reference,
        "invoice_id": invoice.id,
        "amount": str(amount),
    }


def mark_payment_success(reference: str) -> bool:
    """
    Mark invoice paid and activate the attached subscription.
    """
    try:
        invoice = Invoice.objects.select_related("tenant_subscription", "tenant_subscription__plan").get(reference=reference)
    except Invoice.DoesNotExist:
        return False

    with transaction.atomic():
        invoice.status = Invoice.STATUS_PAID
        invoice.save(update_fields=["status"])
        Payment.objects.create(
            tenant=invoice.tenant,
            invoice=invoice,
            amount=invoice.amount,
            method=Payment.METHOD_CARD,
            transaction_ref=reference,
            status=Payment.STATUS_SUCCESS,
            raw_response={"reference": reference, "source": "stub"},
        )
        sub = invoice.tenant_subscription
        if sub:
            sub.activate()
    return True
