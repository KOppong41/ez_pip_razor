import logging
from django.core.files.base import ContentFile

logger = logging.getLogger(__name__)


def generate_invoice_pdf(invoice):
    """
    Stub PDF generator. Replace with real implementation if needed.
    """
    try:
        content = f"Invoice {invoice.invoice_number} for {invoice.amount}".encode()
        filename = f"invoice_{invoice.invoice_number}.txt"
        invoice.pdf_file.save(filename, ContentFile(content), save=True)
        return invoice.pdf_file
    except Exception as e:
        logger.exception("Failed to generate invoice PDF: %s", e)
        return None


def email_invoice_pdf(invoice):
    """
    Stub email sender. Returns True to indicate success.
    """
    generate_invoice_pdf(invoice)
    return True
