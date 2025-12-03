from django import forms
from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.conf import settings
from django.http import Http404, HttpResponseRedirect, JsonResponse
from django.urls import path, reverse

from execution.services.accounts import get_account_balances
from execution.connectors.mt5 import MT5Connector
from execution.connectors.base import ConnectorError
from datetime import timedelta

from django.utils import timezone

from .models import BrokerAccount, Broker


class BrokerAccountForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        self.request = kwargs.pop("request", None)
        super().__init__(*args, **kwargs)
        dynamic_choices = Broker.choices() if hasattr(Broker, "choices") else []
        if not dynamic_choices:
            dynamic_choices = BrokerAccount.available_brokers()
        dynamic_choices = [("", "Select broker type")] + dynamic_choices

        # Ensure existing value remains selectable even if broker is inactive/removed
        current = self.instance.broker if getattr(self.instance, "broker", None) else None
        choice_keys = [c[0] for c in dynamic_choices]
        if current and current not in choice_keys:
            dynamic_choices = [(current, f"{current} (inactive)")] + dynamic_choices

        self.fields["broker"] = forms.ChoiceField(choices=dynamic_choices, label="Broker")
        if self.request and not self.instance.pk and "owner" in self.fields:
            self.fields["owner"].initial = self.request.user

        checkbox_types = (
            forms.CheckboxInput,
            forms.CheckboxSelectMultiple,
        )
        for field_name, field in self.fields.items():
            widget = field.widget
            if isinstance(widget, checkbox_types):
                continue
            existing_classes = widget.attrs.get("class", "")
            widget.attrs["class"] = "form-control" if not existing_classes else f"{existing_classes} form-control"
            if field.required:
                widget.attrs.setdefault("aria-required", "true")

    mt5_password = forms.CharField(
        required=False,
        widget=forms.PasswordInput(render_value=False),
        help_text="Leave blank to keep existing password.",
    )

    class Meta:
        model = BrokerAccount
        fields = [
            "name",
            "broker",
            "account_ref",
            "owner",
            "mt5_server",
            "mt5_login",
            "mt5_path",
            "mt5_password",
            "base_ccy",
            "leverage",
            "is_active",
            "is_verified",
        ]
        exclude = ("mt5_password_enc",)

    def clean(self):
        cleaned = super().clean()

        # Default owner if the field is omitted (read-only in admin).
        if not cleaned.get("owner") and self.request:
            cleaned["owner"] = self.request.user
            self.instance.owner = self.request.user

        return cleaned


@admin.register(BrokerAccount)
class BrokerAccountAdmin(admin.ModelAdmin):

    form = BrokerAccountForm
    
    change_list_template = "admin/brokers/brokeraccount.html"
    change_form_template = "admin/brokers/brokeraccount_change_form.html"

    list_display = ("name", "broker", "owner", "masked_creds", "is_verified", "is_active")
    list_filter = ("broker", "is_active", "created_at")
    search_fields = ("name", "owner__username")
    readonly_fields = ("owner",)
    exclude = ("mt5_password_enc",)

    def get_urls(self):
        urls = super().get_urls()
        custom = [
            path(
                "<path:object_id>/activate/",
                self.admin_site.admin_view(self.activate_account_view),
                name="brokers_brokeraccount_activate",
            ),
            path(
                "<path:object_id>/deactivate/",
                self.admin_site.admin_view(self.deactivate_account_view),
                name="brokers_brokeraccount_deactivate",
            ),
            path(
                "<path:object_id>/test_connection/",
                self.admin_site.admin_view(self.test_connection_view),
                name="brokers_brokeraccount_test_connection",
            ),
            path(
                "<path:object_id>/refresh_balance/",
                self.admin_site.admin_view(self.refresh_balance_view),
                name="brokers_brokeraccount_refresh_balance",
            ),
        ]
        return custom + urls

    def changelist_view(self, request, extra_context=None):
        qs = self.model._default_manager.order_by("-created_at")
        total_accounts = qs.count()
        active_accounts = qs.filter(is_active=True).count()
        verified_accounts = qs.filter(is_verified=True).count()
        connected_accounts = qs.filter(is_active=True, is_verified=True).count()
        extra_context = extra_context or {}
        extra_context.update({
            "accounts": list(qs),
            "total_accounts_count": total_accounts,
            "active_accounts_count": active_accounts,
            "connected_accounts_count": connected_accounts,
            "verified_accounts_count": verified_accounts,
        })
        return super().changelist_view(request, extra_context=extra_context)

    def _toggle_account_active(self, request, object_id, *, active):
        try:
            account = self.get_object(request, object_id)
        except TypeError:
            account = self.model._default_manager.filter(pk=object_id).first()
        if account is None:
            raise Http404("Broker account not found")
        if not self.has_change_permission(request, account):
            raise PermissionDenied
        account.is_active = active
        try:
            account.save(update_fields=["is_active"])
            state = "activated" if active else "deactivated"
            self.message_user(request, f"Broker account '{account.name}' {state}.")
        except ValidationError as exc:
            # Surface validation issues (e.g., multiple active MT5 accounts) without 500s.
            msg = "; ".join(
                [f"{field}: {', '.join(err_list)}" if field != "__all__" else ", ".join(err_list)
                 for field, err_list in getattr(exc, "message_dict", {}).items()]
            ) or str(exc)
            self.message_user(request, msg, level="error")
        next_url = request.POST.get("next") or request.GET.get("next") or reverse("admin:brokers_brokeraccount_changelist")
        return HttpResponseRedirect(next_url)

    def activate_account_view(self, request, object_id, *args, **kwargs):
        if request.method != "POST":
            return HttpResponseRedirect(reverse("admin:brokers_brokeraccount_changelist"))
        return self._toggle_account_active(request, object_id, active=True)

    def deactivate_account_view(self, request, object_id, *args, **kwargs):
        if request.method != "POST":
            return HttpResponseRedirect(reverse("admin:brokers_brokeraccount_changelist"))
        return self._toggle_account_active(request, object_id, active=False)

    def _get_health_symbols(self, obj):
        symbol_map = getattr(settings, "MT5_HEALTHCHECK_SYMBOLS_MAP", {}) or {}
        return symbol_map.get(obj.broker) or getattr(settings, "MT5_HEALTHCHECK_SYMBOLS", ["EURUSDm", "EURUSD"])

    def test_connection_view(self, request, object_id, *args, **kwargs):
        if request.method != "POST":
            return JsonResponse({"ok": False, "message": "POST required"}, status=405)
        if getattr(settings, "ADMIN_DISABLE_MT5_LOGIN", True):
            return JsonResponse({"ok": False, "message": "MT5 connection tests are disabled. Set ADMIN_DISABLE_MT5_LOGIN=0 to enable."}, status=400)
        obj = self.get_object(request, object_id)
        if not obj:
            return JsonResponse({"ok": False, "message": "Broker account not found."}, status=404)
        if not obj.is_active:
            return JsonResponse({"ok": False, "message": "Account is inactive. Activate before testing."}, status=400)
        symbols = self._get_health_symbols(obj)
        errs = []
        for sym in symbols:
            try:
                MT5Connector().check_health(obj.get_creds(), symbol=sym)
                return JsonResponse({"ok": True, "message": f"MT5 connection OK for {sym}"})
            except Exception as e:
                errs.append(f"{sym}: {e}")
        return JsonResponse({"ok": False, "message": " | ".join(errs)}, status=400)

    def refresh_balance_view(self, request, object_id, *args, **kwargs):
        if request.method != "POST":
            return JsonResponse({"ok": False, "message": "POST required"}, status=405)
        if getattr(settings, "ADMIN_DISABLE_MT5_LOGIN", True):
            return JsonResponse({"ok": False, "message": "Balance refresh is disabled. Set ADMIN_DISABLE_MT5_LOGIN=0 to enable."}, status=400)
        obj = self.get_object(request, object_id)
        if not obj:
            return JsonResponse({"ok": False, "message": "Broker account not found."}, status=404)
        if not obj.is_active:
            return JsonResponse({"ok": False, "message": "Account is inactive. Activate before refresh."}, status=400)
        
        # Now safe to call with force_live=True since we've verified account is active
        data = get_account_balances(obj, force_live=True)
        if data["balance"] is None and data["equity"] is None:
            return JsonResponse({"ok": False, "message": "Unable to fetch balance/equity."}, status=400)
        return JsonResponse({"ok": True, **data})

    @admin.action(description="Verify credentials for selected accounts")
    def verify_accounts(self, request, queryset):
        checked = 0
        skipped = 0
        for obj in queryset:
            if not obj.is_active:
                skipped += 1
                continue
            creds = obj.get_creds()
            try:
                MT5Connector().check_health(creds, symbol="EURUSDm")
                obj.is_verified = True
                obj.save(update_fields=["is_verified"])
                checked += 1
            except Exception as e:
                self.message_user(request, f"{obj.name}: verification failed ({e})", level="error")
        if checked:
            self.message_user(request, f"Verified {checked} account(s).")
        if skipped:
            self.message_user(request, f"Skipped {skipped} inactive account(s); activate before verifying.", level="warning")

    def get_form(self, request, obj=None, **kwargs):
        BaseForm = super().get_form(request, obj, **kwargs)

        class RequestAwareForm(BaseForm):
            def __init__(self, *args, **kw):
                kw["request"] = request
                super().__init__(*args, **kw)

        return RequestAwareForm

    def masked_creds(self, obj):
        creds = obj.get_creds() or {}
        login = creds.get("login") or "<none>"
        server = creds.get("server") or "<none>"
        has_pwd = bool(creds.get("password"))
        return f"login={login}, server={server}, password={'***' if has_pwd else '<none>'}"

    masked_creds.short_description = "Credentials"

    def has_add_permission(self, request):
        is_admin = request.user.is_superuser or request.user.groups.filter(name="Admin").exists()
        return is_admin

    def save_model(self, request, obj, form, change):
        is_admin = request.user.is_superuser or request.user.groups.filter(name="Admin").exists()
        if not is_admin:
            raise PermissionDenied("Only Admins may create or modify broker accounts.")

        # Resolve owner
        if not obj.owner:
            obj.owner = request.user

        # Resolve password (new or existing)
        pwd = form.cleaned_data.get("mt5_password")
        if not pwd and obj.pk:
            pwd = obj.get_mt5_password()

        # Optional MT5 health check on save (can be slow). Default: skip and leave unverified.
        run_healthcheck = getattr(settings, "ADMIN_MT5_HEALTHCHECK_ON_SAVE", False)
        if run_healthcheck and obj.is_active:
            creds = {
                "login": form.cleaned_data.get("mt5_login") or obj.mt5_login,
                "server": form.cleaned_data.get("mt5_server") or obj.mt5_server,
                "path": form.cleaned_data.get("mt5_path") or obj.mt5_path,
                "password": pwd or "",
            }
            symbol_map = getattr(settings, "MT5_HEALTHCHECK_SYMBOLS_MAP", {}) or {}
            symbols = symbol_map.get(obj.broker) or getattr(settings, "MT5_HEALTHCHECK_SYMBOLS", ["EURUSDm", "EURUSD"])

            errors = []
            for sym in symbols:
                try:
                    MT5Connector().check_health(creds, symbol=sym)
                    obj.is_verified = True
                    errors = []
                    break
                except Exception as e:
                    msg = str(e)
                    if isinstance(e, ConnectorError) and e.args:
                        msg = e.args[0]
                    errors.append(f"{sym}: {msg}")

            if errors:
                obj.is_verified = False
                messages.warning(
                    request,
                    "MT5 health check failed; account saved as unverified. "
                    + " | ".join(errors)
                    + " (adjust MT5_HEALTHCHECK_SYMBOLS/_MAP or ensure symbol is visible).",
                )
        elif run_healthcheck and not obj.is_active:
            # Inactive accounts should not hit MT5; leave as unverified.
            if change is False:
                obj.is_verified = False
            messages.info(
                request,
                "Skipped MT5 health check because account is inactive. Activate before verifying.",
            )
        else:
            # mark unverified; admins can use the Test Connection button
            if change is False:
                obj.is_verified = False
            messages.info(
                request,
                "Skipped MT5 health check on save (ADMIN_MT5_HEALTHCHECK_ON_SAVE=False). Use Test Connection to verify.",
            )

        # Persist password if a new one was provided
        if pwd:
            obj.set_mt5_password(pwd)

        # Do not force is_verified here; it was already
        # set to True in the loop above when a health
        # check passes, or left False if checks failed.
        super().save_model(request, obj, form, change)

    def live_balance(self, obj):
        data = getattr(obj, "balance", None)
        return data

    def live_equity(self, obj):
        data = getattr(obj, "equity", None)
        return data

    live_balance.short_description = "Balance"
    live_equity.short_description = "Equity"

    @admin.action(description="Refresh live balance/equity (MT5 login)")
    def refresh_live_balances(self, request, queryset):
        refreshed = 0
        errors = []
        for acct in queryset:
            try:
                # Temporarily allow MT5 login for this refresh only
                data = get_account_balances(acct)
                if data["balance"] is not None or data["equity"] is not None:
                    refreshed += 1
                else:
                    errors.append(f"{acct.name}: no data")
            except Exception as e:
                errors.append(f"{acct.name}: {e}")
        if refreshed:
            self.message_user(request, f"Refreshed {refreshed} account(s).")
        if errors:
            for msg in errors:
                self.message_user(request, msg, level="error")

    @admin.action(description="Activate selected broker accounts")
    def activate_accounts(self, request, queryset):
        updated = queryset.update(is_active=True)
        self.message_user(request, f"Activated {updated} account(s).")

    @admin.action(description="Deactivate selected broker accounts")
    def deactivate_accounts(self, request, queryset):
        updated = queryset.update(is_active=False)
        self.message_user(request, f"Deactivated {updated} account(s).")

    actions = [
        "verify_accounts",
        "refresh_live_balances",
        "activate_accounts",
            "deactivate_accounts",
        ]

    def change_view(self, request, object_id=None, form_url="", extra_context=None):
        """
        Fetch live balance/equity on the change form to validate creds visibly.
        """
        extra_context = extra_context or {}
        obj = None
        if object_id:
            obj = self.get_object(request, object_id)
        if obj:
            # Do not fetch balances or touch MT5; render placeholders only.
            setattr(obj, "balance", None)
            setattr(obj, "equity", None)
            setattr(obj, "margin", None)
            extra_context.update(
                {
                    "live_balance": None,
                    "live_equity": None,
                    "live_margin": None,
                    "balance_fetched_at": None,
                }
            )
        return super().change_view(request, object_id, form_url, extra_context=extra_context)


@admin.register(Broker)
class BrokerAdmin(admin.ModelAdmin):
    
    change_list_template = "admin/brokers/broker.html"
    change_form_template = "admin/brokers/broker_change_form.html"
    
    list_display = ("name", "code", "is_active", "created_at")
    list_filter = ("is_active",)
    search_fields = ("name", "code")
    ordering = ("name", "code")

    def changelist_view(self, request, extra_context=None):
        qs = self.model._default_manager.all()
        recent_cutoff = timezone.now() - timedelta(days=30)
        extra_context = extra_context or {}
        extra_context.update({
            "brokers": list(qs),
            "active_brokers_count": qs.filter(is_active=True).count(),
            "connected_brokers_count": qs.filter(is_active=True).count(),
            "total_brokers_count": qs.count(),
            "recent_brokers_count": qs.filter(created_at__gte=recent_cutoff).count(),
        })
        return super().changelist_view(request, extra_context=extra_context)
