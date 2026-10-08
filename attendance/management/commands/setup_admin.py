import os

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "יוצר את משתמש המנהל (admin) אם אין, לפי ADMIN_PASSWORD"

    def handle(self, *a, **kw):
        pw = os.environ.get("ADMIN_PASSWORD")
        user = User.objects.filter(username="admin").first()
        if not user and pw:
            User.objects.create_superuser("admin", password=pw)
            self.stdout.write("נוצר משתמש admin")
        elif not user:
            self.stdout.write("לא הוגדר ADMIN_PASSWORD - לא נוצר משתמש")
        elif pw and os.environ.get("RESET_ADMIN_PASSWORD") == "1":
            user.set_password(pw)
            user.save()
            self.stdout.write("סיסמת admin עודכנה")
