from django.urls import path
from .views import health
from .views import health, metrics

urlpatterns = [
    path("health/", health, name="health"),
    path("metrics", metrics, name="metrics"),
]
