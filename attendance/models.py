"""מודלים: הגדרות בית הספר, תלמידות, דיווחים, כללי התראה והתראות."""
import re

import hashlib
import hmac
import secrets

from django.conf import settings as dj_settings
from django.contrib.auth.models import User
from django.db import models
from django.utils import timezone

from . import crypto
from .tenant import TenantModel

WEEKDAYS = [(0, "שני"), (1, "שלישי"), (2, "רביעי"), (3, "חמישי"), (4, "שישי"), (5, "שבת"), (6, "ראשון")]
# Python weekday(): Monday=0 ... Sunday=6


def clean_phone(p: str) -> str:
    p = re.sub(r"\D", "", p or "")
    if p.startswith("972"):
        p = "0" + p[3:]
    if len(p) == 9 and p.startswith("5"):
        p = "0" + p  # אקסל מוחק אפס מוביל
    return p


def hash_pin(pin: str) -> str:
    """קוד אישי נשמר מגובב, לא כטקסט (4-8 ספרות: ההגנה היא בעיקר מפני צפייה מקרית)."""
    return hmac.new(dj_settings.SECRET_KEY.encode(), f"pin:{pin}".encode(), hashlib.sha256).hexdigest()[:40]


class School(models.Model):
    """בית ספר שרשום במערכת המרכזית. אין לו ירושה מ-TenantModel: זו הישות שמסננים לפיה."""

    PENDING, ACTIVE, SUSPENDED = "pending", "active", "suspended"
    STATUSES = [(PENDING, "ממתין לאישור"), (ACTIVE, "פעיל"), (SUSPENDED, "מושעה")]
    name = models.CharField("שם בית הספר", max_length=120)
    status = models.CharField(max_length=10, choices=STATUSES, default=PENDING)
    phone_secret = models.CharField(max_length=60, unique=True, default=secrets.token_urlsafe)
    contact_name = models.CharField("איש קשר", max_length=80, blank=True)
    contact_phone = models.CharField("טלפון", max_length=20, blank=True)
    contact_email = models.EmailField("מייל", blank=True)
    terms_accepted_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    approved_at = models.DateTimeField(null=True, blank=True)
    last_report_at = models.DateTimeField(null=True, blank=True)
    owner_notes = models.CharField(max_length=300, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.name


class SchoolUser(models.Model):
    """משתמש שמנהל בית ספר מסוים."""

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="school_profile")
    school = models.ForeignKey(School, on_delete=models.CASCADE, related_name="users")


class SchoolSettings(TenantModel):
    """שורה אחת בלבד: כל ההגדרות של בית הספר."""

    AUTH_CHOICES = [
        ("open", "כל מי שמתקשר/ת (מספר זהות בלבד)"),
        ("phone", "רק מספרי טלפון רשומים"),
        ("phone_pin", "מספר טלפון רשום + קוד אישי"),
    ]
    UNKNOWN_CHOICES = [
        ("reject", "הודעת שגיאה אחרי 3 ניסיונות, בלי לשמור"),
        ("record", "לשמור את הדיווח כ'תלמידה לא מזוהה' לטיפול המזכירה"),
    ]
    school_name = models.CharField("שם בית הספר", max_length=120, default="בית הספר")
    yemot_number = models.CharField("מספר מערכת ימות המשיח", max_length=20, blank=True)
    yemot_password_enc = models.TextField(blank=True)
    phone_dir = models.CharField("תיקיית השלוחה בימות המשיח (לשמירת הקלטות שמות)", max_length=60, blank=True,
                                 help_text="למשל /7 - אותה שלוחה שהגדרתם עם type=api")
    caller_id = models.CharField("זיהוי יוצא לצינתוקים (אופציונלי)", max_length=20, blank=True)
    voice_template_id = models.CharField("מזהה תבנית קמפיין להודעה קולית (אופציונלי)", max_length=20, blank=True)
    auth_mode = models.CharField("זיהוי המתקשר/ת", max_length=12, choices=AUTH_CHOICES, default="open")
    unknown_tz = models.CharField("מספר זהות לא מוכר", max_length=8, choices=UNKNOWN_CHOICES, default="record")
    ask_time = models.BooleanField("לשאול בטלפון איזו שעה לרשום באיחור לבית ספר (אחרת - השעה הנוכחית)", default=False)
    ask_reason = models.BooleanField("לשאול סיבה בחיסור", default=True)
    allow_other_date = models.BooleanField("לאפשר דיווח חיסור למחר/אתמול/כמה ימים", default=True)
    days_off = models.CharField("ימים שלא נספרים (שבוע)", max_length=20, default="4,5",
                                help_text="מספרי ימים: 0=שני ... 4=שישי, 5=שבת, 6=ראשון")
    school_year_start = models.DateField("תחילת שנת הלימודים (לספירת 'שנה')", null=True, blank=True)
    support_code_hash = models.CharField(max_length=128, blank=True)
    support_code_expires = models.DateTimeField(null=True, blank=True)
    support_contact = models.CharField("דרך ליצירת קשר עם התמיכה", max_length=200, blank=True)
    assistant_key_enc = models.TextField(blank=True)
    assistant_model = models.CharField("מודל של העוזר האישי", max_length=60, default="claude-sonnet-5-5")
    extension_done = models.BooleanField("השלוחה הוגדרה בימות המשיח", default=False)

    class Meta:
        verbose_name = "הגדרות"

    def __str__(self):
        return self.school_name

    @classmethod
    def get(cls):
        return cls.objects.first() or cls.objects.create()

    @property
    def yemot_password(self):
        return crypto.decrypt(self.yemot_password_enc)

    @yemot_password.setter
    def yemot_password(self, value):
        self.yemot_password_enc = crypto.encrypt(value)

    @property
    def assistant_key(self):
        return crypto.decrypt(self.assistant_key_enc)

    @assistant_key.setter
    def assistant_key(self, value):
        self.assistant_key_enc = crypto.encrypt(value)

    @property
    def days_off_set(self):
        return {int(x) for x in self.days_off.split(",") if x.strip().isdigit()}

    @property
    def yemot_token(self):
        return f"{self.yemot_number}:{self.yemot_password}" if self.yemot_number and self.yemot_password else ""


class LessonSlot(TenantModel):
    number = models.PositiveSmallIntegerField("מספר שיעור")
    start = models.TimeField("התחלה")
    end = models.TimeField("סיום")

    class Meta:
        ordering = ["number"]
        constraints = [models.UniqueConstraint(fields=["school", "number"], name="uniq_lesson_per_school")]

    def __str__(self):
        return f"שיעור {self.number} ({self.start:%H:%M}-{self.end:%H:%M})"


class NonSchoolDay(TenantModel):
    name = models.CharField("שם (חופשה / חג)", max_length=80)
    date_from = models.DateField("מתאריך")
    date_to = models.DateField("עד תאריך")

    class Meta:
        ordering = ["date_from"]

    def __str__(self):
        return f"{self.name} ({self.date_from:%d/%m/%Y} - {self.date_to:%d/%m/%Y})"


class SchoolClass(TenantModel):
    name = models.CharField("שם הכיתה", max_length=40)
    order = models.PositiveSmallIntegerField("סדר", default=0)

    class Meta:
        ordering = ["order", "name"]
        constraints = [models.UniqueConstraint(fields=["school", "name"], name="uniq_class_per_school")]

    def __str__(self):
        return self.name


class Student(TenantModel):
    tz = models.CharField("מספר זהות", max_length=12)
    first_name = models.CharField("שם פרטי", max_length=40)
    last_name = models.CharField("שם משפחה", max_length=40)
    school_class = models.ForeignKey(SchoolClass, on_delete=models.PROTECT, related_name="students", verbose_name="כיתה")
    active = models.BooleanField("פעילה", default=True)
    name_recorded = models.BooleanField("שם מוקלט בטלפון", default=False)
    notes = models.CharField("הערות", max_length=200, blank=True)

    class Meta:
        ordering = ["school_class__order", "school_class__name", "last_name", "first_name"]
        constraints = [models.UniqueConstraint(fields=["school", "tz"], name="uniq_tz_per_school")]

    def __str__(self):
        return f"{self.last_name} {self.first_name}"

    @property
    def full_name(self):
        return f"{self.first_name} {self.last_name}"


class Contact(TenantModel):
    """טלפון המשויך לתלמידה: הורה, תלמידה עצמה וכו'. לשם זיהוי מתקשרות וצינתוקים."""

    student = models.ForeignKey(Student, on_delete=models.CASCADE, related_name="contacts")
    label = models.CharField("תיאור (אמא / אבא / תלמידה...)", max_length=30)
    phone = models.CharField("טלפון", max_length=20)
    receives_alerts = models.BooleanField("מקבל/ת צינתוקי התראה", default=True)
    pin = models.CharField("קוד אישי (מגובב)", max_length=40, blank=True)

    def set_pin(self, pin):
        self.pin = hash_pin(pin) if pin else ""

    def save(self, *a, **kw):
        self.phone = clean_phone(self.phone)
        super().save(*a, **kw)

    def __str__(self):
        return f"{self.label} {self.phone}"


class AuthorizedCaller(TenantModel):
    """מזכירה / מורה / מנהלת שמורשות לדווח על כל תלמידה."""

    name = models.CharField("שם", max_length=60)
    phone = models.CharField("טלפון", max_length=20)
    pin = models.CharField("קוד אישי (מגובב)", max_length=40, blank=True)
    active = models.BooleanField("פעיל", default=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["school", "phone"], name="uniq_caller_per_school")]

    def set_pin(self, pin):
        self.pin = hash_pin(pin) if pin else ""

    def save(self, *a, **kw):
        self.phone = clean_phone(self.phone)
        super().save(*a, **kw)

    def __str__(self):
        return f"{self.name} {self.phone}"


class Attendance(TenantModel):
    LATE_SCHOOL, LATE_LESSON, ABSENT = "late_school", "late_lesson", "absent"
    KINDS = [(LATE_SCHOOL, "איחור לבית ספר"), (LATE_LESSON, "איחור לשיעור"), (ABSENT, "חיסור")]
    REASONS = [("sick", "מחלה"), ("approved", "אישור"), ("other", "אחר"), ("", "ללא")]
    SOURCES = [("phone", "טלפון"), ("web", "אתר")]

    student = models.ForeignKey(Student, null=True, blank=True, on_delete=models.CASCADE, related_name="records")
    raw_tz = models.CharField("ת.ז. שהוקשה (לא מזוהה)", max_length=12, blank=True)
    kind = models.CharField("סוג", max_length=12, choices=KINDS)
    date = models.DateField("תאריך")
    date_to = models.DateField("עד תאריך (חיסור למספר ימים)", null=True, blank=True)
    time = models.TimeField("שעה", null=True, blank=True)
    lesson = models.PositiveSmallIntegerField("שיעור", null=True, blank=True)
    reason = models.CharField("סיבה", max_length=10, choices=REASONS, blank=True)
    justified = models.BooleanField("מוצדק", default=False)
    resolved = models.BooleanField("טופל (בדיווח לא מזוהה)", default=False)
    source = models.CharField(max_length=6, choices=SOURCES, default="phone")
    caller_phone = models.CharField(max_length=20, blank=True)
    source_ref = models.CharField(max_length=80, blank=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-date", "-created_at"]

    def __str__(self):
        return f"{self.student or self.raw_tz} {self.get_kind_display()} {self.date}"


class AlertRule(TenantModel):
    METRICS = [
        ("late_any", "איחורים (לבית ספר + לשיעור)"),
        ("late_school", "איחורים לבית ספר"),
        ("late_lesson", "איחורים לשיעור"),
        ("absent_any", "ימי חיסור (כולם)"),
        ("absent_unjustified", "ימי חיסור לא מוצדקים"),
    ]
    WINDOWS = [
        ("last_days", "X ימים אחרונים"),
        ("month", "החודש הלועזי הנוכחי"),
        ("week", "השבוע (מיום ראשון)"),
        ("school_year", "מתחילת שנת הלימודים"),
        ("custom", "טווח תאריכים קבוע"),
    ]
    TZ_TARGETS = [("contacts", "טלפוני ההורים/התלמידה המסומנים"), ("extra", "מספרים נוספים בלבד"),
                  ("both", "שניהם")]
    name = models.CharField("שם ההתראה", max_length=80)
    active = models.BooleanField("פעילה", default=True)
    metric = models.CharField("מה סופרים", max_length=20, choices=METRICS)
    threshold = models.PositiveSmallIntegerField("מספר (סף)", default=3)
    repeat = models.BooleanField("חוזרת בכל כפולה של הסף (7, 14, 21...)", default=False)
    window = models.CharField("טווח זמן", max_length=12, choices=WINDOWS, default="last_days")
    window_days = models.PositiveSmallIntegerField("מספר ימים (ל'X ימים אחרונים')", default=183)
    date_from = models.DateField(null=True, blank=True)
    date_to = models.DateField(null=True, blank=True)
    class_filter = models.ForeignKey(SchoolClass, null=True, blank=True, on_delete=models.CASCADE,
                                     verbose_name="רק לכיתה (ריק = כולן)")
    notify_site = models.BooleanField("להציג במסך ההתראות באתר", default=True)
    notify_caller = models.BooleanField("להשמיע למתקשרת בשיחה הבאה על התלמידה", default=True)
    message = models.CharField("נוסח ההודעה בטלפון", max_length=250,
                               default="לתלמידה יש {late} איחורים ו-{absent} ימי חיסור",
                               help_text="אפשר להשתמש ב: {name} {late} {absent} {count}")
    tzintuk = models.BooleanField("לשלוח צינתוק", default=False)
    tzintuk_voice = models.BooleanField("במקום צלצול - שיחה עם הקראת ההודעה", default=False)
    tzintuk_target = models.CharField("למי לצנתק", max_length=10, choices=TZ_TARGETS, default="contacts")
    extra_phones = models.CharField("מספרים נוספים (מופרדים בפסיק)", max_length=200, blank=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Alert(TenantModel):
    rule = models.ForeignKey(AlertRule, on_delete=models.CASCADE, related_name="alerts")
    student = models.ForeignKey(Student, on_delete=models.CASCADE, related_name="alerts")
    multiple = models.PositiveSmallIntegerField(default=1)
    count = models.PositiveSmallIntegerField()
    message = models.CharField(max_length=300)
    created_at = models.DateTimeField(default=timezone.now)
    window_start = models.DateField()
    announce_pending = models.BooleanField(default=False)
    announced_at = models.DateTimeField(null=True, blank=True)
    handled = models.BooleanField("טופל", default=False)
    tzintuk_status = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ["handled", "-created_at"]


class AuditLog(TenantModel):
    when = models.DateTimeField(default=timezone.now)
    actor = models.CharField(max_length=60)
    action = models.CharField(max_length=40)
    details = models.CharField(max_length=300, blank=True)

    class Meta:
        ordering = ["-when"]


class PendingAction(TenantModel):
    """שינוי שהעוזר האישי הציע וממתין ללחיצת אישור."""

    tool = models.CharField(max_length=40)
    args = models.JSONField()
    summary = models.CharField(max_length=400)
    status = models.CharField(max_length=10, default="pending")  # pending / done / failed / cancelled
    result = models.CharField(max_length=300, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
