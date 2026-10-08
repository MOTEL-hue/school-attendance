from django.http import HttpResponseForbidden


class SupportReadOnlyMiddleware:
    """כניסת תמיכה היא לצפייה בלבד: חוסמת כל פעולה ששולחת נתונים (חוץ מיציאה)."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.session.get("support") and request.method not in ("GET", "HEAD", "OPTIONS") \
                and not request.path.startswith("/logout"):
            return HttpResponseForbidden("כניסת תמיכה היא לצפייה בלבד")
        return self.get_response(request)
