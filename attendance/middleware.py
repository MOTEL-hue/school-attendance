from django.http import HttpResponseForbidden
from django.shortcuts import redirect, render

from . import tenant
from .models import School

OPEN_PREFIXES = ("/phone/", "/healthz", "/static/", "/login/", "/logout/", "/register/", "/terms/", "/privacy/",
                 "/support/")


class TenantMiddleware:
    """קובע את בית הספר הנוכחי לכל בקשה (ראו tenant.py) וחוסם בתי ספר שלא אושרו או הושעו."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        path = request.path
        if path.startswith(("/phone/", "/healthz", "/static/")):
            return self.get_response(request)  # בית הספר נקבע שם לפי המפתח בכתובת

        user = request.user
        school = None
        support_id = request.session.get("support_school")
        if support_id:
            school = School.objects.filter(pk=support_id, status=School.ACTIVE).first()
        elif user.is_authenticated:
            if user.is_superuser:
                vid = request.session.get("viewing_school")
                school = School.objects.filter(pk=vid).first() if vid else None
            else:
                profile = getattr(user, "school_profile", None)
                school = profile.school if profile else None
        request.school = school

        if user.is_authenticated and not path.startswith(OPEN_PREFIXES):
            if user.is_superuser and not support_id:
                if school is None and not path.startswith("/platform/"):
                    return redirect("platform")
            elif path.startswith("/platform/"):
                return HttpResponseForbidden("אין הרשאה")
            elif school is None or school.status != School.ACTIVE:
                return render(request, "attendance/school_status.html", {"school": school}, status=403)

        token = tenant.activate(school)
        try:
            return self.get_response(request)
        finally:
            tenant.deactivate(token)


class SupportReadOnlyMiddleware:
    """כניסת תמיכה היא לצפייה בלבד: חוסמת כל פעולה ששולחת נתונים (חוץ מיציאה)."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.session.get("support_school") and request.method not in ("GET", "HEAD", "OPTIONS") \
                and not request.path.startswith("/logout"):
            return HttpResponseForbidden("כניסת תמיכה היא לצפייה בלבד")
        return self.get_response(request)
