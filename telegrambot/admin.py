from django.contrib import admin
from .models import TelegramSource

@admin.register(TelegramSource)
class TelegramSourceAdmin(admin.ModelAdmin):
    list_display = ("chat_id","title","is_enabled","bot")
    list_filter = ("is_enabled",)
    search_fields = ("title","chat_id")
