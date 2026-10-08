"""הודעות לוח שנה שמוצגות בראש כל עמוד: אירועים של היום ושל מחר."""
from datetime import timedelta

from . import tenant
from .calendar_utils import today_il


def calendar_notices(request):
    if tenant.current() is None or not getattr(request, "user", None) or not request.user.is_authenticated:
        return {}
    from .models import CalendarEvent
    today = today_il()
    out = []
    for ev in CalendarEvent.objects.filter(announce_banner=True, date_from__lte=today + timedelta(days=1),
                                           date_to__gte=today)[:4]:
        when = "היום" if ev.date_from <= today <= ev.date_to else "מחר"
        out.append({"when": when, "text": ev.spoken_text(when).replace("שימו לב ", "", 1), "iso": max(ev.date_from, today).isoformat()
                    if when == "היום" else ev.date_from.isoformat()})
    return {"cal_notices": out}
