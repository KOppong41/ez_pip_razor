import base64
import hashlib
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from cryptography.fernet import Fernet, InvalidToken


def _get_fernet() -> Fernet:
    """
    Derive a stable Fernet key from the dedicated BROKER_CREDS_KEY.

    Broker passwords must not share key material with Django signing.  Failing
    here is intentional: live credentials cannot be read or written safely
    until the operator configures a separate key.
    """
    secret = getattr(settings, "BROKER_CREDS_KEY", None)
    if not secret:
        raise ImproperlyConfigured(
            "BROKER_CREDS_KEY is required to encrypt or decrypt live broker credentials"
        )
    key = hashlib.sha256(secret.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(key))


def encrypt_secret(value: str) -> str:
    """
    Encrypt a single secret string. Returns a token string safe for DB storage.
    """
    if value is None:
        return ""
    if not isinstance(value, str):
        value = str(value)
    return _get_fernet().encrypt(value.encode()).decode()


def decrypt_secret(token: str) -> str:
    """
    Decrypt a token produced by encrypt_secret. Returns empty string on failure.
    """
    if not token:
        return ""
    try:
        return _get_fernet().decrypt(token.encode()).decode()
    except (InvalidToken, ValueError, TypeError):
        return ""
