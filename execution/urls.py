from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import SignalViewSet, OrderViewSet, alert_webhook, decision_fanout
from .personal_api import (
    personal_account_test,
    personal_accounts,
    personal_backtesting,
    personal_control,
    personal_dashboard,
    personal_history,
    personal_logs,
    personal_markets,
    personal_position_action,
    personal_positions,
    personal_risk,
    personal_runtime_session,
    personal_runtime_stop,
    personal_strategies,
)

router = DefaultRouter()
router.register(r"signals", SignalViewSet, basename="signals")
router.register(r"orders", OrderViewSet, basename="orders")

urlpatterns = [
    path("", include(router.urls)),
    path("decisions/<int:decision_id>/fanout/", decision_fanout, name="decision-fanout"),
    path("alerts/webhook/", alert_webhook, name="alert-webhook"),
    path("personal/dashboard/", personal_dashboard, name="personal-dashboard"),
    path("personal/control/", personal_control, name="personal-control"),
    path("personal/runtime/session/", personal_runtime_session, name="personal-runtime-session"),
    path("personal/runtime/stop/", personal_runtime_stop, name="personal-runtime-stop"),
    path("personal/markets/", personal_markets, name="personal-markets"),
    path("personal/strategies/", personal_strategies, name="personal-strategies"),
    path("personal/positions/", personal_positions, name="personal-positions"),
    path("personal/positions/<int:position_id>/action/", personal_position_action, name="personal-position-action"),
    path("personal/risk/", personal_risk, name="personal-risk"),
    path("personal/history/", personal_history, name="personal-history"),
    path("personal/logs/", personal_logs, name="personal-logs"),
    path("personal/backtesting/", personal_backtesting, name="personal-backtesting"),
    path("personal/accounts/", personal_accounts, name="personal-accounts"),
    path("personal/accounts/test/", personal_account_test, name="personal-account-test"),
]
