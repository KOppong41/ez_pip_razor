from django.core.management.base import BaseCommand, CommandError
from django.contrib.auth import get_user_model

from notifications.utils import notify_user


class Command(BaseCommand):
    help = "Send a test notification to a user and record the result in NotificationLog."

    def add_arguments(self, parser):
        parser.add_argument("username", help="Username to notify")
        parser.add_argument(
            "--event",
            default="order_filled",
            help="Event type to use (default: order_filled)",
        )

    def handle(self, *args, **options):
        username = options["username"]
        event = options["event"]
        User = get_user_model()
        try:
            user = User.objects.get(username=username)
        except User.DoesNotExist:
            raise CommandError(f"User '{username}' not found")

        notify_user(
            user=user,
            event_type=event,
            subject=f"Test notification ({event})",
            message="This is a test notification.",
            payload={"source": "send_test_notification"},
        )
        self.stdout.write(self.style.SUCCESS(f"Test notification enqueued/logged for {username}"))
