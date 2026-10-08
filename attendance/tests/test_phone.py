from datetime import timedelta
from unittest import mock

from attendance.calendar_utils import today_il
from attendance.models import Alert, AlertRule, Attendance, Student
from attendance.phone import handle

from .base import Base

P = {"ApiPhone": "0521111111", "ApiCallId": "c1"}


class PhoneFlow(Base):
    def test_first_prompt_asks_tz(self):
        out = handle(dict(P))
        self.assertTrue(out.startswith("read=") and "=tz1_1," in out)

    def test_late_school_full_call(self):
        p = dict(P, tz1_1="012345678")
        out = handle(p)
        self.assertIn("=k1,", out)
        p["k1"] = "1"
        out = handle(p)
        self.assertIn("=more1,", out)
        rec = Attendance.objects.get()
        self.assertEqual((rec.kind, rec.student, rec.source), ("late_school", self.st, "phone"))
        self.assertIsNotNone(rec.time)
        # שליחה חוזרת של אותה בקשה לא יוצרת דיווח כפול
        handle(p)
        self.assertEqual(Attendance.objects.count(), 1)

    def test_two_students_in_one_call_and_finish(self):
        st2 = Student.objects.create(tz="000000002", first_name="רחל", last_name="לוי", school_class=self.cls,
                                     name_recorded=True)
        p = dict(P, tz1_1="012345678", k1="1", more1="1")
        self.assertIn("=tz2_1,", handle(p))
        p.update(tz2_1="2", k2="3", dt2="1", rs2="1")
        self.assertIn("=more2,", handle(p))
        ab = Attendance.objects.get(student=st2)
        self.assertEqual((ab.kind, ab.reason, ab.justified), ("absent", "sick", True))
        p["more2"] = "2"
        self.assertTrue(handle(p).startswith("id_list_message="))

    def test_absent_several_days_and_tomorrow(self):
        p = dict(P, tz1_1="012345678", k1="3", dt1="4", dd1="3", rs1="3")
        handle(p)
        rec = Attendance.objects.get()
        self.assertEqual(rec.date_to, today_il() + timedelta(days=2))
        self.assertFalse(rec.justified)

    def test_lesson_late_with_custom_time(self):
        from attendance.models import LessonSlot
        from datetime import time
        LessonSlot.objects.create(number=3, start=time(9, 0), end=time(9, 45))
        p = dict(P, tz1_1="012345678", k1="2", tm1="2", hm1_1="0915")
        handle(p)
        rec = Attendance.objects.get()
        self.assertEqual((rec.kind, rec.time.strftime("%H:%M"), rec.lesson), ("late_lesson", "09:15", 3))

    def test_unknown_tz_retries_then_records_unmatched(self):
        p = dict(P, tz1_1="111111111")
        self.assertIn("=tz1_2,", handle(p))
        p["tz1_2"] = "222222222"
        self.assertIn("=tz1_3,", handle(p))
        p["tz1_3"] = "333333333"
        self.assertIn("=k1,", handle(p))
        p["k1"] = "1"
        handle(p)
        rec = Attendance.objects.get()
        self.assertIsNone(rec.student)
        self.assertEqual(rec.raw_tz, "333333333")

    def test_unknown_tz_reject_policy(self):
        self.s.unknown_tz = "reject"
        self.s.save()
        p = dict(P, tz1_1="1", tz1_2="2", tz1_3="3")
        self.assertIn("go_to_folder=hangup", handle(p))
        self.assertEqual(Attendance.objects.count(), 0)

    def test_name_recording_first_time(self):
        self.st.name_recorded = False
        self.st.save()
        out = handle(dict(P, tz1_1="012345678"))
        self.assertIn(f"=nm{self.st.tz},no,record,/7,name_{self.st.tz}", out)
        out = handle(dict(P, tz1_1="012345678", **{f"nm{self.st.tz}": "ok"}))
        self.assertIn("=k1,", out)
        self.st.refresh_from_db()
        self.assertTrue(self.st.name_recorded)
        self.assertIn(f"f-/7/name_{self.st.tz}", out)

    def test_auth_phone_mode_rejects_unknown_number(self):
        self.s.auth_mode = "phone"
        self.s.save()
        self.assertIn("go_to_folder=hangup", handle(dict(P)))
        self.assertIn("=tz1_1,", handle({"ApiPhone": "0527000000", "ApiCallId": "x"}))

    def test_auth_pin_mode(self):
        self.s.auth_mode = "phone_pin"
        self.s.save()
        base = {"ApiPhone": "0527000000", "ApiCallId": "x"}
        self.assertIn("=pin_1,", handle(base))
        self.assertIn("=pin_2,", handle(dict(base, pin_1="0000")))
        self.assertIn("=tz1_1,", handle(dict(base, pin_1="9999")))
        self.assertIn("go_to_folder=hangup", handle(dict(base, pin_1="1", pin_2="2", pin_3="3")))

    def test_parent_can_only_report_own_daughter(self):
        self.s.auth_mode = "phone"
        self.s.save()
        Student.objects.create(tz="000000002", first_name="רחל", last_name="לוי", school_class=self.cls)
        p = {"ApiPhone": "0501234567", "ApiCallId": "x", "tz1_1": "2", "tz1_2": "2", "tz1_3": "2"}
        self.assertIn("go_to_folder=hangup", handle(p))
        self.assertIn("=k1,", handle({"ApiPhone": "0501234567", "ApiCallId": "x", "tz1_1": "012345678"}))

    def test_pending_alert_announced_once(self):
        Alert.objects.create(rule=AlertRule.objects.create(name="r", metric="late_any"), student=self.st, count=3,
                             message="לשרה יש שלושה איחורים", window_start=today_il(), announce_pending=True)
        out = handle(dict(P, tz1_1="012345678"))
        self.assertIn("שלושה איחורים", out)
        self.assertNotIn("שלושה איחורים", handle(dict(P, tz1_1="012345678")))

    def test_hangup_and_errors_are_safe(self):
        self.assertEqual(handle({"hangup": "yes"}), "noop=hangup")
        with mock.patch("attendance.phone._handle", side_effect=RuntimeError("x")):
            self.assertIn("go_to_folder=hangup", handle(dict(P)))

    def test_prompt_text_has_no_reserved_chars(self):
        self.st.first_name = "שרה, בת. א=ב"
        self.st.name_recorded = False
        self.st.save()
        self.s.phone_dir = ""
        self.s.save()
        out = handle(dict(P, tz1_1="012345678"))
        text = out.split("=", 1)[1].rsplit("=", 1)[0]
        self.assertNotIn(",", text.replace("שרה", ""))
