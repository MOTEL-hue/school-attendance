"""רישום שגיאות שרת בעברית, ללא גוף בקשה או פרטי התחברות."""
import logging

logger = logging.getLogger("monitoring")


class HebrewErrorLoggingMiddleware:
    """נרשם רק בעת חריגה; משתמש ביומן הפלט הקיים של סביבת ההרצה."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)

    def process_exception(self, request, exception):
        # מתעדים נתיב בלבד, ללא query string, cookies, גוף או פרטי משתמש.
        path = (getattr(request, "path", "/") or "/")[:160]
        logger.error(
            "שגיאת שרת | מערכת: נוכחות | נתיב: %s | סוג: %s | פרטים: %s",
            path,
            type(exception).__name__[:80],
            str(exception).replace("\n", " ")[:240],
        )
        return None
