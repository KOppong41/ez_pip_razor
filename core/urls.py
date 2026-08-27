from django.urls import path
from .views import desktop_bootstrap, health, metrics

urlpatterns = [
    path("health/", health, name="health"),
    path("desktop/bootstrap/", desktop_bootstrap, name="desktop-bootstrap"),
    path("metrics", metrics, name="metrics"),
]
