import hashlib
import hmac
from django.conf import settings
from django.http import HttpResponseForbidden
from rest_framework import viewsets, mixins, status
from rest_framework.decorators import action
from rest_framework.response import Response
from core.metrics import signals_ingested_total
from core.permissions import IsOps, IsOpsOrReadOnly
from execution.services.brokers import dispatch_place_order, dispatch_cancel_order
from execution.services.decision import make_decision_from_signal
from execution.services.fanout import fanout_orders
from execution.services.psychology import bot_is_available_for_trading
from .models import Decision, Signal, Order
from .serializers import AlertWebhookSerializer, SignalSerializer, OrderSerializer, QuickOrderCreateSerializer, OrderCreateFromDecisionSerializer, OrderTransitionSerializer
from rest_framework.decorators import api_view
from .services.orchestrator import create_order_from_decision, update_order_status
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.permissions import AllowAny
from django.views.decorators.csrf import csrf_exempt


def _verify_alert_signature(request, secret: str) -> bool:
    """
    Validate X-ALERT-SIGNATURE header using HMAC-SHA256 of the raw body.
    Accepts either the bare hex digest or 'sha256=<digest>'.
    """
    if not secret:
        return True
    signature = request.headers.get("X-ALERT-SIGNATURE", "")
    if not signature:
        return False
    if signature.startswith("sha256="):
        signature = signature.split("=", 1)[1]
    computed = hmac.new(secret.encode("utf-8"), request.body or b"", hashlib.sha256).hexdigest()
    return hmac.compare_digest(computed, signature)


class SignalViewSet(mixins.CreateModelMixin, mixins.ListModelMixin, viewsets.GenericViewSet):
    permission_classes = [IsOpsOrReadOnly]
    
    queryset = Signal.objects.all().order_by("-received_at")
    serializer_class = SignalSerializer

    @action(detail=True, methods=["post"], url_path="decide")
    def decide(self, request, pk=None):
        signal = self.get_object()
        decision = make_decision_from_signal(signal)
        return Response({
            "decision_id": decision.id,
            "action": decision.action,
            "reason": decision.reason,
            "score": decision.score,
            "params": decision.params,
        }, status=status.HTTP_201_CREATED)

class OrderViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
    permission_classes = [IsOpsOrReadOnly]
    
    queryset = Order.objects.all().order_by("-created_at")
    serializer_class = OrderSerializer

    @action(detail=False, methods=["post"], url_path="quick-create")
    def quick_create(self, request):
        ser = QuickOrderCreateSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        order = ser.save()
        return Response(OrderSerializer(order).data, status=status.HTTP_201_CREATED)
    
    @action(detail=False, methods=["post"], url_path="from-decision")
    def from_decision(self, request):
        ser = OrderCreateFromDecisionSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        decision = ser.validated_data["decision"]
        broker_account = ser.validated_data["broker_account"]
        qty = str(ser.validated_data["qty"])
        order, created = create_order_from_decision(decision, broker_account, qty)
        data = OrderSerializer(order).data
        data["created"] = created
        return Response(data, status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)

    @action(detail=True, methods=["post"], url_path="transition")
    def transition(self, request, pk=None):
        order = self.get_object()
        ser = OrderTransitionSerializer(data=request.data)
        ser.is_valid(raise_exception=True)

        try:
            order = update_order_status(
                order,
                ser.validated_data["to_status"],
                price=ser.validated_data.get("price"),
                error_msg=ser.validated_data.get("error_msg"),
            )
        except ValueError as e:
            return Response({"detail": str(e)}, status=status.HTTP_400_BAD_REQUEST)

        return Response(OrderSerializer(order).data, status=status.HTTP_200_OK)


        
    @action(detail=True, methods=["post"], url_path="send")
    def send(self, request, pk=None):
        order = self.get_object()
        if order.status != "new":
            return Response({"detail": "Only 'new' orders can be sent."}, status=status.HTTP_400_BAD_REQUEST)
        dispatch_place_order(order)
        return Response(OrderSerializer(order).data, status=status.HTTP_200_OK)

    @action(detail=True, methods=["post"], url_path="cancel")
    def cancel(self, request, pk=None):
        order = self.get_object()
        dispatch_cancel_order(order)
        return Response(OrderSerializer(order).data, status=status.HTTP_200_OK)

@csrf_exempt
@api_view(["POST"])
@authentication_classes([])
@permission_classes([AllowAny])
def alert_webhook(request):
    
    expected = getattr(settings, "ALERT_WEBHOOK_TOKEN", None)
    if expected and request.headers.get("X-ALERT-TOKEN") != expected:
        return HttpResponseForbidden("Invalid token")

    webhook_secret = getattr(settings, "ALERT_WEBHOOK_SECRET", None) or getattr(settings, "EXECUTION_ALERT_SECRET", None)
    if webhook_secret and not _verify_alert_signature(request, webhook_secret):
        return HttpResponseForbidden("Invalid signature")

    ser = AlertWebhookSerializer(data=request.data)
    ser.is_valid(raise_exception=True)
    signal, created = ser.save()

    
    try:
        from core.metrics import signals_ingested_total
        signals_ingested_total.labels(signal.source, signal.symbol, signal.timeframe).inc()
        from core.utils import audit_log
        audit_log("signal.ingest", "Signal", signal.id, {"source": signal.source, "symbol": signal.symbol})
    except Exception:
        pass

    
    orders_sent = 0
    decision_id = None
    if signal.bot and bot_is_available_for_trading(signal.bot) and getattr(signal.bot, "auto_trade", False):
        # 1) Strategy -> Decision
        decision = make_decision_from_signal(signal)
        decision_id = decision.id

        if decision.action == "open":  # only open orders in this MVP
            # 2) Fan-out (qty=None -> uses bot.default_qty)
            created_orders = fanout_orders(decision, master_qty=None)

            # 3) Send each order to its connector (Paper/MT5)
            for order, _created in created_orders:
                try:
                    dispatch_place_order(order)
                    orders_sent += 1
                except Exception as e:
                    # soft-fail: one follower failing shouldn't break the whole webhook
                    try:
                        from core.utils import audit_log
                        audit_log("order.send.error", "Order", order.id, {"error": str(e)})
                    except Exception:
                        pass

    return Response(
        {"detail": "External alerts are disabled. Bot now uses internal engine only."},
        status=status.HTTP_410_GONE,
    )


@api_view(["post"])
@permission_classes([IsOps])
def decision_fanout(request, decision_id: int):
    # master size from request (MVP default 0.10)
    master_qty = str(request.data.get("qty", "0.10"))
    decision = Decision.objects.get(id=decision_id)
    qty = request.data.get("qty")
    created = fanout_orders(decision, qty, master_qty)
    return Response(
        {"count": len(created), "orders": [OrderSerializer(o).data for o, _ in created]},
        status=status.HTTP_201_CREATED if created else status.HTTP_200_OK
    )
