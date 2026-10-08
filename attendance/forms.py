from datetime import date

from django import forms

from .models import (WEEKDAYS, AlertRule, Attendance, AuthorizedCaller, Contact, LessonSlot, NonSchoolDay,
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


class StudentForm(forms.ModelForm):
    class Meta:
        model = Student
        fields = ["tz", "first_name", "last_name", "school_class", "active", "notes"]

    def clean_tz(self):
        tz = "".join(c for c in self.cleaned_data["tz"] if c.isdigit())
        if not 5 <= len(tz) <= 9:
            raise forms.ValidationError("מספר זהות: 5 עד 9 ספרות")
        return tz.zfill(9)


class ContactForm(forms.ModelForm):
    class Meta:
        model = Contact
        fields = ["label", "phone", "receives_alerts", "pin"]


class ClassForm(forms.ModelForm):
    class Meta:
        model = SchoolClass
        fields = ["name", "order"]


class CallerForm(forms.ModelForm):
    class Meta:
        model = AuthorizedCaller
        fields = ["name", "phone", "pin", "active"]


class HolidayForm(forms.ModelForm):
    class Meta:
        model = NonSchoolDay
        fields = ["name", "date_from", "date_to"]
        widgets = {"date_from": DATE, "date_to": DATE}


class LessonForm(forms.ModelForm):
    class Meta:
        model = LessonSlot
        fields = ["number", "start", "end"]
        widgets = {"start": TIME, "end": TIME}


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
