import secrets
import string

from django import template

register = template.Library()

@register.simple_tag
def random_id(length=10, prefix=""):
    """Generate a random alphanumeric ID of `length` characters."""
    if length <= 0:
        return prefix
    alphabet = string.ascii_letters + string.digits
    return prefix + "".join(secrets.choice(alphabet) for _ in range(length))
