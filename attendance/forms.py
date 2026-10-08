from datetime import date

from django import forms

from .models import (WEEKDAYS, CalendarEvent, AlertRule, Attendance, AuthorizedCaller, Contact, LessonSlot, NonSchoolDay,
                     SchoolClass, SchoolSettings, Student)

DATE = forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")
TIME = forms.TimeInput(attrs={"type": "time"}, format="%H:%M")


class SettingsForm(forms.ModelForm):
    yemot_password_input = forms.CharField(label="סיסמת ימות המשיח", required=False,
                                           widget=forms.PasswordInput(render_value=False),
                                           help_text="משאירים ריק כדי לא לשנות. נשמרת מוצפנת.")
    assistant_key_input = forms.CharField(label="מפתח API של Claude (לעוזר האישי)", required=False,
                                          widget=forms.PasswordInput(render_value=False),
                                          help_text="מתקבל באתר console.anthropic.com. נשמר מוצפן. ריק = לא משנים. "
                                                    "העוזר שולח ל-Claude את שאלותיכם ונתונים מצטברים (שמות ומספרים, ת.ז. מוסתרת).")
    days_off_list = forms.MultipleChoiceField(label="ימים שלא נספרים כימי לימוד", required=False,
                                              choices=[(str(k), v) for k, v in WEEKDAYS],
                                              widget=forms.CheckboxSelectMultiple)

    field_order = ["school_name", "yemot_number", "yemot_password_input", "phone_dir", "auth_mode", "unknown_tz",
                   "ask_time", "ask_reason", "allow_other_date", "days_off_list"]

    class Meta:
        model = SchoolSettings
        fields = ["school_name", "yemot_number", "phone_dir", "caller_id", "voice_template_id", "auth_mode",
                  "unknown_tz", "ask_time", "ask_reason", "allow_other_date", "school_year_start", "assistant_model",
                  "support_contact"]
        widgets = {"school_year_start": DATE}

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.fields["days_off_list"].initial = [str(d) for d in sorted(self.instance.days_off_set)]

    def save(self, commit=True):
        obj = super().save(commit=False)
        obj.days_off = ",".join(sorted(self.cleaned_data["days_off_list"]))
        if self.cleaned_data["assistant_key_input"]:
            obj.assistant_key = self.cleaned_data["assistant_key_input"]
        if self.cleaned_data["yemot_password_input"]:
            obj.yemot_password = self.cleaned_data["yemot_password_input"]
        if commit:
            obj.save()
        return obj


class UniqueInSchoolMixin:
    """בדיקת ייחודיות בתוך בית הספר (שדה בית הספר לא נמצא בטופס, ולכן Django לא בודק לבד)."""

    unique_check = ()  # שמות שדות

    def clean(self):
        data = super().clean()
        for field in self.unique_check:
            value = data.get(field)
            if value in (None, ""):
                continue
            clash = self._meta.model.objects.filter(**{field: value}).exclude(pk=self.instance.pk)
            if clash.exists():
                self.add_error(field, "כבר קיים")
        return data


class PinMixin:
    """קוד אישי: מוזן כטקסט, נשמר מגובב. ריק = לא משנים."""

    def save(self, commit=True):
        obj = super().save(commit=False)
        pin = self.cleaned_data.get("pin_input", "")
        if pin:
            obj.set_pin(pin)
        if commit:
            obj.save()
        return obj


PIN_FIELD = forms.RegexField(r"^\d{4,8}$", label="קוד אישי לטלפון (4-8 ספרות)", required=False,
                             error_messages={"invalid": "4 עד 8 ספרות"},
                             help_text="ריק = לא משנים. נשמר מוצפן ואי אפשר להציגו שוב.")


class StudentForm(UniqueInSchoolMixin, forms.ModelForm):
    unique_check = ("tz",)

    class Meta:
        model = Student
        fields = ["tz", "first_name", "last_name", "school_class", "active", "notes"]

    def clean_tz(self):
        tz = "".join(c for c in self.cleaned_data["tz"] if c.isdigit())
        if not 5 <= len(tz) <= 9:
            raise forms.ValidationError("מספר זהות: 5 עד 9 ספרות")
        return tz.zfill(9)


class ContactForm(PinMixin, forms.ModelForm):
    pin_input = PIN_FIELD

    class Meta:
        model = Contact
        fields = ["label", "phone", "receives_alerts"]


class ClassForm(UniqueInSchoolMixin, forms.ModelForm):
    unique_check = ("name",)

    class Meta:
        model = SchoolClass
        fields = ["name", "order"]


class CallerForm(PinMixin, UniqueInSchoolMixin, forms.ModelForm):
    unique_check = ("phone",)
    pin_input = PIN_FIELD

    class Meta:
        model = AuthorizedCaller
        fields = ["name", "phone", "active"]

    def clean_phone(self):
        from .models import clean_phone
        return clean_phone(self.cleaned_data["phone"])


class HolidayForm(forms.ModelForm):
    class Meta:
        model = NonSchoolDay
        fields = ["name", "date_from", "date_to"]
        widgets = {"date_from": DATE, "date_to": DATE}


class LessonForm(forms.ModelForm):
    class Meta:
        model = LessonSlot
        fields = ["season", "number", "start", "end"]
        widgets = {"start": TIME, "end": TIME}

    def clean(self):
        d = super().clean()
        if d.get("number") is not None and d.get("season"):
            clash = LessonSlot.objects.filter(number=d["number"], season=d["season"]).exclude(pk=self.instance.pk)
            if clash.exists():
                self.add_error("number", "שיעור במספר הזה כבר קיים באותה מערכת")
        return d


class RuleForm(forms.ModelForm):
    class Meta:
        model = AlertRule
        exclude = []
        widgets = {"date_from": DATE, "date_to": DATE}


class RecordForm(forms.ModelForm):
    class Meta:
        model = Attendance
        fields = ["student", "kind", "date", "date_to", "time", "reason", "justified", "resolved"]
        widgets = {"date": DATE, "date_to": DATE, "time": TIME}

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.fields["student"].queryset = Student.objects.filter(active=True).select_related("school_class")
        self.fields["student"].required = False
        self.fields["date"].initial = date.today()


class ImportForm(forms.Form):
    file = forms.FileField(label="קובץ אקסל (xlsx) או CSV")


class CalendarEventForm(forms.ModelForm):
    class Meta:
        model = CalendarEvent
        fields = ["kind", "title", "date_from", "date_to", "time", "notes", "class_filter", "announce_banner",
                  "announce_phone"]
        widgets = {"date_from": DATE, "date_to": DATE, "time": TIME}

    def clean(self):
        d = super().clean()
        if d.get("date_from") and d.get("date_to") and d["date_to"] < d["date_from"]:
            self.add_error("date_to", "תאריך הסיום לפני תאריך ההתחלה")
        if d.get("kind") in ("late_start", "early_end") and not d.get("time"):
            self.add_error("time", "בהתחלה מאוחרת או סיום מוקדם צריך לציין שעה")
        return d


class CalendarSettingsForm(forms.ModelForm):
    from .zmanim import CANDLE_CHOICES, CITY_CHOICES
    city_key = forms.ChoiceField(label="עיר (לחישוב זמני היום)", choices=CITY_CHOICES)
    candle_minutes = forms.ChoiceField(label="מנהג הדלקת נרות", choices=CANDLE_CHOICES,
                                       help_text="כמה דקות לפני השקיעה מדליקים נרות. ביישובים רבים 18, בירושלים 40.")

    class Meta:
        model = SchoolSettings
        fields = ["city_key", "custom_lat", "custom_lon", "candle_minutes", "season_mode", "start_winter", "end_winter",
                  "start_summer", "end_summer", "friday_end"]
        widgets = {k: TIME for k in ("start_winter", "end_winter", "start_summer", "end_summer", "friday_end")}

    def clean_candle_minutes(self):
        return int(self.cleaned_data["candle_minutes"])
