from django.core.management.base import BaseCommand
from django.contrib.auth.models import Group

class Command(BaseCommand):
    help = "Create default roles (Admin, Ops, ReadOnly)"

    def handle(self, *args, **kwargs):
        for name in ["Admin", "Ops", "ReadOnly"]:
            Group.objects.get_or_create(name=name)
        self.stdout.write(self.style.SUCCESS("Roles created/ensured: Admin, Ops, ReadOnly"))
