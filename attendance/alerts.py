"""חישוב התראות: ספירה לפי כללים, יצירת התראות, שליחת צינתוק."""
from datetime import timedelta

from django.db.models import Q
from django.utils import timezone

from . import yemot_client
from .calendar_utils import school_days_between, today_il
from .models import Alert, AlertRule, Attendance, Contact, SchoolSettings, clean_phone


def window_range(rule, today=None, settings=None):
    today = today or today_il()
    settings = settings or SchoolSettings.get()
    if rule.window == "month":
        return today.replace(day=1), today
    if rule.window == "week":
        return today - timedelta(days=(today.weekday() + 1) % 7), today
    if rule.window == "school_year":
        return settings.school_year_start or today.replace(month=1, day=1), today
    if rule.window == "custom" and rule.date_from and rule.date_to:
        return rule.date_from, rule.date_to
    return today - timedelta(days=rule.window_days), today


def counts_for(student, start, end, settings=None):
    """(איחורים לבית ספר, איחורים לשיעור, ימי חיסור, ימי חיסור לא מוצדקים) בטווח."""
    settings = settings or SchoolSettings.get()
    recs = Attendance.objects.filter(student=student).filter(
        Q(date__gte=start, date__lte=end) | Q(kind=Attendance.ABSENT, date__lte=end, date_to__gte=start))
    ls = ll = ab = abu = 0
    for r in recs:
        if r.kind == Attendance.LATE_SCHOOL:
            ls += 1
        elif r.kind == Attendance.LATE_LESSON:
            ll += 1
        else:
            days = school_days_between(max(r.date, start), min(r.date_to or r.date, end), settings)
            ab += len(days)
            if not r.justified:
                abu += len(days)
    return ls, ll, ab, abu


def metric_value(rule, c):
    ls, ll, ab, abu = c
    return {"late_any": ls + ll, "late_school": ls, "late_lesson": ll,
            "absent_any": ab, "absent_unjustified": abu}[rule.metric]


def render_message(rule, student, c):
    ls, ll, ab, _ = c
    text = rule.message
    for k, v in {"{name}": student.first_name, "{late}": str(ls + ll), "{absent}": str(ab),
                 "{count}": str(metric_value(rule, c))}.items():
        text = text.replace(k, v)
    return text


def evaluate_student(student, today=None):
    """נקרא אחרי כל דיווח חדש: יוצר התראות חדשות אם עברו סף."""
    if not student:
        return []
    settings = SchoolSettings.get()
    created = []
    for rule in AlertRule.objects.filter(active=True):
        if rule.class_filter_id and rule.class_filter_id != student.school_class_id:
            continue
        start, end = window_range(rule, today, settings)
        c = counts_for(student, start, end, settings)
        value = metric_value(rule, c)
        if rule.threshold < 1 or value < rule.threshold:
            continue
        top = value // rule.threshold if rule.repeat else 1
        for multiple in range(1, top + 1):
            exists = Alert.objects.filter(rule=rule, student=student, multiple=multiple,
                                          window_start=start).exists()
            if exists:
                continue
            alert = Alert.objects.create(
                rule=rule, student=student, multiple=multiple, count=multiple * rule.threshold if rule.repeat else value,
                message=render_message(rule, student, c), window_start=start,
                announce_pending=rule.notify_caller)
            created.append(alert)
            if rule.tzintuk:
                send_tzintuk(alert, settings)
    return created


def alert_phones(alert):
    rule = alert.rule
    phones = []
    if rule.tzintuk_target in ("contacts", "both"):
        phones += [c.phone for c in Contact.objects.filter(student=alert.student, receives_alerts=True)]
    if rule.tzintuk_target in ("extra", "both"):
        phones += [clean_phone(p) for p in rule.extra_phones.split(",")]
    return sorted({p for p in phones if p})


def send_tzintuk(alert, settings=None):
    settings = settings or SchoolSettings.get()
    phones = alert_phones(alert)
    if not phones:
        alert.tzintuk_status = "אין מספרים לצינתוק"
    else:
        try:
            if alert.rule.tzintuk_voice:
                result = yemot_client.run_voice(settings, phones, alert.message)
            else:
                result = yemot_client.run_tzintuk(settings, phones)
            alert.tzintuk_status = f"נשלח ל-{len(phones)} מספרים: {result}"[:200]
        except yemot_client.YemotError as e:
            alert.tzintuk_status = f"שגיאה: {e}"[:200]
    alert.save(update_fields=["tzintuk_status"])


def pending_announcements(student):
    """התראות שעוד לא הושמעו למתקשרת עבור התלמידה (ומסומנות כמושמעות)."""
    alerts = list(Alert.objects.filter(student=student, announce_pending=True))
    if alerts:
        Alert.objects.filter(pk__in=[a.pk for a in alerts]).update(
            announce_pending=False, announced_at=timezone.now())
    return alerts
