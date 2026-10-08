import hashlib
import hmac
import os
import re
import secrets

from django.conf import settings as dj
from django.db import migrations

TENANT_MODELS = ["SchoolSettings", "LessonSlot", "NonSchoolDay", "SchoolClass", "Student", "Contact",
                 "AuthorizedCaller", "Attendance", "AlertRule", "Alert", "AuditLog", "PendingAction"]


def assign(apps, schema_editor):
    School = apps.get_model("attendance", "School")
    has_rows = any(apps.get_model("attendance", m).objects.filter(school__isnull=True).exists() for m in TENANT_MODELS)
    if not has_rows:
        return
    settings_row = apps.get_model("attendance", "SchoolSettings").objects.filter(school__isnull=True).first()
    school = School.objects.create(
        name=(settings_row.school_name if settings_row else "בית הספר"), status="active",
        phone_secret=os.environ.get("PHONE_SECRET") or secrets.token_urlsafe())
    for m in TENANT_MODELS:
        apps.get_model("attendance", m).objects.filter(school__isnull=True).update(school=school)
    for m in ("Contact", "AuthorizedCaller"):
        for row in apps.get_model("attendance", m).objects.exclude(pin=""):
            if not re.fullmatch(r"[0-9a-f]{40}", row.pin):
                row.pin = hmac.new(dj.SECRET_KEY.encode(), f"pin:{row.pin}".encode(), hashlib.sha256).hexdigest()[:40]
                row.save(update_fields=["pin"])


class Migration(migrations.Migration):
    dependencies = [("attendance", "0003_multi_school")]
    operations = [migrations.RunPython(assign, migrations.RunPython.noop)]
