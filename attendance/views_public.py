"""עמודים ציבוריים: הרשמה, כניסה עם הגבלת ניסיונות, תנאים ופרטיות, בדיקת חיים, מחיקת בית ספר."""
from django.contrib.auth import logout
from django.contrib.auth import password_validation
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.contrib.auth.views import LoginView
from django.core.cache import cache
from django import forms
from django.db import transaction
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from .models import (Alert, AlertRule, Attendance, AuditLog, AuthorizedCaller, Contact, LessonSlot, NonSchoolDay,
                     PendingAction, School, SchoolClass, SchoolSettings, SchoolUser, Student)

LOGIN_MAX_FAILS, LOGIN_LOCK_SECONDS = 6, 15 * 60
REGISTER_MAX_PER_HOUR = 5


def healthz(request):
    return HttpResponse("ok", content_type="text/plain")


def client_ip(request):
    fwd = request.META.get("HTTP_X_FORWARDED_FOR", "")
    return (fwd.split(",")[0].strip() if fwd else request.META.get("REMOTE_ADDR", "")) or "?"


class ThrottledLoginView(LoginView):
    template_name = "attendance/login.html"

    def _key(self, username):
        return f"loginfail:{(username or '').strip().lower()}"

    def post(self, request, *args, **kwargs):
        if cache.get(self._key(request.POST.get("username")), 0) >= LOGIN_MAX_FAILS:
            form = self.get_form()
            form.add_error(None, "יותר מדי ניסיונות כושלים. נסו שוב בעוד כ-15 דקות.")
            return self.render_to_response(self.get_context_data(form=form))
        return super().post(request, *args, **kwargs)

    def form_invalid(self, form):
        key = self._key(self.request.POST.get("username"))
        cache.set(key, cache.get(key, 0) + 1, LOGIN_LOCK_SECONDS)
        return super().form_invalid(form)

    def form_valid(self, form):
        cache.delete(self._key(form.cleaned_data["username"]))
        return super().form_valid(form)


class RegisterForm(forms.Form):
    school_name = forms.CharField(label="שם בית הספר / הסמינר", max_length=120)
    contact_name = forms.CharField(label="שם איש הקשר", max_length=80)
    contact_phone = forms.CharField(label="טלפון", max_length=20)
    email = forms.EmailField(label="מייל (ישמש לכניסה)")
    password = forms.CharField(label="סיסמה (לפחות 8 תווים)", widget=forms.PasswordInput)
    password2 = forms.CharField(label="אימות סיסמה", widget=forms.PasswordInput)
    accept = forms.BooleanField(label="קראתי ואני מסכים/ה לתנאי השימוש ולמדיניות הפרטיות",
                                error_messages={"required": "יש לאשר את התנאים"})

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        if User.objects.filter(username=email).exists():
            raise forms.ValidationError("כתובת המייל כבר רשומה")
        return email

    def clean(self):
        d = super().clean()
        if d.get("password") and d["password"] != d.get("password2"):
            self.add_error("password2", "הסיסמאות לא זהות")
        elif d.get("password"):
            try:
                password_validation.validate_password(d["password"])
            except forms.ValidationError as e:
                self.add_error("password", e)
        return d


@transaction.atomic
def create_school(name, email, password, contact_name="", contact_phone="", status=School.PENDING, accepted=True):
    school = School.objects.create(
        name=name, status=status, contact_name=contact_name, contact_phone=contact_phone, contact_email=email,
        terms_accepted_at=timezone.now() if accepted else None,
        approved_at=timezone.now() if status == School.ACTIVE else None)
    SchoolSettings.all_objects.create(school=school, school_name=name)
    user = User.objects.create_user(username=email.lower(), email=email.lower(), password=password)
    SchoolUser.objects.create(user=user, school=school)
    return school


def register(request):
    if request.user.is_authenticated:
        return redirect("dashboard")
    form = RegisterForm(request.POST or None)
    key = f"register:{client_ip(request)}"
    if request.method == "POST" and cache.get(key, 0) >= REGISTER_MAX_PER_HOUR:
        form.add_error(None, "יותר מדי הרשמות מהכתובת הזאת. נסו שוב מאוחר יותר.")
    elif request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        create_school(d["school_name"], d["email"], d["password"], d["contact_name"], d["contact_phone"])
        cache.set(key, cache.get(key, 0) + 1, 3600)
        return render(request, "attendance/register_done.html", {"name": d["school_name"]})
    return render(request, "attendance/register.html", {"form": form})


def terms(request):
    from django.conf import settings
    return render(request, "attendance/terms.html", {"OWNER_CONTACT": settings.OWNER_CONTACT})


def privacy(request):
    from django.conf import settings
    return render(request, "attendance/privacy.html", {"OWNER_CONTACT": settings.OWNER_CONTACT})


TENANT_MODELS_DELETE_ORDER = [Alert, AlertRule, Attendance, Contact, Student, SchoolClass, AuthorizedCaller,
                              LessonSlot, NonSchoolDay, PendingAction, AuditLog, SchoolSettings]


@transaction.atomic
def delete_school(school):
    """מחיקה מלאה של בית ספר וכל נתוניו (לפי סדר שמונע התנגשות בין קשרים)."""
    for model in TENANT_MODELS_DELETE_ORDER:
        model.all_objects.filter(school=school).delete()
    User.objects.filter(school_profile__school=school).delete()
    school.delete()


@login_required
@require_POST
def school_delete(request):
    school = request.school
    if request.session.get("support_school") or school is None \
            or request.POST.get("confirm", "").strip() != school.name:
        from django.contrib import messages
        messages.error(request, "לא נמחק: יש להקליד את שם בית הספר במדויק.")
        return redirect("settings")
    delete_school(school)
    logout(request)
    return render(request, "attendance/goodbye.html")
