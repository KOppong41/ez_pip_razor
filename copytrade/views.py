from rest_framework import viewsets, mixins, status
from rest_framework.decorators import action
from rest_framework.response import Response

from core.permissions import IsOpsOrReadOnly
from .models import Follower
from .serializers import FollowerSerializer

class FollowerViewSet(mixins.CreateModelMixin, mixins.ListModelMixin, viewsets.GenericViewSet):
    permission_classes = [IsOpsOrReadOnly]
    
    queryset = Follower.objects.all().order_by("-created_at")
    serializer_class = FollowerSerializer
