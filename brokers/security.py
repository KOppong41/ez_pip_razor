import base64
import hashlib
from django.conf import settings
from cryptography.fernet import Fernet, InvalidToken


def _get_fernet() -> Fernet:
    """
    Derive a stable Fernet key from BROKER_CREDS_KEY or SECRET_KEY.
    """
    secret = getattr(settings, "BROKER_CREDS_KEY", None) or settings.SECRET_KEY
    key = hashlib.sha256(secret.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(key))


_FERNET = _get_fernet()


def encrypt_secret(value: str) -> str:
    """
    Encrypt a single secret string. Returns a token string safe for DB storage.
    """
    if value is None:
        return ""
    if not isinstance(value, str):
        value = str(value)
    return _FERNET.encrypt(value.encode()).decode()


def decrypt_secret(token: str) -> str:
    """
    Decrypt a token produced by encrypt_secret. Returns empty string on failure.
    """
    if not token:
        return ""
    try:
        return _FERNET.decrypt(token.encode()).decode()
    except (InvalidToken, ValueError, TypeError):
        return ""
