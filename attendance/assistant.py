"""העוזר האישי: שיחה עם Claude עם כלים. קריאה חופשית, שינויים רק אחרי אישור בלחיצה."""
import json
import urllib.error
import urllib.request
from datetime import date, timedelta
from pathlib import Path

from django.forms.models import model_to_dict

from . import alerts, yemot_client
from .calendar_utils import hebrew_date, today_il
from .forms import CalendarEventForm, ClassForm, HolidayForm, LessonForm, RuleForm
from .models import (Alert, AlertRule, Attendance, AuthorizedCaller, Contact, LessonSlot, NonSchoolDay,
                     PendingAction, SchoolClass, SchoolSettings, Student)
from .reports import summary_rows
from .services import audit

API = "https://api.anthropic.com/v1/messages"
MAX_TURNS = 6
KNOWLEDGE = (Path(__file__).parent / "assistant_knowledge.md").read_text(encoding="utf-8")

SETTINGS_FIELDS = {
    "school_name": str, "auth_mode": ("open", "phone", "phone_pin"), "unknown_tz": ("reject", "record"),
    "ask_time": bool, "ask_reason": bool, "allow_other_date": bool, "phone_dir": "dir", "support_contact": str,
    "school_year_start": "date", "days_off": "days", "yemot_number": str, "caller_id": str, "voice_template_id": str,
}


class AssistantError(Exception):
    pass


def mask_tz(tz):
    return "******" + tz[-3:]


# ---------- קריאה ל-Claude ----------
def call_claude(key, model, system, messages, tools):
    body = json.dumps({"model": model, "max_tokens": 1500, "system": system, "messages": messages,
                       "tools": tools}).encode()
    req = urllib.request.Request(API, data=body, headers={
        "x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=50) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise AssistantError("מפתח ה-API לא תקין. בדקו אותו בהגדרות.") from e
        if e.code == 429:
            raise AssistantError("יותר מדי בקשות או שנגמרה המכסה בחשבון. נסו שוב עוד רגע.") from e
        raise AssistantError(f"תקלה בשירות הבינה המלאכותית ({e.code})") from e
    except (urllib.error.URLError, TimeoutError, ValueError) as e:
        raise AssistantError("אין תקשורת עם שירות הבינה המלאכותית") from e


# ---------- הגדרת הכלים ----------
def _tool(name, desc, props=None, required=None):
    return {"name": name, "description": desc,
            "input_schema": {"type": "object", "properties": props or {}, "required": required or []}}


S, I, B = {"type": "string"}, {"type": "integer"}, {"type": "boolean"}
TOOLS = [
    _tool("get_settings", "קורא את הגדרות בית הספר והחיבור (בלי סיסמאות וסודות) ורשימת שלבי התקנה חסרים."),
    _tool("list_config", "רשימת כללי התראה, כיתות, חופשות, מערכת שעות ומורשות לדווח."),
    _tool("find_students", "חיפוש תלמידות לפי שם או חלק ממספר זהות (ת.ז. מוסתרת).", {"q": S}, ["q"]),
    _tool("ranking", "דירוג תלמידות לפי איחורים/חיסורים בטווח תאריכים (YYYY-MM-DD). ברירת מחדל: 30 יום אחרונים.",
          {"from": S, "to": S, "class_name": S, "top": I, "order_by": {"type": "string", "enum": [
              "late", "absent", "unjustified"]}}),
    _tool("recent_alerts", "התראות פתוחות אחרונות."),
    _tool("data_health", "בדיקת תקינות: תלמידות בלי טלפון, דיווחים לא מזוהים, תלמידות בלי שם מוקלט וכו'."),
    _tool("test_yemot", "בודק את החיבור לימות המשיח עם הפרטים השמורים."),
    _tool("propose_update_settings", "מציע שינוי בהגדרות. נשלח לאישור המשתמש.",
          {"changes": {"type": "object", "description": "שדה:ערך. שדות: " + ", ".join(SETTINGS_FIELDS) +
                       ". days_off: מחרוזת כמו '4,5' (0=שני...4=שישי,5=שבת,6=ראשון). phone_dir מתחיל ב-/"}},
          ["changes"]),
    _tool("propose_alert_rule", "מציע יצירה (בלי id) או עדכון (עם id) של כלל התראה.",
          {"id": I, "name": S, "active": B,
           "metric": {"type": "string", "enum": [m for m, _ in AlertRule.METRICS]}, "threshold": I, "repeat": B,
           "window": {"type": "string", "enum": [w for w, _ in AlertRule.WINDOWS]}, "window_days": I,
           "date_from": S, "date_to": S, "class_name": S, "notify_site": B, "notify_caller": B,
           "message": {"type": "string", "description": "אפשר {name} {late} {absent} {count}"},
           "tzintuk": B, "tzintuk_voice": B, "tzintuk_target": {"type": "string", "enum": ["contacts", "extra", "both"]},
           "extra_phones": S}),
    _tool("propose_add_holiday", "מציע הוספת חופשה/חג.", {"name": S, "date_from": S, "date_to": S},
          ["name", "date_from", "date_to"]),
    _tool("propose_add_lesson", "מציע הוספת שיעור למערכת השעות (HH:MM).", {"number": I, "start": S, "end": S},
          ["number", "start", "end"]),
    _tool("upcoming_events", "אירועי לוח השנה ב-30 הימים הקרובים (התחלה מאוחרת, סיום מוקדם, מבחנים...) + זמני היום ועונת השעון."),
    _tool("propose_calendar_event", "מציע הוספת אירוע ללוח השנה. kind: late_start/early_end (חובה שעה HH:MM), exam, trip, event. תאריכים YYYY-MM-DD.",
          {"kind": {"type": "string", "enum": ["late_start", "early_end", "exam", "trip", "event"]}, "title": S,
           "date_from": S, "date_to": S, "time": S, "notes": S, "announce_banner": B, "announce_phone": B},
          ["kind", "title", "date_from"]),
    _tool("propose_add_class", "מציע הוספת כיתה.", {"name": S, "order": I}, ["name"]),
    _tool("propose_setup_extension", "מציע להגדיר/לעדכן אוטומטית את שלוחת ה-API בימות המשיח (לפי phone_dir בהגדרות)."),
]


# ---------- כלי קריאה ----------
def _overview(s):
    todo = []
    if not (s.yemot_number and s.yemot_password):
        todo.append("להזין מספר מערכת וסיסמה של ימות המשיח בהגדרות")
    if not s.phone_dir:
        todo.append("להגדיר תיקיית שלוחה (phone_dir) כמו /7")
    if not SchoolClass.objects.exists():
        todo.append("להוסיף כיתות")
    if not Student.objects.exists():
        todo.append("לייבא או להוסיף תלמידות")
    if not NonSchoolDay.objects.exists():
        todo.append("להגדיר חופשות (לא חובה)")
    if not LessonSlot.objects.exists():
        todo.append("להגדיר מערכת שעות (רק אם רוצים שיוך איחור לשיעור)")
    if not AlertRule.objects.exists():
        todo.append("להגדיר כללי התראה (לא חובה)")
    return todo


def t_get_settings(args, ctx):
    s = SchoolSettings.get()
    d = {k: getattr(s, k) for k in ("school_name", "yemot_number", "phone_dir", "caller_id", "voice_template_id",
                                    "auth_mode", "unknown_tz", "ask_time", "ask_reason", "allow_other_date",
                                    "days_off", "support_contact")}
    d["school_year_start"] = str(s.school_year_start or "")
    d["yemot_password_set"] = bool(s.yemot_password_enc)
    d["students"] = Student.objects.count()
    d["today"] = f"{today_il():%Y-%m-%d} ({hebrew_date(today_il())})"
    d["missing_setup_steps"] = _overview(s)
    return d


def t_list_config(args, ctx):
    return {
        "rules": [{"id": r.pk, **{k: v for k, v in model_to_dict(r).items() if k not in ("id", "class_filter")},
                   "class": r.class_filter.name if r.class_filter else ""} for r in AlertRule.objects.all()],
        "classes": [c.name for c in SchoolClass.objects.all()],
        "holidays": [str(h) for h in NonSchoolDay.objects.all()],
        "lessons": [str(x) for x in LessonSlot.objects.all()],
        "authorized_callers": [{"name": c.name, "has_pin": bool(c.pin), "active": c.active}
                               for c in AuthorizedCaller.objects.all()],
    }


def t_find_students(args, ctx):
    q = (args.get("q") or "").strip()
    qs = Student.objects.select_related("school_class")
    from django.db.models import Q
    qs = qs.filter(Q(first_name__icontains=q) | Q(last_name__icontains=q) | Q(tz__endswith=q))[:15]
    return [{"id": s.pk, "name": str(s), "class": s.school_class.name, "tz": mask_tz(s.tz), "active": s.active,
             "phones": s.contacts.count()} for s in qs]


def _d(v, default):
    try:
        return date.fromisoformat(v) if v else default
    except ValueError:
        return default


def t_ranking(args, ctx):
    today = today_il()
    start, end = _d(args.get("from"), today - timedelta(days=30)), _d(args.get("to"), today)
    qs = Student.objects.filter(active=True).select_related("school_class")
    if args.get("class_name"):
        qs = qs.filter(school_class__name=args["class_name"])
    rows = summary_rows(qs, start, end)
    key = {"late": lambda r: r["late_school"] + r["late_lesson"], "absent": lambda r: r["absent"],
           "unjustified": lambda r: r["unjustified"]}[args.get("order_by") or "late"]
    rows.sort(key=key, reverse=True)
    top = max(1, min(int(args.get("top") or 10), 30))
    return {"from": str(start), "to": str(end), "students_in_scope": len(rows),
            "rows": [{"name": str(r["student"]), "class": r["student"].school_class.name,
                      "late_school": r["late_school"], "late_lesson": r["late_lesson"], "absent_days": r["absent"],
                      "unjustified_days": r["unjustified"]} for r in rows[:top]]}


def t_recent_alerts(args, ctx):
    return [{"student": str(a.student), "class": a.student.school_class.name, "rule": a.rule.name,
             "message": a.message, "date": f"{a.created_at:%Y-%m-%d}", "tzintuk": a.tzintuk_status}
            for a in Alert.objects.filter(handled=False).select_related("student", "rule")[:20]]


def t_data_health(args, ctx):
    active = Student.objects.filter(active=True)
    return {
        "active_students": active.count(),
        "students_without_phone": active.filter(contacts__isnull=True).count(),
        "students_without_recorded_name": active.filter(name_recorded=False).count(),
        "unmatched_reports_open": Attendance.objects.filter(student__isnull=True, resolved=False).count(),
        "authorized_callers_without_pin": AuthorizedCaller.objects.filter(pin="", active=True).count(),
        "contacts_without_pin": Contact.objects.filter(pin="").count(),
        "auth_mode": SchoolSettings.get().auth_mode,
    }


def t_test_yemot(args, ctx):
    s = SchoolSettings.get()
    try:
        yemot_client._call("GetSession", s.yemot_token, {})
        return {"ok": True}
    except yemot_client.YemotError as e:
        return {"ok": False, "error": str(e)}


def t_upcoming_events(args, ctx):
    from datetime import timedelta
    from . import zmanim as zm
    from .models import CalendarEvent
    today = today_il()
    sch = SchoolSettings.get()
    z = zm.zmanim_for(sch, today)
    return {"today": f"{today:%Y-%m-%d}", "hebrew": zm.day_label(today)["hebrew"], "season": zm.season_label(zm.season_for(sch, today)),
            "city": zm.CITIES.get(sch.city_key, zm.CITIES["jerusalem"])[0],
            "zmanim_today": {k: z[k].strftime("%H:%M") for k in ("hanetz", "chatzos", "shkia", "tzais") if z.get(k)},
            "events": [{"title": e.title, "kind": e.kind, "from": str(e.date_from), "to": str(e.date_to),
                        "time": e.time.strftime("%H:%M") if e.time else ""}
                       for e in CalendarEvent.objects.filter(date_from__lte=today + timedelta(days=30), date_to__gte=today)[:30]]}


READ_TOOLS = {"upcoming_events": t_upcoming_events, "get_settings": t_get_settings, "list_config": t_list_config, "find_students": t_find_students,
              "ranking": t_ranking, "recent_alerts": t_recent_alerts, "data_health": t_data_health,
              "test_yemot": t_test_yemot}


# ---------- כלי הצעה (נשמרים לאישור) ----------
def _validate_settings(changes):
    clean = {}
    for k, v in (changes or {}).items():
        spec = SETTINGS_FIELDS.get(k)
        if spec is None:
            raise ValueError(f"שדה לא מורשה: {k}")
        if spec is bool:
            v = bool(v)
        elif spec is str:
            v = str(v)[:200]
        elif isinstance(spec, tuple):
            if v not in spec:
                raise ValueError(f"ערך לא חוקי ל-{k}: {v}")
        elif spec == "dir":
            v = str(v).rstrip("/")
            if v and not v.startswith("/"):
                raise ValueError("תיקיית שלוחה חייבת להתחיל ב-/")
        elif spec == "date":
            v = date.fromisoformat(v).isoformat() if v else None
        elif spec == "days":
            parts = [x.strip() for x in str(v).split(",") if x.strip()]
            if not all(x.isdigit() and 0 <= int(x) <= 6 for x in parts):
                raise ValueError("ימי שבוע: מספרים 0-6 מופרדים בפסיק")
            v = ",".join(sorted(set(parts)))
        clean[k] = v
    if not clean:
        raise ValueError("אין שינויים")
    return clean


def _rule_form(args):
    inst = AlertRule.objects.filter(pk=args["id"]).first() if args.get("id") else None
    if args.get("id") and not inst:
        raise ValueError("כלל ההתראה לא נמצא")
    data = model_to_dict(inst) if inst else {"active": True, "notify_site": True, "notify_caller": True,
                                             "window": "last_days", "window_days": 183, "tzintuk_target": "contacts",
                                             "message": "לתלמידה יש {late} איחורים ו-{absent} ימי חיסור"}
    for k, v in args.items():
        if k in ("id", "class_name"):
            continue
        data[k] = v
    if "class_name" in args:
        c = SchoolClass.objects.filter(name=args["class_name"]).first() if args["class_name"] else None
        if args["class_name"] and not c:
            raise ValueError(f"כיתה לא קיימת: {args['class_name']}")
        data["class_filter"] = c.pk if c else None
    form = RuleForm({k: v for k, v in data.items() if v is not None}, instance=inst)
    if not form.is_valid():
        raise ValueError("; ".join(f"{k}: {' '.join(e)}" for k, e in form.errors.items()))
    return form, inst


def propose(tool, args):
    """בדיקת תקינות + סיכום בעברית. מחזיר סיכום או זורק ValueError."""
    if tool == "propose_update_settings":
        ch = _validate_settings(args.get("changes"))
        return "שינוי הגדרות: " + ", ".join(f"{k} ← {v}" for k, v in ch.items()), {"changes": ch}
    if tool == "propose_alert_rule":
        form, inst = _rule_form(args)
        d = form.cleaned_data
        return (f"{'עדכון' if inst else 'יצירת'} כלל התראה '{d['name']}': סף {d['threshold']} "
                f"({dict(AlertRule.METRICS)[d['metric']]}), טווח {dict(AlertRule.WINDOWS)[d['window']]}"
                f"{', חוזר' if d['repeat'] else ''}{', צינתוק' if d['tzintuk'] else ''}"), args
    if tool == "propose_add_holiday":
        f = HolidayForm(args)
        if not f.is_valid():
            raise ValueError("תאריכים לא תקינים (YYYY-MM-DD)")
        return f"הוספת חופשה '{args['name']}' {args['date_from']} עד {args['date_to']}", args
    if tool == "propose_add_lesson":
        f = LessonForm(args)
        if not f.is_valid():
            raise ValueError("שיעור לא תקין (מספר ייחודי, שעות HH:MM)")
        return f"הוספת שיעור {args['number']}: {args['start']}-{args['end']}", args
    if tool == "propose_calendar_event":
        data = {"announce_banner": True, "announce_phone": True, **args}
        data.setdefault("date_to", data["date_from"])
        f = CalendarEventForm(data)
        if not f.is_valid():
            raise ValueError("אירוע לא תקין: " + "; ".join(f"{k}: {' '.join(e)}" for k, e in f.errors.items()))
        return f"הוספת אירוע ללוח: {args['title']} ב-{data['date_from']}" + (f" בשעה {args['time']}" if args.get("time") else ""), data
    if tool == "propose_add_class":
        f = ClassForm({"order": 0, **args})
        if not f.is_valid():
            raise ValueError("כיתה לא תקינה או כבר קיימת")
        return f"הוספת כיתה {args['name']}", args
    if tool == "propose_setup_extension":
        s = SchoolSettings.get()
        if not (s.phone_dir and s.yemot_token):
            raise ValueError("קודם צריך מספר מערכת, סיסמה ותיקיית שלוחה בהגדרות")
        return f"הגדרה אוטומטית של שלוחה {s.phone_dir} בימות המשיח (type=api)", {}
    raise ValueError("כלי לא מוכר")


def execute(action, base_url, actor_name="עוזר אישי", phone_secret=""):
    """מבצע הצעה שאושרה. מחזיר טקסט תוצאה."""
    tool, a = action.tool, action.args
    if tool == "propose_update_settings":
        s = SchoolSettings.get()
        for k, v in _validate_settings(a["changes"]).items():
            setattr(s, k, v)
        s.save()
        msg = "ההגדרות עודכנו"
    elif tool == "propose_alert_rule":
        form, _ = _rule_form(a)
        form.save()
        msg = "כלל ההתראה נשמר"
    elif tool == "propose_add_holiday":
        HolidayForm(a).save()
        msg = "החופשה נוספה"
    elif tool == "propose_add_lesson":
        LessonForm(a).save()
        msg = "השיעור נוסף"
    elif tool == "propose_calendar_event":
        CalendarEventForm(a).save()
        msg = "האירוע נוסף ללוח השנה (לשליחה להורים: לוח שנה, ביום האירוע)"
    elif tool == "propose_add_class":
        ClassForm({"order": 0, **a}).save()
        msg = "הכיתה נוספה"
    elif tool == "propose_setup_extension":
        s = SchoolSettings.get()
        yemot_client.update_extension(s, s.phone_dir, {
            "type": "api", "api_link": f"{base_url}/phone/{phone_secret}/", "api_url_post": "yes",
            "api_log": "no", "api_dir": s.phone_dir, "api_hangup_send": "no"})
        msg = f"השלוחה {s.phone_dir} הוגדרה בימות המשיח"
    else:
        raise ValueError("כלי לא מוכר")
    audit(actor_name, "שינוי באישור המשתמש", action.summary)
    return msg


# ---------- לולאת השיחה ----------
def system_prompt(path):
    return KNOWLEDGE + f"\n\nהמשתמש/ת נמצא/ת כרגע בעמוד: {path}. התאריך היום: {today_il():%Y-%m-%d} ({hebrew_date(today_il())})."


def run_chat(history, path="/", llm=None):
    """history: [{role: user|assistant, content: str}]. מחזיר (טקסט, [PendingAction])."""
    llm = llm or call_claude
    s = SchoolSettings.get()
    if not s.assistant_key:
        raise AssistantError("העוזר האישי עוד לא מחובר. הזינו מפתח API של Claude בעמוד ההגדרות.")
    msgs = [{"role": m["role"], "content": str(m["content"])[:4000]} for m in history[-16:]
            if m.get("role") in ("user", "assistant") and m.get("content")]
    while msgs and msgs[0]["role"] != "user":
        msgs.pop(0)
    if not msgs:
        raise AssistantError("שלחו הודעה.")
    pending = []
    for _ in range(MAX_TURNS):
        resp = llm(s.assistant_key, s.assistant_model, system_prompt(path), msgs, TOOLS)
        blocks = resp.get("content", [])
        uses = [b for b in blocks if b.get("type") == "tool_use"]
        if not uses:
            text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text").strip()
            return text or "לא הצלחתי לנסח תשובה, נסו לשאול אחרת.", pending
        msgs.append({"role": "assistant", "content": blocks})
        results = []
        for u in uses:
            name, args = u["name"], u.get("input") or {}
            try:
                if name in READ_TOOLS:
                    out = READ_TOOLS[name](args, None)
                elif name.startswith("propose_"):
                    summary, clean = propose(name, args)
                    pa = PendingAction.objects.create(tool=name, args=clean, summary=summary[:400])
                    pending.append(pa)
                    out = {"status": "הוצע וממתין ללחיצת אישור של המשתמש/ת", "summary": summary}
                else:
                    out = {"error": "כלי לא מוכר"}
            except (ValueError, KeyError, TypeError) as e:
                out = {"error": str(e)}
            results.append({"type": "tool_result", "tool_use_id": u["id"],
                            "content": json.dumps(out, ensure_ascii=False, default=str)[:6000]})
        msgs.append({"role": "user", "content": results})
    return "העבודה ארכה יותר מדי. נסו לשאול בשאלה מצומצמת יותר.", pending
