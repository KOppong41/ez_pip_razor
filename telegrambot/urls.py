from django.urls import path
from .views import webhook

urlpatterns = [
    path("alerts/telegram/<str:secret>/", webhook, name="telegram-webhook"),
]
