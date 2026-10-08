"""בידוד בין בתי ספר: כל שאילתה על מודל של בית ספר מסוננת אוטומטית לבית הספר הנוכחי.

בית הספר הנוכחי נקבע פעם אחת בתחילת בקשה (middleware או כתובת הטלפון). בלי בית ספר נוכחי
מוחזרת רשימה ריקה, ושמירה נכשלת. כך שכחת סינון לא יכולה לחשוף נתונים של בית ספר אחר.
פעולות ברמת המערכת (המנהל הראשי) משתמשות ב-`all_objects` במפורש.
"""
from contextvars import ContextVar

from django.db import models

_current = ContextVar("current_school", default=None)


def current():
    return _current.get()


def activate(school):
    """מחזיר token לשחזור עם deactivate."""
    return _current.set(school)


def deactivate(token):
    _current.reset(token)


class TenantManager(models.Manager):
    def get_queryset(self):
        qs = super().get_queryset()
        school = current()
        return qs.filter(school=school) if school is not None else qs.none()


class TenantModel(models.Model):
    school = models.ForeignKey("attendance.School", on_delete=models.CASCADE, editable=False, related_name="+")

    objects = TenantManager()
    all_objects = models.Manager()

    class Meta:
        abstract = True
        base_manager_name = "all_objects"

    def save(self, *args, **kwargs):
        if self.school_id is None:
            school = current()
            if school is None:
                raise RuntimeError("אין בית ספר נוכחי: אי אפשר לשמור")
            self.school = school
        super().save(*args, **kwargs)
