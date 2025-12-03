from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import FollowerViewSet

router = DefaultRouter()
router.register(r"followers", FollowerViewSet, basename="followers")

urlpatterns = [
    path("", include(router.urls)),
]
