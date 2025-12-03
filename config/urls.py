from django.contrib import admin
from django.urls import path, include
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

admin.site.site_header = "EzTrade"
admin.site.site_title = "EzTrade"

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/", include("core.urls")),
    path("api/", include("execution.urls")),
    path("api/", include("copytrade.urls")),
    path("api/", include("bots.urls")),
    path("api/", include("telegrambot.urls")),

    path("api/auth/token/", TokenObtainPairView.as_view(), name="token_obtain_pair"),
    path("api/auth/token/refresh/", TokenRefreshView.as_view(), name="token_refresh"),

]
