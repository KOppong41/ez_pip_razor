from django.contrib import admin
from payments.pdf_utils import generate_invoice_pdf
from .models import Invoice, Payment


@admin.register(Invoice)
class InvoiceAdmin(admin.ModelAdmin):
    actions = ["download_pdf"]
    list_display = ["invoice_number", "amount", "status", "due_date", "tenant"]
    list_filter = ("status", "tenant")
    search_fields = ("invoice_number", "tenant__name")
    readonly_fields = ("invoice_number", "reference", "created_at", "updated_at")

    def download_pdf(self, request, queryset):
        for invoice in queryset:
            if not invoice.pdf_file:
                invoice.pdf_file = generate_invoice_pdf(invoice)
                invoice.save()
        self.message_user(request, f"Generated PDFs for {queryset.count()} invoice(s).")


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ("transaction_ref", "invoice", "amount", "method", "status")
    list_filter = ("method", "status", "tenant")
    search_fields = ("transaction_ref", "invoice__invoice_number")
    readonly_fields = ("transaction_ref", "raw_response", "created_at")
