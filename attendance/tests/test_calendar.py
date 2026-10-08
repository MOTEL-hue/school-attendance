from datetime import date, time
from unittest import mock

from django.test import Client

from attendance import tenant, zmanim as zm
from attendance.models import CalendarEvent, Contact, LessonSlot, MessageLog, NonSchoolDay, SchoolSettings, Student
from attendance.phone import handle
from attendance.services import lesson_for_time, lessons_for_date

from .base import Base, make_school


class CalendarLogic(Base):
    def test_zmanim_order_and_candle_lighting(self):
        z = zm.zmanim_for(self.s, date(2026, 10, 9))  # יום שישי
        self.assertTrue(z["hanetz"] < z["chatzos"] < z["shkia"] < z["tzais"])
        self.assertEqual((z["shkia"] - z["candle"]).seconds // 60, self.s.candle_minutes)
        self.assertIsNone(zm.zmanim_for(self.s, date(2026, 10, 8))["candle"])  # יום חמישי רגיל
        self.assertIsNotNone(zm.zmanim_for(self.s, date(2026, 10, 10))["havdalah"])  # מוצאי שבת

    def test_candle_before_yom_tov_and_hebrew_names(self):
        self.assertIsNotNone(zm.zmanim_for(self.s, date(2026, 9, 11))["candle"])  # ערב ר"ה (שישי)
        self.assertEqual(zm.festival(date(2026, 9, 12)), "ראש השנה")
        self.assertTrue(zm.rosh_chodesh(date(2026, 10, 12)))

    def test_dst_dates_and_season(self):
        self.assertEqual(zm.dst_changes(2026), [(date(2026, 3, 27), "summer"), (date(2026, 10, 25), "winter")])
        self.assertEqual(zm.season_for(self.s, date(2026, 7, 1)), "summer")
        self.assertEqual(zm.season_for(self.s, date(2026, 12, 1)), "winter")
        self.s.season_mode = "winter"
        self.assertEqual(zm.season_for(self.s, date(2026, 7, 1)), "winter")

    def test_lessons_follow_season_and_override_all(self):
        LessonSlot.objects.create(number=1, season="all", start=time(8, 0), end=time(8, 45))
        LessonSlot.objects.create(number=2, season="all", start=time(8, 45), end=time(9, 30))
        LessonSlot.objects.create(number=1, season="summer", start=time(7, 30), end=time(8, 15))
        summer = {l.number: l.start for l in lessons_for_date(date(2026, 7, 1), self.s)}
        winter = {l.number: l.start for l in lessons_for_date(date(2026, 12, 1), self.s)}
        self.assertEqual((summer[1], summer[2]), (time(7, 30), time(8, 45)))
        self.assertEqual(winter[1], time(8, 0))
        self.assertEqual(lesson_for_time(time(7, 40), date(2026, 7, 1)), 1)
        self.assertIsNone(lesson_for_time(time(7, 40), date(2026, 12, 1)))

    def test_same_lesson_number_allowed_per_season_only(self):
        c = Client(); c.login(username="a@x.com", password="pw")
        post = {"season": "summer", "number": 1, "start": "07:30", "end": "08:15"}
        c.post("/manage/lessons/new/", post)
        c.post("/manage/lessons/new/", post)
        self.assertEqual(LessonSlot.objects.filter(number=1, season="summer").count(), 1)
        c.post("/manage/lessons/new/", {**post, "season": "winter"})
        self.assertEqual(LessonSlot.objects.filter(number=1).count(), 2)


class CalendarPages(Base):
    def setUp(self):
        super().setUp()
        self.c = Client(); self.c.login(username="a@x.com", password="pw")

    def test_pages_render(self):
        for url in ["/calendar/", "/calendar/?y=2026&m=10", "/calendar/day/2026-10-09/", "/calendar/settings/",
                    "/calendar/zmanim/", "/calendar/zmanim/?export=xlsx", "/calendar/holidays/", "/calendar/event/new/", "/"]:
            self.assertEqual(self.c.get(url).status_code, 200, url)
        self.assertContains(self.c.get("/calendar/day/2026-10-09/"), "הדלקת נרות")
        self.assertEqual(self.c.get("/calendar/day/not-a-date/").status_code, 404)

    def test_late_start_event_banner_phone_and_send(self):
        today = date.today()
        r = self.c.post("/calendar/event/new/", {"kind": "late_start", "title": "יום עיון", "date_from": today.isoformat(),
                                                  "date_to": today.isoformat(), "time": "09:30", "announce_banner": "on", "announce_phone": "on"})
        ev = CalendarEvent.objects.get()
        self.assertRedirects(r, f"/calendar/event/{ev.pk}/send/")
        self.assertContains(self.c.get("/"), "הלימודים יתחילו בשעה 09:30")  # באנר
        out = handle({"ApiPhone": "0521111111", "ApiCallId": "1"})
        self.assertIn("הלימודים יתחילו בשעה 09 30", out)  # נשמע בתחילת השיחה (נקודתיים הוחלפו)
        self.assertIn("=tz1_1,", out)
        # שליחה: צריך אישור, ערוץ, ומונעת כפילות
        self.s.yemot_number, self.s.yemot_password = "0771", "p"
        self.s.save()
        url = f"/calendar/event/{ev.pk}/send/"
        self.c.post(url, {"tzintuk": "on"})  # בלי אישור
        self.assertEqual(MessageLog.objects.count(), 0)
        with mock.patch("attendance.yemot_client._call") as m:
            self.c.post(url, {"tzintuk": "on", "confirm": "on", "parents_only": "1"})
            self.c.post(url, {"tzintuk": "on", "confirm": "on", "parents_only": "1"})  # כפילות
        self.assertEqual(m.call_count, 1)
        self.assertEqual(m.call_args[0][2]["phones"], "0501234567")
        self.assertEqual(MessageLog.objects.get().recipients, 1)

    def test_late_start_requires_time_and_dates_valid(self):
        d = date.today().isoformat()
        self.c.post("/calendar/event/new/", {"kind": "late_start", "title": "x", "date_from": d, "date_to": d})
        self.assertEqual(CalendarEvent.objects.count(), 0)
        self.c.post("/calendar/event/new/", {"kind": "event", "title": "x", "date_from": "2026-10-10", "date_to": "2026-10-01"})
        self.assertEqual(CalendarEvent.objects.count(), 0)

    def test_holidays_auto_adds_selected_once(self):
        r = self.c.get("/calendar/holidays/?year=2026")
        self.assertContains(r, "ראש השנה")
        from attendance.views_calendar import _proposals
        picks = [g["id"] for g in _proposals(2026) if g["default"]]
        self.c.post("/calendar/holidays/?year=2026", {"pick": picks})
        n = NonSchoolDay.objects.count()
        self.assertGreaterEqual(n, 6)
        self.c.post("/calendar/holidays/?year=2026", {"pick": picks})
        self.assertEqual(NonSchoolDay.objects.count(), n)  # בלי כפילויות

    def test_events_isolated_between_schools(self):
        other = make_school("אחר", secret="o", email="o@x.com")
        tok = tenant.activate(other)
        try:
            CalendarEvent.objects.create(kind="event", title="סודי", date_from=date.today(), date_to=date.today())
        finally:
            tenant.deactivate(tok)
        self.assertNotContains(self.c.get("/calendar/"), "סודי")
        self.assertEqual(CalendarEvent.objects.count(), 0)

    def test_calendar_settings_saved(self):
        self.c.post("/calendar/settings/", {"city_key": "safed", "candle_minutes": "22", "season_mode": "auto",
                                            "start_winter": "08:00", "end_winter": "14:30", "friday_end": "12:00"})
        s = SchoolSettings.get()
        self.assertEqual((s.city_key, s.candle_minutes, s.friday_end), ("safed", 22, time(12, 0)))
