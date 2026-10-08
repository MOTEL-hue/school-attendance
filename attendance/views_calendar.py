"""לוח שנה: חודש ויום, זמני היום, אירועים והודעות להורים, חגים אוטומטיים והגדרות."""
import calendar as pycal
import io
from datetime import date, datetime, timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST
from openpyxl import Workbook

from . import yemot_client, zmanim as zm
from .calendar_utils import both_dates, is_school_day, today_il
from .forms import CalendarEventForm, CalendarSettingsForm
from .models import Attendance, CalendarEvent, Contact, MessageLog, NonSchoolDay, SchoolSettings
from .services import audit, lessons_for_date

KIND_COLORS = {"late_start": "#e58e00", "early_end": "#7a4df0", "exam": "#d93025", "trip": "#0f9d58", "event": "#2457d6"}
MAX_PER_SEND = 1500
CHUNK = 200


def _actor(request):
    return "תמיכה" if request.session.get("support_school") else "מנהל/ת"


def _parse_day(text):
    try:
        return date.fromisoformat(text)
    except (TypeError, ValueError):
        raise Http404


def _fmt(dt):
    return dt.strftime("%H:%M") if dt else ""


@login_required
def calendar_month(request):
    today = today_il()
    try:
        y, m = int(request.GET.get("y", today.year)), int(request.GET.get("m", today.month))
        first = date(y, m, 1)
    except ValueError:
        first = today.replace(day=1)
    school = SchoolSettings.get()
    last = date(first.year + (first.month == 12), first.month % 12 + 1, 1) - timedelta(days=1)
    weeks = pycal.Calendar(firstweekday=6).monthdatescalendar(first.year, first.month)
    span_start, span_end = weeks[0][0], weeks[-1][-1]
    events = list(CalendarEvent.objects.filter(date_from__lte=span_end, date_to__gte=span_start))
    offs = list(NonSchoolDay.objects.filter(date_from__lte=span_end, date_to__gte=span_start))
    changes = {d: s for yr in {span_start.year, span_end.year} for d, s in zm.dst_changes(yr)}
    rows = []
    for wk in weeks:
        row = []
        for d in wk:
            lab = zm.day_label(d)
            evs = [e for e in events if e.date_from <= d <= e.date_to]
            off = next((o for o in offs if o.date_from <= d <= o.date_to), None)
            row.append({"date": d, "in_month": d.month == first.month, "today": d == today, "hday": lab["hday"],
                        "festival": lab["festival"], "rc": lab["rosh_chodesh"], "parsha": lab["parsha"],
                        "events": [{"e": e, "color": KIND_COLORS[e.kind]} for e in evs], "off_name": off.name if off else "",
                        "weekend_off": d.weekday() in school.days_off_set, "dst": changes.get(d)})
        rows.append(row)
    prev = first - timedelta(days=1)
    nxt = last + timedelta(days=1)
    hebrew_range = f"{zm.day_label(first)['hebrew'].split(' ', 1)[1]} - {zm.day_label(last)['hebrew'].split(' ', 1)[1]}"
    return render(request, "attendance/calendar.html", {
        "weeks": rows, "title": f"{zm.HEB_MONTHS[first.month]} {first.year}", "hebrew_range": hebrew_range,
        "prev": prev, "next": nxt, "today": today, "season": zm.season_label(zm.season_for(school, today)),
        "city": zm.CITIES.get(school.city_key, zm.CITIES["jerusalem"])[0],
        "dayheads": ["ראשון", "שני", "שלישי", "רביעי", "חמישי", "שישי", "שבת"],
        "legend": [(v, dict(CalendarEvent.KINDS)[k]) for k, v in KIND_COLORS.items()]})


@login_required
def calendar_day(request, iso):
    d = _parse_day(iso)
    school = SchoolSettings.get()
    lab = zm.day_label(d)
    z = zm.zmanim_for(school, d)
    rows = [(label, _fmt(z[key])) for key, label in zm.ZMAN_ROWS if z.get(key)]
    start, end = zm.school_hours(school, d)
    counts = {"late": Attendance.objects.filter(date=d).exclude(kind="absent").count(),
              "absent": Attendance.objects.filter(kind="absent", date__lte=d).filter(
                  date_to__gte=d).count() + Attendance.objects.filter(kind="absent", date=d, date_to__isnull=True).count()}
    return render(request, "attendance/calendar_day.html", {
        "d": d, "lab": lab, "rows": rows, "season": zm.season_label(zm.season_for(school, d)),
        "events": CalendarEvent.objects.filter(date_from__lte=d, date_to__gte=d),
        "off": NonSchoolDay.objects.filter(date_from__lte=d, date_to__gte=d).first(),
        "is_school_day": is_school_day(d), "lessons": lessons_for_date(d, school), "start": start, "end": end,
        "counts": counts, "both": both_dates(d), "prev": d - timedelta(days=1), "next": d + timedelta(days=1),
        "city": zm.CITIES.get(school.city_key, zm.CITIES["jerusalem"])[0]})


@login_required
def event_edit(request, pk=None):
    ev = get_object_or_404(CalendarEvent, pk=pk) if pk else None
    initial = {}
    if not ev and request.GET.get("date"):
        dd = _parse_day(request.GET["date"])
        initial = {"date_from": dd, "date_to": dd, "kind": request.GET.get("kind", "event")}
    form = CalendarEventForm(request.POST or None, instance=ev, initial=initial)
    if request.method == "POST" and form.is_valid():
        ev = form.save()
        audit(_actor(request), "אירוע בלוח שנה", str(ev))
        messages.success(request, "האירוע נשמר")
        if ev.kind in ("late_start", "early_end"):
            return redirect("event_send", pk=ev.pk)
        return redirect("calendar_day", iso=ev.date_from.isoformat())
    return render(request, "attendance/form.html", {
        "form": form, "title": "אירוע בלוח השנה" if ev else "אירוע חדש בלוח השנה",
        "help": "התחלה מאוחרת או סיום מוקדם: ציינו את השעה. האירוע יוצג כהודעה באתר ויושמע בטלפון למי שמתקשרת. "
                "אחרי השמירה אפשר גם לשלוח אותו להורים בצינתוק או בשיחה.",
        "delete": ("event_delete", ev.pk, None) if ev else None})


@login_required
@require_POST
def event_delete(request, pk):
    ev = get_object_or_404(CalendarEvent, pk=pk)
    day = ev.date_from.isoformat()
    audit(_actor(request), "מחיקת אירוע", str(ev))
    ev.delete()
    messages.success(request, "האירוע נמחק")
    return redirect("calendar_day", iso=day)


def recipients_for(event, parents_only=True):
    qs = Contact.objects.filter(student__active=True)
    if event.class_filter_id:
        qs = qs.filter(student__school_class_id=event.class_filter_id)
    if parents_only:
        qs = qs.filter(label__in=["אמא", "אבא"])
    return sorted({c.phone for c in qs if c.phone})


def default_message(event, school):
    d = event.date_from
    day = f"ביום {zm.DAY_NAMES_HE[d.weekday()]} {d:%d/%m}"
    if event.kind == CalendarEvent.LATE_START and event.time:
        return f"שלום הורים. {day} הלימודים ב{school.school_name} יתחילו בשעה {event.time:%H:%M}."
    if event.kind == CalendarEvent.EARLY_END and event.time:
        return f"שלום הורים. {day} הלימודים ב{school.school_name} יסתיימו בשעה {event.time:%H:%M}."
    return f"שלום הורים. {day}: {event.title}."


@login_required
def event_send(request, pk):
    ev = get_object_or_404(CalendarEvent, pk=pk)
    school = SchoolSettings.get()
    parents_only = request.POST.get("parents_only", "1") == "1" if request.method == "POST" else True
    phones = recipients_for(ev, parents_only)
    text = (request.POST.get("text") or default_message(ev, school)).strip()
    recent = MessageLog.objects.filter(event=ev, created_at__gte=timezone.now() - timedelta(minutes=30))
    if request.method == "POST":
        channels = [c for c in ("tzintuk", "voice") if request.POST.get(c)]
        if request.session.get("support_school"):
            raise Http404
        if not request.POST.get("confirm"):
            messages.error(request, "יש לסמן אישור לשליחה.")
        elif not channels:
            messages.error(request, "לא נבחר ערוץ שליחה.")
        elif not phones:
            messages.error(request, "אין מספרי טלפון של הורים לשליחה. הוסיפו טלפונים בכרטיסי התלמידות.")
        elif len(phones) > MAX_PER_SEND:
            messages.error(request, f"יותר מדי נמענים ({len(phones)}). המקסימום לשליחה אחת: {MAX_PER_SEND}.")
        elif any(recent.filter(channel=c).exists() for c in channels):
            messages.error(request, "כבר נשלחה הודעה בערוץ הזה לפני פחות מחצי שעה. מניעת שליחה כפולה (עולה כסף).")
        else:
            for ch in channels:
                ok = fail = 0
                err = ""
                for i in range(0, len(phones), CHUNK):
                    part = phones[i:i + CHUNK]
                    try:
                        if ch == "tzintuk":
                            yemot_client.run_tzintuk(school, part)
                        else:
                            yemot_client.run_voice(school, part, text)
                        ok += len(part)
                    except yemot_client.YemotError as e:
                        fail += len(part)
                        err = str(e)
                status = f"נשלח ל-{ok}" + (f", נכשל ל-{fail}: {err}" if fail else "")
                MessageLog.objects.create(event=ev, channel=ch, recipients=ok, status=status[:200], text=text[:300])
                (messages.success if not fail else messages.error)(
                    request, f"{'צינתוק' if ch == 'tzintuk' else 'שיחה קולית'}: {status}")
            audit(_actor(request), "הודעה להורים", f"{ev.title}: {', '.join(channels)} ל-{len(phones)}")
            return redirect("event_send", pk=ev.pk)
    return render(request, "attendance/event_send.html", {
        "ev": ev, "text": text, "count": len(phones), "parents_only": parents_only, "log": ev.messages.all()[:10],
        "has_yemot": bool(school.yemot_token), "has_template": bool(school.voice_template_id)})


@login_required
def calendar_settings(request):
    school = SchoolSettings.get()
    form = CalendarSettingsForm(request.POST or None, instance=school)
    if request.method == "POST" and form.is_valid():
        form.save()
        audit(_actor(request), "הגדרות לוח שנה")
        messages.success(request, "ההגדרות נשמרו")
        return redirect("calendar_settings")
    today = today_il()
    z = zm.zmanim_for(school, today)
    return render(request, "attendance/calendar_settings.html", {
        "form": form, "today_rows": [(label, _fmt(z[k])) for k, label in zm.ZMAN_ROWS[:12] if z.get(k)],
        "season": zm.season_label(zm.season_for(school, today)),
        "changes": [(d, zm.season_label(s)) for yr in (today.year, today.year + 1) for d, s in zm.dst_changes(yr)
                    if d >= today][:3], "city": zm.CITIES.get(school.city_key, zm.CITIES["jerusalem"])[0]})


@login_required
def zmanim_table(request):
    today = today_il()
    try:
        y, m = int(request.GET.get("y", today.year)), int(request.GET.get("m", today.month))
        first = date(y, m, 1)
    except ValueError:
        first = today.replace(day=1)
    school = SchoolSettings.get()
    last = date(first.year + (first.month == 12), first.month % 12 + 1, 1) - timedelta(days=1)
    cols = [("hanetz", "הנץ"), ("shma_gra", "ק״ש גר״א"), ("shma_mga", "ק״ש מג״א"), ("chatzos", "חצות"),
            ("mincha_gedola", "מנחה גדולה"), ("plag", "פלג"), ("shkia", "שקיעה"), ("tzais", "צאת הכוכבים"),
            ("candle", "הדלקה"), ("havdalah", "הבדלה")]
    data, d = [], first
    while d <= last:
        z, lab = zm.zmanim_for(school, d), zm.day_label(d)
        data.append({"d": d, "lab": lab, "cells": [_fmt(z[k]) for k, _ in cols]})
        d += timedelta(days=1)
    if request.GET.get("export") == "xlsx":
        wb = Workbook()
        ws = wb.active
        ws.sheet_view.rightToLeft = True
        ws.append(["תאריך", "תאריך עברי", "יום", "חג/מועד"] + [c[1] for c in cols])
        for r in data:
            ws.append([r["d"].strftime("%d/%m/%Y"), r["lab"]["hebrew"], r["lab"]["weekday"],
                       r["lab"]["festival"] or r["lab"]["parsha"]] + r["cells"])
        buf = io.BytesIO()
        wb.save(buf)
        resp = HttpResponse(buf.getvalue(), content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        resp["Content-Disposition"] = f'attachment; filename="zmanim-{first:%Y-%m}.xlsx"'
        return resp
    prev, nxt = first - timedelta(days=1), last + timedelta(days=1)
    return render(request, "attendance/zmanim_table.html", {
        "data": data, "cols": cols, "title": f"{zm.HEB_MONTHS[first.month]} {first.year}", "prev": prev, "next": nxt,
        "city": zm.CITIES.get(school.city_key, zm.CITIES["jerusalem"])[0], "school": school.school_name,
        "y": first.year, "m": first.month})


def _proposals(year):
    """חגים ותעניות בשנת הלימודים (ספטמבר year עד ספטמבר year+1), מקובצים לטווחים רצופים."""
    groups, d = [], date(year, 8, 20)
    end = date(year + 1, 8, 31)
    while d <= end:
        name = zm.festival(d)
        if name in zm.OFF_DEFAULT | zm.OFF_OPTIONAL:
            if groups and groups[-1]["name"] == name and groups[-1]["to"] == d - timedelta(days=1):
                groups[-1]["to"] = d
            else:
                groups.append({"name": name, "from": d, "to": d})
        d += timedelta(days=1)
    existing = {(o.name, o.date_from) for o in NonSchoolDay.objects.all()}
    for i, g in enumerate(groups):
        g["id"] = f"{i}|{g['name']}|{g['from']}|{g['to']}"
        g["default"] = g["name"] in zm.OFF_DEFAULT
        g["exists"] = (g["name"], g["from"]) in existing
        g["hebrew"] = zm.day_label(g["from"])["hebrew"]
    return groups


@login_required
def holidays_auto(request):
    today = today_il()
    year = int(request.GET.get("year", today.year if today.month >= 8 else today.year - 1))
    props = _proposals(year)
    if request.method == "POST":
        if request.session.get("support_school"):
            raise Http404
        picked = set(request.POST.getlist("pick"))
        created = 0
        for g in props:
            if g["id"] in picked and not g["exists"]:
                NonSchoolDay.objects.create(name=g["name"], date_from=g["from"], date_to=g["to"])
                created += 1
        audit(_actor(request), "חגים אוטומטיים", f"נוספו {created}")
        messages.success(request, f"נוספו {created} חופשות וחגים")
        return redirect("crud_list", kind="holidays")
    return render(request, "attendance/holidays_auto.html", {"props": props, "year": year, "years": [year - 1, year, year + 1]})
