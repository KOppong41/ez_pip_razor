from rest_framework import viewsets, mixins, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.decorators import action, permission_classes
from rest_framework.permissions import IsAuthenticated
from core.permissions import IsAdminOnlyWrite_ReadForAllAuth
from .models import Bot
from .serializers import BotSerializer, BotControlSerializer, BotSettingsSerializer
from core.utils import structured_log

class BotViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    permission_classes = [IsAdminOnlyWrite_ReadForAllAuth]
    queryset = Bot.objects.all().order_by("id")
    serializer_class = BotSerializer

    @action(detail=True, methods=["post"], url_path="control")
    def control(self, request, pk=None):
        bot = self.get_object()
        ser = BotControlSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        act = ser.validated_data["action"]
        new_status = {"start":"active","pause":"paused","stop":"stopped"}[act]
        bot.status = new_status
        bot.save(update_fields=["status"])
        structured_log("bot.control", bot_id=bot.id, action=act, status=new_status)
        return Response(BotSerializer(bot).data, status=status.HTTP_200_OK)

    @action(detail=True, methods=["patch"], url_path="settings")
    def update_settings(self, request, pk=None):  # <-- renamed from `settings`
        bot = self.get_object()
        ser = BotSettingsSerializer(bot, data=request.data, partial=True)
        ser.is_valid(raise_exception=True)
        ser.save()
        structured_log("bot.settings", bot_id=bot.id, changes=ser.validated_data)
        return Response(BotSerializer(bot).data, status=status.HTTP_200_OK)
    
