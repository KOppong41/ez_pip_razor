from django import forms
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

from .models import BrokerAccount


class BrokerAccountForm(forms.ModelForm):
    """
    Admin form for BrokerAccount.
    """

    mt5_password = forms.CharField(
        label=_("MT5 Password"),
        widget=forms.PasswordInput(render_value=True, attrs={'autocomplete': 'new-password'}),
        required=False,
        help_text=_("Leave blank to keep the existing password. Required for MT5 brokers when creating new account."),
    )

    class Meta:
        model = BrokerAccount
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        self.request = kwargs.pop("request", None)
        super().__init__(*args, **kwargs)
        
        # Set initial owner for new accounts
        if self.request and not self.instance.pk and "owner" in self.fields:
            self.fields["owner"].initial = self.request.user

    def clean(self):
        cleaned_data = super().clean()
        instance = self.instance
        is_new = not instance.pk
        
        # Get broker and password
        broker = cleaned_data.get("broker")
        password = cleaned_data.get("mt5_password", "")
        
        # For MT5 brokers on new accounts, password is required
        if is_new and broker in ["mt5", "exness_mt5", "icmarket_mt5", "fbs"]:
            if not password:
                raise ValidationError({
                    "mt5_password": _("Password is required for MT5 brokers when creating a new account.")
                })
        
        # For existing accounts, if password is blank, keep existing one
        if not is_new and not password:
            # This will be handled in save method - remove from cleaned_data so it doesn't override
            if "mt5_password" in cleaned_data:
                del cleaned_data["mt5_password"]
        
        return cleaned_data
