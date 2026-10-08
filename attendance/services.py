"""יצירה ותיקון של דיווחי נוכחות (משותף לטלפון ולאתר) + יומן פעולות."""
from datetime import datetime

from django.utils import timezone

from . import tenant
from .alerts import evaluate_student
from .models import Attendance, AuditLog, LessonSlot


def audit(actor, action, details=""):
    AuditLog.objects.create(actor=actor[:60], action=action[:40], details=details[:300])


def lessons_for_date(d, school=None):
    """מערכת השעות של יום מסוים: שיעורי 'כל השנה', ושיעורי העונה (קיץ/חורף) גוברים עליהם."""
    from .models import SchoolSettings
    from .zmanim import season_for
    season = season_for(school or SchoolSettings.get(), d)
    slots = {sl.number: sl for sl in LessonSlot.objects.filter(season="all")}
    slots.update({sl.number: sl for sl in LessonSlot.objects.filter(season=season)})
    return [slots[k] for k in sorted(slots)]


def lesson_for_time(t, d=None):
    """מספר השיעור שבו נמצאת השעה, לפי מערכת השעות של אותו יום (אם הוגדרה)."""
    if not t:
        return None
    from .calendar_utils import today_il
    for slot in lessons_for_date(d or today_il()):
        if slot.start <= t <= slot.end:
            return slot.number
    return None


def create_record(*, student, kind, date, time=None, date_to=None, reason="", justified=None,
                  source="phone", caller_phone="", source_ref="", raw_tz="", actor="טלפון"):
    if source_ref:
        existing = Attendance.objects.filter(source_ref=source_ref).first()
        if existing:
            return existing, False
    if justified is None:
        justified = reason in ("sick", "approved")
    rec = Attendance.objects.create(
        student=student, raw_tz=raw_tz if not student else "", kind=kind, date=date,
        date_to=date_to if date_to and date_to != date else None, time=time, reason=reason,
        lesson=lesson_for_time(time, date) if kind == Attendance.LATE_LESSON else None,
        justified=justified, source=source, caller_phone=caller_phone, source_ref=source_ref)
    who = str(student) if student else f"ת.ז. {raw_tz}"
    audit(actor, "דיווח חדש", f"{who}: {rec.get_kind_display()} {date:%d/%m/%Y}" + (f" {time:%H:%M}" if time else ""))
    school = tenant.current()
    if school is not None:
        type(school).objects.filter(pk=school.pk).update(last_report_at=timezone.now())
    evaluate_student(student)
    return rec, True


def parse_hhmm(text):
    text = (text or "").strip()
    if len(text) in (3, 4) and text.isdigit():
        h, m = int(text[:-2]), int(text[-2:])
        if h < 24 and m < 60:
            return datetime(2000, 1, 1, h, m).time()
    return None
