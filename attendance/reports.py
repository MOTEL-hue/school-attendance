"""דוחות מסכמים + ייצוא לאקסל + גיבוי מלא."""
import io
import json

from openpyxl import Workbook

from .alerts import counts_for
from .calendar_utils import both_dates, hebrew_date
from .models import (Alert, AlertRule, Attendance, AuthorizedCaller, Contact, LessonSlot, NonSchoolDay,
                     SchoolClass, SchoolSettings, Student)


def summary_rows(students, start, end):
    settings = SchoolSettings.get()
    rows = []
    for st in students:
        ls, ll, ab, abu = counts_for(st, start, end, settings)
        rows.append({"student": st, "late_school": ls, "late_lesson": ll, "absent": ab, "unjustified": abu})
    return rows


HEAD = ["כיתה", "שם", "ת.ז.", "איחורים לבית ספר", "איחורים לשיעור", "ימי חיסור", "מתוכם לא מוצדקים"]


def summary_xlsx(rows, start, end, title):
    wb = Workbook()
    ws = wb.active
    ws.sheet_view.rightToLeft = True
    ws.title = "סיכום"
    ws.append([f"{title} - {both_dates(start)} עד {both_dates(end)}"])
    ws.append(HEAD)
    for r in rows:
        st = r["student"]
        ws.append([st.school_class.name, str(st), st.tz, r["late_school"], r["late_lesson"], r["absent"], r["unjustified"]])
    return _bytes(wb)


def records_sheet(ws, qs):
    ws.append(["תאריך", "תאריך עברי", "עד תאריך", "כיתה", "תלמידה", "ת.ז.", "סוג", "שעה", "שיעור", "סיבה", "מוצדק",
               "דווח דרך", "מי דיווח (טלפון)"])
    for r in qs:
        st = r.student
        ws.append([r.date.strftime("%d/%m/%Y"), hebrew_date(r.date), r.date_to.strftime("%d/%m/%Y") if r.date_to else "",
                   st.school_class.name if st else "", str(st) if st else "לא מזוהה", st.tz if st else r.raw_tz,
                   r.get_kind_display(), r.time.strftime("%H:%M") if r.time else "", r.lesson or "",
                   r.get_reason_display(), "כן" if r.justified else "לא", r.get_source_display(), r.caller_phone])


def records_xlsx(qs, title="דיווחים"):
    wb = Workbook()
    ws = wb.active
    ws.sheet_view.rightToLeft = True
    ws.title = title
    records_sheet(ws, qs)
    return _bytes(wb)


def full_backup_json():
    """גיבוי מלא של כל הנתונים (בלי סיסמת ימות המשיח)."""
    def dump(qs, fields):
        return list(qs.values(*fields))
    data = {
        "classes": dump(SchoolClass.objects.all(), ["name", "order"]),
        "students": dump(Student.objects.all(), ["tz", "first_name", "last_name", "school_class__name", "active",
                                                 "name_recorded", "notes"]),
        "contacts": dump(Contact.objects.all(), ["student__tz", "label", "phone", "receives_alerts", "pin"]),
        "callers": dump(AuthorizedCaller.objects.all(), ["name", "phone", "pin", "active"]),
        "attendance": dump(Attendance.objects.all(), ["student__tz", "raw_tz", "kind", "date", "date_to", "time",
                                                      "lesson", "reason", "justified", "source", "caller_phone",
                                                      "created_at"]),
        "rules": dump(AlertRule.objects.all(), ["name", "active", "metric", "threshold", "repeat", "window",
                                                "window_days", "tzintuk", "tzintuk_voice", "message"]),
        "alerts": dump(Alert.objects.all(), ["rule__name", "student__tz", "multiple", "count", "message",
                                             "created_at", "handled"]),
        "holidays": dump(NonSchoolDay.objects.all(), ["name", "date_from", "date_to"]),
        "lessons": dump(LessonSlot.objects.all(), ["number", "start", "end"]),
    }
    return json.dumps(data, ensure_ascii=False, indent=1, default=str).encode("utf-8")


def _bytes(wb):
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
