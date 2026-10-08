"""שיחת הדיווח בטלפון (מודול API של ימות המשיח).

ימות המשיח שולחת בכל בקשה את כל מה שהמתקשרת הקישה עד עכשיו, ולכן אין צורך לשמור מצב
בשרת: המקום בשיחה נקבע לפי המשתנים שכבר הגיעו. אחרי כל דיווח מתחיל "סיבוב" חדש
(המשתנים מסומנים במספר הסיבוב) כדי שאפשר יהיה לדווח על כמה תלמידות באותה שיחה.
"""
import hmac
import logging
from datetime import timedelta

from .alerts import counts_for, pending_announcements, window_range  # noqa: F401
from .calendar_utils import now_il, today_il
from .models import Attendance, AuthorizedCaller, Contact, SchoolSettings, Student, clean_phone, hash_pin
from .services import create_record, parse_hhmm

log = logging.getLogger(__name__)
_BAD = str.maketrans({c: " " for c in ".,=&"})


def t(text):
    return "t-" + " ".join(str(text).translate(_BAD).split())


def ask_digits(parts, var, maxd, mind=1, timeout=7, mode="No"):
    return f"read={'.'.join(parts)}={var},no,{maxd},{mind},{timeout},{mode},no,no"


def ask_record(parts, var, folder, filename):
    return f"read={'.'.join(parts)}={var},no,record,{folder},{filename},no,yes,no"


def end(parts, hangup=False):
    return "id_list_message=" + ".".join(parts) + ("&go_to_folder=hangup" if hangup else "")


def name_parts(student, s):
    if student.name_recorded and s.phone_dir:
        return [f"f-{s.phone_dir}/name_{student.tz}"]
    return [t(student.full_name)]


def find_caller(phone):
    """(שם, קוד אישי, תלמידות מותרות או None לכולן) או None אם לא רשומה."""
    ac = AuthorizedCaller.objects.filter(phone=phone, active=True).first()
    if ac:
        return ac.name, ac.pin, None
    contacts = list(Contact.objects.filter(phone=phone))
    if contacts:
        pins = {c.pin for c in contacts if c.pin}
        return contacts[0].label, (sorted(pins)[0] if pins else ""), {c.student_id for c in contacts}
    return None


def lookup_student(tz, allowed):
    tz = tz.zfill(9)
    st = Student.objects.filter(tz__in={tz, tz.lstrip("0")}, active=True).first()
    if st and allowed is not None and st.pk not in allowed:
        return None
    return st


def handle(p):
    try:
        return _handle(p)
    except Exception:  # noqa: BLE001 - המתקשרת חייבת לקבל תשובה תקינה
        log.exception("phone flow failed")
        return end([t("אירעה תקלה נסו שוב מאוחר יותר")], hangup=True)


def _handle(p):
    if p.get("hangup") == "yes":
        return "noop=hangup"
    s = SchoolSettings.get()
    phone = clean_phone(p.get("ApiPhone", ""))
    allowed = None
    caller_name = "טלפון"

    if s.auth_mode != "open":
        info = find_caller(phone)
        if not info:
            return end([t("מספר הטלפון שלכם אינו רשום במערכת")], hangup=True)
        caller_name, pin, allowed = info
        if s.auth_mode == "phone_pin":
            if not pin:
                return end([t("לא הוגדר לכם קוד אישי פנו להנהלת בית הספר")], hangup=True)
            for i in range(1, 4):
                var = f"pin_{i}"
                if var not in p:
                    return ask_digits([t("הקישו את הקוד האישי שלכם")] if i == 1
                                      else [t("הקוד שגוי הקישו שוב")], var, 8)
                if hmac.compare_digest(hash_pin(p[var]), pin):
                    break
            else:
                return end([t("הקוד שגוי")], hangup=True)

    r = 1
    while f"more{r}" in p:
        if p[f"more{r}"] != "1":
            return end([t("תודה ולהתראות")])
        r += 1

    # --- זיהוי תלמידה ---
    student, raw_tz = None, ""
    for i in range(1, 4):
        var = f"tz{r}_{i}"
        if var not in p:
            return ask_digits([t("הקישו מספר זהות של התלמידה" if i == 1 else "מספר הזהות לא מוכר הקישו שוב")],
                              var, 9, 5, 15, "Digits")
        student = lookup_student(p[var], allowed)
        if student:
            break
        raw_tz = p[var]
    else:
        if s.unknown_tz == "reject" or allowed is not None:
            return end([t("מספר הזהות לא נמצא במערכת")], hangup=True)

    # --- הקלטת שם בפעם הראשונה ---
    if student and s.phone_dir and not student.name_recorded:
        var = f"nm{student.tz}"
        if var not in p:
            return ask_record([t(f"זו הפעם הראשונה שמדווחים על {student.full_name} אנא אמרו את שם התלמידה "
                                 "בקול ברור ובסיום הקישו סולמית")],
                              var, s.phone_dir, f"name_{student.tz}")
        student.name_recorded = True
        student.save(update_fields=["name_recorded"])

    who = name_parts(student, s) if student else [t("תלמידה לא מזוהה")]

    # --- סוג הדיווח (+ הודעות התראה מהשיחה הקודמת/הנוכחית) ---
    kind_var = f"k{r}"
    if kind_var not in p:
        parts = list(who)
        if student:
            for a in pending_announcements(student):
                parts.append(t(a.message))
        parts.append(t("לאיחור לבית ספר הקישו 1 לאיחור לשיעור הקישו 2 לחיסור הקישו 3"))
        return ask_digits(parts, kind_var, 1)
    kind = {"1": Attendance.LATE_SCHOOL, "2": Attendance.LATE_LESSON, "3": Attendance.ABSENT}.get(p[kind_var])
    if not kind:
        return end([t("בחירה לא תקינה")], hangup=True)

    today = today_il()
    time, date, date_to, reason = None, today, None, ""

    if kind in (Attendance.LATE_SCHOOL, Attendance.LATE_LESSON):
        time = now_il().time().replace(second=0, microsecond=0)
        if kind == Attendance.LATE_LESSON or s.ask_time:
            tm = f"tm{r}"
            if tm not in p:
                return ask_digits([t("לשעה הנוכחית הקישו 1 לשעה אחרת הקישו 2")], tm, 1)
            if p[tm] == "2":
                for i in range(1, 4):
                    var = f"hm{r}_{i}"
                    if var not in p:
                        return ask_digits([t("הקישו את שעת הכניסה בארבע ספרות לדוגמה שמונה ורבע זה 0815")],
                                          var, 4, 3, 10)
                    parsed = parse_hhmm(p[var])
                    if parsed:
                        time = parsed
                        break
                else:
                    return end([t("השעה לא תקינה")], hangup=True)
    else:
        if s.allow_other_date:
            dt = f"dt{r}"
            if dt not in p:
                return ask_digits([t("להיום הקישו 1 למחר 2 לאתמול 3 לכמה ימים 4")], dt, 1)
            if p[dt] == "2":
                date = today + timedelta(days=1)
            elif p[dt] == "3":
                date = today - timedelta(days=1)
            elif p[dt] == "4":
                dd = f"dd{r}"
                if dd not in p:
                    return ask_digits([t("לכמה ימים כולל היום הקישו מספר")], dd, 2)
                days = max(1, min(int(p[dd] or 1), 60))
                date_to = today + timedelta(days=days - 1)
        if s.ask_reason:
            rs = f"rs{r}"
            if rs not in p:
                return ask_digits([t("לחיסור בגלל מחלה הקישו 1 באישור 2 סיבה אחרת 3")], rs, 1)
            reason = {"1": "sick", "2": "approved", "3": "other"}.get(p[rs], "other")

    call_id = p.get("ApiCallId", "")
    create_record(student=student, raw_tz=raw_tz, kind=kind, date=date, date_to=date_to, time=time,
                  reason=reason, source="phone", caller_phone=phone, actor=f"טלפון: {caller_name}",
                  source_ref=f"{call_id}:{r}" if call_id else "")

    if kind == Attendance.ABSENT:
        done = [t("נרשם חיסור"), f"date-{date:%d/%m/%Y}"]
        if date_to:
            done += [t("עד"), f"date-{date_to:%d/%m/%Y}"]
    else:
        label = "איחור לבית ספר" if kind == Attendance.LATE_SCHOOL else "איחור לשיעור"
        done = [t(f"נרשם {label} בשעה {time:%H:%M}")]
    return ask_digits(who + done + [t("לדיווח על תלמידה נוספת הקישו 1 לסיום הקישו 2")], f"more{r}", 1)
