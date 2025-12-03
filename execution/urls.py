from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import SignalViewSet, OrderViewSet, alert_webhook, decision_fanout

router = DefaultRouter()
router.register(r"signals", SignalViewSet, basename="signals")
router.register(r"orders", OrderViewSet, basename="orders")

urlpatterns = [
    path("", include(router.urls)),
    path("decisions/<int:decision_id>/fanout/", decision_fanout, name="decision-fanout"),
    path("alerts/webhook/", alert_webhook, name="alert-webhook"),
]
