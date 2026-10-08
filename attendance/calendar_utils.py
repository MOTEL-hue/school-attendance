"""ימי לימוד, תאריך עברי וזמן ישראל."""
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from pyluach import dates

from .models import NonSchoolDay, SchoolSettings

IL = ZoneInfo("Asia/Jerusalem")
DAY_NAMES = {6: "ראשון", 0: "שני", 1: "שלישי", 2: "רביעי", 3: "חמישי", 4: "שישי", 5: "שבת"}


def now_il() -> datetime:
    return datetime.now(IL)


def today_il() -> date:
    return now_il().date()


def hebrew_date(d: date) -> str:
    if not d:
        return ""
    return dates.GregorianDate(d.year, d.month, d.day).to_heb().hebrew_date_string()


def both_dates(d: date) -> str:
    return f"{d:%d/%m/%Y} ({hebrew_date(d)})" if d else ""


def _holidays(settings=None):
    return list(NonSchoolDay.objects.all())


def is_school_day(d: date, settings=None, holidays=None) -> bool:
    settings = settings or SchoolSettings.get()
    if d.weekday() in settings.days_off_set:
        return False
    for h in (holidays if holidays is not None else _holidays()):
        if h.date_from <= d <= h.date_to:
            return False
    return True


def school_days_between(d1: date, d2: date, settings=None) -> list:
    """רשימת ימי הלימוד בטווח (כולל קצוות). חיסור שנופל על חופשה לא נספר."""
    settings = settings or SchoolSettings.get()
    holidays = _holidays()
    out, d = [], d1
    while d <= d2:
        if is_school_day(d, settings, holidays):
            out.append(d)
        d += timedelta(days=1)
    return out
