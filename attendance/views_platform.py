"""ממשק הבעלים של המערכת: אישור בתי ספר, השעיה, יצירה ישירה, כניסה לבית ספר לצורך תמיכה."""
import secrets

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required, user_passes_test
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from .models import Attendance, School, Student
from .views_public import RegisterForm, create_school, delete_school

owner_only = user_passes_test(lambda u: u.is_authenticated and u.is_superuser, login_url="login")


@login_required
@owner_only
def platform(request):
    schools = list(School.objects.all())
    ids = [s.pk for s in schools]
    students = dict(Student.all_objects.filter(school_id__in=ids, active=True).values_list("school_id")
                    .annotate(n=Count("id")))
    reports = dict(Attendance.all_objects.filter(school_id__in=ids).values_list("school_id").annotate(n=Count("id")))
    for s in schools:
        s.students_n, s.reports_n = students.get(s.pk, 0), reports.get(s.pk, 0)
    order = {School.PENDING: 0, School.ACTIVE: 1, School.SUSPENDED: 2}
    schools.sort(key=lambda s: order[s.status])
    return render(request, "attendance/platform.html", {"schools": schools,
                                                        "pending": sum(s.status == School.PENDING for s in schools)})


class CreateSchoolForm(forms.Form):
    school_name = forms.CharField(label="שם בית הספר", max_length=120)
    contact_name = forms.CharField(label="איש קשר", max_length=80, required=False)
    contact_phone = forms.CharField(label="טלפון", max_length=20, required=False)
    email = forms.EmailField(label="מייל (שם משתמש)")

    def clean_email(self):
        from django.contrib.auth.models import User
        email = self.cleaned_data["email"].strip().lower()
        if User.objects.filter(username=email).exists():
            raise forms.ValidationError("כתובת המייל כבר רשומה")
        return email


@login_required
@owner_only
def platform_create(request):
    form = CreateSchoolForm(request.POST or None)
    temp = None
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        temp = secrets.token_urlsafe(9)
        create_school(d["school_name"], d["email"], temp, d["contact_name"], d["contact_phone"],
                      status=School.ACTIVE)
        messages.success(request, f"נוצר. שם משתמש: {d['email']} · סיסמה זמנית: {temp} (שלחו לבית הספר, והם יוכלו להחליף).")
        return redirect("platform")
    return render(request, "attendance/platform_create.html", {"form": form})


@login_required
@owner_only
@require_POST
def platform_action(request, pk, action):
    school = get_object_or_404(School, pk=pk)
    if action == "approve":
        school.status, school.approved_at = School.ACTIVE, timezone.now()
        school.save(update_fields=["status", "approved_at"])
        messages.success(request, f"{school.name} אושר")
    elif action == "suspend":
        school.status = School.SUSPENDED
        school.save(update_fields=["status"])
        messages.success(request, f"{school.name} הושעה (הטלפון והאתר חסומים)")
    elif action == "resume":
        school.status = School.ACTIVE
        school.save(update_fields=["status"])
        messages.success(request, f"{school.name} הופעל מחדש")
    elif action == "delete":
        if request.POST.get("confirm", "").strip() != school.name:
            messages.error(request, "לא נמחק: יש להקליד את שם בית הספר במדויק.")
        else:
            delete_school(school)
            messages.success(request, "בית הספר וכל נתוניו נמחקו")
    elif action == "notes":
        school.owner_notes = request.POST.get("notes", "")[:300]
        school.save(update_fields=["owner_notes"])
    elif action == "enter":
        request.session["viewing_school"] = school.pk
        return redirect("dashboard")
    return redirect("platform")


@login_required
@owner_only
def platform_exit(request):
    request.session.pop("viewing_school", None)
    return redirect("platform")
