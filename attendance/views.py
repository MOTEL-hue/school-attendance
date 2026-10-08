import hashlib
import secrets
from datetime import timedelta

from django.conf import settings as dj
from django.contrib import messages
from django.contrib.auth import login as auth_login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.core.cache import cache
from django.db.models import Count, Q
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from . import alerts as alert_mod
from . import tenant
from . import importers, phone, reports, yemot_client
from .calendar_utils import both_dates, hebrew_date, now_il, today_il
from .forms import (CallerForm, ClassForm, ContactForm, HolidayForm, ImportForm, LessonForm, RecordForm, RuleForm,
                    SettingsForm, StudentForm)
from .models import (Alert, AlertRule, Attendance, AuditLog, AuthorizedCaller, Contact, LessonSlot, NonSchoolDay,
                     School, SchoolClass, SchoolSettings, Student)
from .services import audit, create_record, parse_hhmm


def actor(request):
    return "תמיכה" if request.session.get("support_school") else "מנהל/ת"


# ---------- טלפון ----------
@csrf_exempt
def yemot_api(request, secret):
    school = School.objects.filter(phone_secret=secret).first()
    if not school:
        raise Http404
    if school.status != School.ACTIVE:
        return HttpResponse("id_list_message=t-המערכת אינה פעילה פנו להנהלה&go_to_folder=hangup",
                            content_type="text/plain; charset=utf-8")
    params = {k: v for k, v in (request.POST if request.method == "POST" else request.GET).items()}
    token = tenant.activate(school)
    try:
        out = phone.handle(params)
    finally:
        tenant.deactivate(token)
    return HttpResponse(out, content_type="text/plain; charset=utf-8")


def phone_url(request):
    return f"{request.build_absolute_uri('/').rstrip('/')}/phone/{request.school.phone_secret}/"


# ---------- דף הבית ----------
@login_required
def dashboard(request):
    today = today_il()
    todays = Attendance.objects.filter(Q(date=today) | Q(kind="absent", date__lte=today, date_to__gte=today))
    ctx = {
        "today": both_dates(today),
        "late_today": todays.exclude(kind="absent").count(),
        "absent_today": todays.filter(kind="absent").count(),
        "open_alerts": Alert.objects.filter(handled=False, rule__notify_site=True).select_related("student", "rule")[:8],
        "open_alerts_count": Alert.objects.filter(handled=False, rule__notify_site=True).count(),
        "unmatched": Attendance.objects.filter(student__isnull=True, resolved=False).count(),
        "recent": Attendance.objects.select_related("student", "student__school_class")[:10],
        "students": Student.objects.filter(active=True).count(),
        "s": SchoolSettings.get(),
    }
    from . import zmanim as zm
    sch = ctx["s"]
    z, lab = zm.zmanim_for(sch, today), zm.day_label(today)
    ctx["day"] = {"lab": lab, "season": zm.season_label(zm.season_for(sch, today)),
                  "city": zm.CITIES.get(sch.city_key, zm.CITIES["jerusalem"])[0],
                  "times": [(label, z[k].strftime("%H:%M")) for k, label in zm.ZMAN_ROWS
                            if k in ("hanetz", "shma_gra", "chatzos", "shkia", "candle", "havdalah") and z.get(k)]}
    steps = onboarding_steps(request)
    ctx["onboarding_left"] = sum(not x["done"] for x in steps)
    ctx["onboarding_total"] = len(steps)
    ctx["onboarding_pct"] = int(100 * (len(steps) - ctx["onboarding_left"]) / len(steps))
    return render(request, "attendance/dashboard.html", ctx)


# ---------- דיווח דרך האתר ----------
@login_required
def web_report(request):
    st = None
    if request.method == "POST":
        tz = "".join(c for c in request.POST.get("tz", "") if c.isdigit())
        st = Student.objects.filter(tz__in={tz.zfill(9), tz}, active=True).first() if tz else \
            Student.objects.filter(pk=request.POST.get("student") or 0).first()
        kind = request.POST.get("kind")
        try:
            from datetime import date as _d
            d = _d.fromisoformat(request.POST.get("date") or today_il().isoformat())
            d2 = request.POST.get("date_to")
            d2 = _d.fromisoformat(d2) if d2 else None
        except ValueError:
            d, d2 = None, None
        t = request.POST.get("time")
        tm = parse_hhmm((t or "").replace(":", "")) if t else None
        if not st:
            messages.error(request, "לא נמצאה תלמידה. הקלידו מספר זהות או בחרו מהרשימה.")
        elif kind not in dict(Attendance.KINDS) or not d:
            messages.error(request, "סוג או תאריך לא תקינים.")
        elif kind != "absent" and not tm:
            tm = now_il().time().replace(second=0, microsecond=0)
        if st and kind in dict(Attendance.KINDS) and d and not list(messages.get_messages(request)):
            create_record(student=st, kind=kind, date=d, date_to=d2, time=tm if kind != "absent" else None,
                          reason=request.POST.get("reason", ""), source="web", actor=actor(request))
            messages.success(request, f"נרשם: {st} - {dict(Attendance.KINDS)[kind]}")
            return redirect("web_report")
    return render(request, "attendance/report.html", {
        "students": Student.objects.filter(active=True).select_related("school_class"),
        "today": today_il().isoformat(), "now": now_il().strftime("%H:%M"), "reasons": Attendance.REASONS[:3]})


# ---------- דיווחים: רשימה ועריכה ----------
def _filtered_records(request):
    qs = Attendance.objects.select_related("student", "student__school_class")
    g = request.GET
    if g.get("from"):
        qs = qs.filter(Q(date__gte=g["from"]) | Q(date_to__gte=g["from"]))
    if g.get("to"):
        qs = qs.filter(date__lte=g["to"])
    if g.get("class"):
        qs = qs.filter(student__school_class_id=g["class"])
    if g.get("kind"):
        qs = qs.filter(kind=g["kind"])
    if g.get("q"):
        q = g["q"]
        qs = qs.filter(Q(student__first_name__icontains=q) | Q(student__last_name__icontains=q) | Q(student__tz__icontains=q))
    if g.get("unmatched"):
        qs = qs.filter(student__isnull=True)
    return qs


@login_required
def records(request):
    qs = _filtered_records(request)
    if request.GET.get("export") == "xlsx":
        return _xlsx(reports.records_xlsx(qs[:20000]), "attendance-records.xlsx")
    rows = list(qs[:300])
    for r in rows:
        r.heb = hebrew_date(r.date)
    return render(request, "attendance/records.html", {
        "rows": rows, "classes": SchoolClass.objects.all(), "kinds": Attendance.KINDS, "g": request.GET,
        "total": qs.count()})


@login_required
def record_edit(request, pk=None):
    rec = get_object_or_404(Attendance, pk=pk) if pk else None
    form = RecordForm(request.POST or None, instance=rec)
    if request.method == "POST" and form.is_valid():
        obj = form.save(commit=False)
        obj.lesson = obj.lesson if obj.kind == "late_lesson" else None
        obj.save()
        audit(actor(request), "תיקון דיווח" if rec else "דיווח ידני", f"{obj}")
        alert_mod.evaluate_student(obj.student)
        messages.success(request, "נשמר")
        return redirect("records")
    return render(request, "attendance/form.html", {
        "form": form, "title": "תיקון דיווח" if rec else "דיווח חדש",
        "help": "אפשר לתקן סוג, תאריך, שעה, סיבה ולסמן אם החיסור מוצדק. שינוי נרשם ביומן הפעולות. "
                "לדיווח 'לא מזוהה' בחרו תלמידה כדי לשייך אותו."})


@login_required
@require_POST
def record_delete(request, pk):
    rec = get_object_or_404(Attendance, pk=pk)
    audit(actor(request), "מחיקת דיווח", f"{rec} ({rec.get_kind_display()} {rec.date:%d/%m/%Y})")
    rec.delete()
    messages.success(request, "הדיווח נמחק")
    return redirect("records")


# ---------- תלמידות ----------
@login_required
def students(request):
    qs = Student.objects.select_related("school_class")
    cls, q = request.GET.get("class"), request.GET.get("q")
    if cls:
        qs = qs.filter(school_class_id=cls)
    if q:
        qs = qs.filter(Q(first_name__icontains=q) | Q(last_name__icontains=q) | Q(tz__icontains=q))
    if not request.GET.get("all"):
        qs = qs.filter(active=True)
    return render(request, "attendance/students.html", {"rows": qs[:500], "classes": SchoolClass.objects.all(),
                                                        "g": request.GET})


@login_required
def student_edit(request, pk=None):
    st = get_object_or_404(Student, pk=pk) if pk else None
    form = StudentForm(request.POST or None, instance=st)
    cform = ContactForm(prefix="c")
    if request.method == "POST" and "add_contact" in request.POST and st:
        cform = ContactForm(request.POST, prefix="c")
        if cform.is_valid():
            c = cform.save(commit=False)
            c.student = st
            c.save()
            messages.success(request, "הטלפון נוסף")
            return redirect("student_edit", pk=st.pk)
    elif request.method == "POST" and "reset_name" in request.POST and st:
        st.name_recorded = False
        st.save(update_fields=["name_recorded"])
        messages.success(request, "בשיחה הבאה על התלמידה תתבקש הקלטה חדשה של השם")
        return redirect("student_edit", pk=st.pk)
    elif request.method == "POST" and form.is_valid():
        st = form.save()
        audit(actor(request), "עדכון תלמידה", str(st))
        messages.success(request, "נשמר")
        return redirect("student_edit", pk=st.pk)
    ctx = {"form": form, "student": st, "cform": cform}
    if st:
        ctx["contacts"] = st.contacts.all()
        today = today_il()
        ctx["records"] = [(r, hebrew_date(r.date)) for r in st.records.all()[:100]]
        ctx["alerts"] = st.alerts.all()[:10]
        start = today - timedelta(days=183)
        ls, ll, ab, abu = alert_mod.counts_for(st, start, today)
        ctx["stats"] = {"late_school": ls, "late_lesson": ll, "absent": ab, "unjustified": abu}
    return render(request, "attendance/student.html", ctx)


@login_required
@require_POST
def contact_delete(request, pk):
    c = get_object_or_404(Contact, pk=pk)
    sid = c.student_id
    c.delete()
    return redirect("student_edit", pk=sid)


@login_required
def students_import(request):
    form = ImportForm(request.POST or None, request.FILES or None)
    result = None
    if request.method == "POST" and form.is_valid():
        try:
            result = importers.import_students(form.cleaned_data["file"])
            audit(actor(request), "ייבוא תלמידות", f"נוספו {result[0]}, עודכנו {result[1]}")
        except Exception as e:  # noqa: BLE001
            messages.error(request, f"הקובץ לא נקרא: {e}")
    return render(request, "attendance/import.html", {"form": form, "result": result})


@login_required
def import_template(request):
    return _xlsx(importers.template_xlsx(), "students-template.xlsx")


@login_required
@require_POST
def new_year(request):
    """מעבר לשנת לימודים חדשה: מעביר כיתה לכיתה לפי מיפוי, ומאפס מונה."""
    moves = []  # קודם אוספים, כדי שתלמידות שהועברו לא יועברו שוב בכיתה הבאה
    for cls in SchoolClass.objects.all():
        target = request.POST.get(f"map_{cls.pk}")
        ids = list(cls.students.values_list("pk", flat=True))
        if target == "graduate":
            moves.append((ids, {"active": False}))
        elif target and target.isdigit() and int(target) != cls.pk:
            moves.append((ids, {"school_class_id": int(target)}))
    for ids, change in moves:
        Student.objects.filter(pk__in=ids).update(**change)
    ss = SchoolSettings.get()
    ss.school_year_start = today_il()
    ss.save(update_fields=["school_year_start"])
    audit(actor(request), "מעבר שנה", "כיתות הועברו; נספרת שנה חדשה")
    messages.success(request, "המעבר בוצע. הדיווחים הישנים נשמרו בהיסטוריה.")
    return redirect("students")


@login_required
def new_year_page(request):
    return render(request, "attendance/new_year.html", {"classes": SchoolClass.objects.all()})


# ---------- דוחות ----------
@login_required
def report_summary(request):
    today = today_il()
    g = request.GET
    start = _date(g.get("from")) or today - timedelta(days=30)
    end = _date(g.get("to")) or today
    qs = Student.objects.filter(active=True).select_related("school_class")
    if g.get("class"):
        qs = qs.filter(school_class_id=g["class"])
    rows = reports.summary_rows(qs, start, end)
    if g.get("min"):
        m = int(g["min"] or 0)
        rows = [r for r in rows if r["late_school"] + r["late_lesson"] + r["absent"] >= m]
    if g.get("export") == "xlsx":
        return _xlsx(reports.summary_xlsx(rows, start, end, "סיכום נוכחות"), "attendance-summary.xlsx")
    return render(request, "attendance/summary.html", {
        "rows": rows, "start": start, "end": end, "start_h": hebrew_date(start), "end_h": hebrew_date(end),
        "classes": SchoolClass.objects.all(), "g": g, "school": SchoolSettings.get().school_name})


# ---------- התראות ----------
@login_required
def alerts_page(request):
    show = request.GET.get("all")
    qs = Alert.objects.select_related("student", "student__school_class", "rule")
    if not show:
        qs = qs.filter(handled=False)
    return render(request, "attendance/alerts.html", {"rows": qs[:200], "show_all": show})


@login_required
@require_POST
def alert_action(request, pk):
    a = get_object_or_404(Alert, pk=pk)
    act = request.POST.get("do")
    if act == "handled":
        a.handled = True
        a.save(update_fields=["handled"])
    elif act == "reopen":
        a.handled = False
        a.save(update_fields=["handled"])
    elif act == "tzintuk":
        alert_mod.send_tzintuk(a)
        messages.info(request, a.tzintuk_status)
    return redirect(request.POST.get("next") or "alerts")


# ---------- הגדרות ----------
@login_required
def settings_page(request):
    s = SchoolSettings.get()
    form = SettingsForm(request.POST or None, instance=s)
    if request.method == "POST" and form.is_valid():
        form.save()
        audit(actor(request), "שינוי הגדרות")
        messages.success(request, "ההגדרות נשמרו")
        return redirect("settings")
    url = phone_url(request)
    basic = ["school_name", "yemot_number", "yemot_password_input", "phone_dir", "auth_mode", "unknown_tz",
             "ask_time", "ask_reason", "allow_other_date", "days_off_list"]
    return render(request, "attendance/settings.html", {"form": form, "s": s, "phone_url": url, "basic": basic,
                                                        "ext_ini": ext_ini_text(s, url)})


def ext_ini_text(s, url):
    return (f"type=api\napi_link={url}\napi_url_post=yes\napi_log=no\n"
            + (f"api_dir={s.phone_dir}\n" if s.phone_dir else "") + "api_hangup_send=no\n")


@login_required
@require_POST
def yemot_setup(request):
    s = SchoolSettings.get()
    if not s.phone_dir:
        messages.error(request, "קודם הזינו את תיקיית השלוחה (למשל /7) ושמרו.")
        return redirect("settings")
    try:
        yemot_client.update_extension(s, s.phone_dir, {
            "type": "api", "api_link": phone_url(request), "api_url_post": "yes",
            "api_log": "no", "api_dir": s.phone_dir, "api_hangup_send": "no"})
        s.extension_done = True
        s.save(update_fields=["extension_done"])
        audit(actor(request), "הגדרת שלוחה בימות המשיח", s.phone_dir)
        messages.success(request, f"השלוחה {s.phone_dir} הוגדרה בימות המשיח")
    except yemot_client.YemotError as e:
        messages.error(request, f"לא הצלחנו להגדיר אוטומטית: {e}. אפשר להדביק ידנית לפי ההוראות.")
    return redirect("settings")


@login_required
@require_POST
def yemot_test(request):
    s = SchoolSettings.get()
    try:
        yemot_client._call("GetSession", s.yemot_token, {})
        messages.success(request, "החיבור לימות המשיח תקין")
    except yemot_client.YemotError as e:
        messages.error(request, f"החיבור נכשל: {e}")
    return redirect("settings")


# ---------- ניהול רשימות פשוטות ----------
CRUD = {
    "classes": (SchoolClass, ClassForm, "כיתות", "הוסיפו כיתות לפני ייבוא תלמידות. 'סדר' קובע את סדר ההצגה."),
    "callers": (AuthorizedCaller, CallerForm, "מורשות לדווח (מזכירה / מורה)",
                "מספרים שמורשים לדווח על כל תלמידה. הורים ותלמידות מוגדרים בכרטיס התלמידה. "
                "בהגדרת 'מספר טלפון + קוד אישי' כל מי שרשומה חייבת קוד."),
    "holidays": (NonSchoolDay, HolidayForm, "חופשות וחגים",
                 "ימים שבהם אין לימודים. חיסור שחל בהם לא נספר. ימי השבוע (שישי/שבת) מוגדרים בהגדרות."),
    "lessons": (LessonSlot, LessonForm, "מערכת שעות",
                "שעות השיעורים. איחור לשיעור נשייך אוטומטית לשיעור לפי שעת הכניסה."),
    "rules": (AlertRule, RuleForm, "כללי התראה",
              "כלל קובע מה סופרים, מאיזה סף ובאיזה טווח זמן, ומה קורה כשהסף נחצה: הודעה למתקשרת, "
              "הצגה במסך ההתראות, צינתוק או שיחה."),
}


@login_required
def crud_list(request, kind):
    model, form, title, help_ = CRUD[kind]
    return render(request, "attendance/crud_list.html", {"rows": model.objects.all(), "kind": kind, "title": title,
                                                         "help": help_})


@login_required
def crud_edit(request, kind, pk=None):
    model, form_cls, title, help_ = CRUD[kind]
    obj = get_object_or_404(model, pk=pk) if pk else None
    form = form_cls(request.POST or None, instance=obj)
    if request.method == "POST" and form.is_valid():
        form.save()
        audit(actor(request), f"שמירה: {title}", str(form.instance))
        messages.success(request, "נשמר")
        return redirect("crud_list", kind=kind)
    return render(request, "attendance/form.html", {"form": form, "title": title, "help": help_,
                                                    "delete": ("crud_delete", kind, obj.pk) if obj else None})


@login_required
@require_POST
def crud_delete(request, kind, pk):
    model = CRUD[kind][0]
    obj = get_object_or_404(model, pk=pk)
    try:
        audit(actor(request), "מחיקה", f"{CRUD[kind][2]}: {obj}")
        obj.delete()
        messages.success(request, "נמחק")
    except Exception:  # noqa: BLE001 - למשל כיתה שיש בה תלמידות
        messages.error(request, "אי אפשר למחוק: יש נתונים שמשתמשים בפריט הזה")
    return redirect("crud_list", kind=kind)


# ---------- יומן, גיבוי, עזרה, תמיכה ----------
@login_required
def audit_page(request):
    return render(request, "attendance/audit.html", {"rows": AuditLog.objects.all()[:500]})


@login_required
def backup(request):
    if request.GET.get("format") == "json":
        r = HttpResponse(reports.full_backup_json(), content_type="application/json")
        r["Content-Disposition"] = f'attachment; filename="backup-{today_il()}.json"'
        return r
    if request.GET.get("format") == "xlsx":
        return _xlsx(reports.records_xlsx(Attendance.objects.select_related("student", "student__school_class")),
                     f"attendance-all-{today_il()}.xlsx")
    return render(request, "attendance/backup.html")


@login_required
def help_page(request):
    return render(request, "attendance/help.html", {"s": SchoolSettings.get()})


@login_required
@require_POST
def support_code(request):
    if request.session.get("support_school"):
        raise Http404
    digits = f"{secrets.randbelow(10**8):08d}"
    s = SchoolSettings.get()
    s.support_code_hash = hashlib.sha256(digits.encode()).hexdigest()
    s.support_code_expires = timezone.now() + timedelta(hours=24)
    s.save(update_fields=["support_code_hash", "support_code_expires"])
    audit(actor(request), "נוצר קוד תמיכה", "תקף 24 שעות")
    messages.success(request, f"קוד התמיכה: {request.school.pk}-{digits} (תקף 24 שעות, צפייה בלבד). שלחו אותו למי שמסייע לכם.")
    return redirect("help")


@login_required
@require_POST
def support_revoke(request):
    s = SchoolSettings.get()
    s.support_code_hash, s.support_code_expires = "", None
    s.save(update_fields=["support_code_hash", "support_code_expires"])
    messages.success(request, "קוד התמיכה בוטל")
    return redirect("help")


def support_login(request):
    """כניסה לצפייה בלבד עם קוד בצורה '12-34567890' שבעל/ת בית הספר הפיק/ה."""
    error = ""
    if request.method == "POST":
        ip_key = f"supportfail:{request.META.get('REMOTE_ADDR', '')}"
        code = request.POST.get("code", "").strip()
        sid, _, digits = code.partition("-")
        row = SchoolSettings.all_objects.filter(school_id=int(sid)).first() if sid.isdigit() else None
        if cache.get(ip_key, 0) >= 10:
            error = "יותר מדי ניסיונות. נסו שוב מאוחר יותר."
        elif row and row.support_code_hash and row.support_code_expires and row.support_code_expires > timezone.now() \
                and secrets.compare_digest(hashlib.sha256(digits.encode()).hexdigest(), row.support_code_hash) \
                and row.school.status == School.ACTIVE:
            user, _ = User.objects.get_or_create(username="support", defaults={"is_active": True})
            user.set_unusable_password()
            user.save()
            auth_login(request, user, backend="django.contrib.auth.backends.ModelBackend")
            request.session["support_school"] = row.school_id
            tok = tenant.activate(row.school)
            try:
                audit("תמיכה", "כניסת תמיכה", "צפייה בלבד")
            finally:
                tenant.deactivate(tok)
            return redirect("dashboard")
        else:
            cache.set(ip_key, cache.get(ip_key, 0) + 1, 900)
            error = "קוד שגוי או שפג תוקפו"
    return render(request, "attendance/support_login.html", {"error": error})


@login_required
@require_POST
def rotate_secret(request):
    import secrets as _s
    school = request.school
    school.phone_secret = _s.token_urlsafe()
    school.save(update_fields=["phone_secret"])
    s = SchoolSettings.get()
    s.extension_done = False
    s.save(update_fields=["extension_done"])
    audit(actor(request), "הוחלפה כתובת סודית של הטלפון")
    messages.success(request, "הכתובת הוחלפה. עדכנו את השלוחה (הגדרה אוטומטית, או הדבקה מחדש של ה-ext.ini).")
    return redirect("settings")


def onboarding_steps(request):
    s = SchoolSettings.get()
    return [
        {"done": bool(s.yemot_number and s.yemot_password_enc), "title": "פרטי ימות המשיח",
         "text": "מספר המערכת והסיסמה שלכם בימות המשיח (נשמרים מוצפנים). נדרשים לצינתוקים ולהגדרה האוטומטית של השלוחה.",
         "url": "settings", "cta": "להגדרות"},
        {"done": bool(s.phone_dir and s.extension_done), "title": "שלוחת הטלפון",
         "text": "הגדירו תיקיית שלוחה (למשל /7) ולחצו 'הגדרה אוטומטית', או הדביקו ידנית את ה-ext.ini.",
         "url": "settings", "cta": "להגדרת השלוחה"},
        {"done": Student.objects.exists(), "title": "כיתות ותלמידות",
         "text": "ייבוא מקובץ אקסל (עם תבנית להורדה) או הוספה ידנית.", "url": "students_import", "cta": "לייבוא"},
        {"done": Attendance.objects.filter(source="phone").exists(), "title": "שיחת ניסיון",
         "text": "חייגו לשלוחה, הקישו ת.ז. של תלמידה ודווחו. הדיווח יופיע באתר ואז ההתקנה הושלמה.",
         "url": "records", "cta": "לדיווחים"},
    ]


@login_required
def start_page(request):
    steps = onboarding_steps(request)
    s = SchoolSettings.get()
    return render(request, "attendance/start.html", {"steps": steps, "done": sum(x["done"] for x in steps),
                                                     "phone_url": phone_url(request), "ext_ini": ext_ini_text(s, phone_url(request))})


# ---------- עזרים ----------
def _date(v):
    from datetime import date
    try:
        return date.fromisoformat(v) if v else None
    except ValueError:
        return None


def _xlsx(data, name):
    r = HttpResponse(data, content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    r["Content-Disposition"] = f'attachment; filename="{name}"'
    return r


@login_required
def plans_page(request):
    from .plans import COMPONENTS, SCENARIOS
    owner = request.user.is_superuser and not request.session.get("support_school")
    comps = COMPONENTS if owner else [c for c in COMPONENTS if c["key"] in ("ai", "yemot")]
    return render(request, "attendance/plans.html", {"components": comps, "scenarios": SCENARIOS if owner else [],
                                                     "is_owner": owner})


# ---------- עוזר אישי ----------
@login_required
@require_POST
def assistant_chat(request):
    import json as _json
    from django.http import JsonResponse
    from . import assistant
    try:
        data = _json.loads(request.body or "{}")
        text, pending = assistant.run_chat(data.get("messages", []), data.get("path", "/"))
    except assistant.AssistantError as e:
        return JsonResponse({"error": str(e)}, status=200)
    except ValueError:
        return JsonResponse({"error": "בקשה לא תקינה"}, status=400)
    return JsonResponse({"reply": text, "pending": [{"id": p.pk, "summary": p.summary} for p in pending]})


@login_required
@require_POST
def assistant_decide(request, pk, decision):
    from django.http import JsonResponse
    from . import assistant
    from .models import PendingAction
    action = get_object_or_404(PendingAction, pk=pk)
    if action.status != "pending":
        return JsonResponse({"ok": False, "message": "ההצעה כבר טופלה"})
    if decision == "cancel":
        action.status = "cancelled"
        action.save(update_fields=["status"])
        return JsonResponse({"ok": True, "message": "בוטל, לא שונה כלום"})
    try:
        base = request.build_absolute_uri("/").rstrip("/")
        msg = assistant.execute(action, base, actor(request), request.school.phone_secret)
        action.status, action.result = "done", msg
    except (ValueError, KeyError, yemot_client.YemotError) as e:
        action.status, action.result = "failed", str(e)[:300]
        msg = f"לא בוצע: {e}"
    action.save(update_fields=["status", "result"])
    return JsonResponse({"ok": action.status == "done", "message": msg})
