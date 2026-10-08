from datetime import timedelta
from unittest import mock

from attendance.alerts import evaluate_student
from attendance.calendar_utils import today_il
from attendance.models import Alert, AlertRule, Attendance, Contact
from attendance.services import create_record

from .base import Base


class AlertTests(Base):
    def late(self, n=1, days_ago=0):
        for i in range(n):
            create_record(student=self.st, kind="late_school", date=today_il() - timedelta(days=days_ago + i))

    def test_threshold_fires_once(self):
        AlertRule.objects.create(name="3 איחורים", metric="late_any", threshold=3, window="last_days", window_days=30)
        self.late(2)
        self.assertEqual(Alert.objects.count(), 0)
        self.late(1, days_ago=5)
        self.assertEqual(Alert.objects.count(), 1)
        self.late(1, days_ago=9)
        self.assertEqual(Alert.objects.count(), 1)

    def test_repeat_every_multiple(self):
        AlertRule.objects.create(name="כל 2", metric="late_any", threshold=2, repeat=True, window="last_days",
                                 window_days=100)
        self.late(4)
        self.assertEqual(sorted(Alert.objects.values_list("multiple", flat=True)), [1, 2])

    def test_absence_counts_school_days_only(self):
        # יום חיסור שחל על שישי-שבת לא נספר
        AlertRule.objects.create(name="חיסור", metric="absent_any", threshold=100)
        from attendance.alerts import counts_for
        monday = today_il() - timedelta(days=(today_il().weekday()))
        create_record(student=self.st, kind="absent", date=monday - timedelta(days=7), date_to=monday - timedelta(days=1))
        c = counts_for(self.st, monday - timedelta(days=7), monday - timedelta(days=1))
        self.assertEqual(c[2], 5)  # שני עד ראשון בלי שישי/שבת

    def test_unjustified_absence_metric(self):
        AlertRule.objects.create(name="לא מוצדק", metric="absent_unjustified", threshold=1)
        create_record(student=self.st, kind="absent", date=today_il(), reason="sick")
        self.assertEqual(Alert.objects.count(), 0)
        create_record(student=self.st, kind="absent", date=today_il() - timedelta(days=1), reason="other")
        self.assertEqual(Alert.objects.count(), 1)

    def test_message_and_announce_flag(self):
        AlertRule.objects.create(name="x", metric="late_any", threshold=1,
                                 message="ל{name} יש {late} איחורים ו-{absent} חיסורים")
        self.late(1)
        a = Alert.objects.get()
        self.assertEqual(a.message, "לשרה יש 1 איחורים ו-0 חיסורים")
        self.assertTrue(a.announce_pending)

    def test_tzintuk_sent_to_flagged_contacts_and_extra(self):
        Contact.objects.create(student=self.st, label="אבא", phone="0529999999", receives_alerts=False)
        AlertRule.objects.create(name="t", metric="late_any", threshold=1, tzintuk=True, tzintuk_target="both",
                                 extra_phones="0541111111")
        with mock.patch("attendance.yemot_client.run_tzintuk", return_value="צינתוק") as m:
            self.late(1)
        phones = m.call_args[0][1]
        self.assertEqual(sorted(phones), ["0501234567", "0541111111"])
        self.assertIn("נשלח", Alert.objects.get().tzintuk_status)

    def test_tzintuk_error_is_recorded_not_raised(self):
        from attendance.yemot_client import YemotError
        AlertRule.objects.create(name="t", metric="late_any", threshold=1, tzintuk=True)
        with mock.patch("attendance.yemot_client.run_tzintuk", side_effect=YemotError("boom")):
            self.late(1)
        self.assertIn("boom", Alert.objects.get().tzintuk_status)
        self.assertEqual(Attendance.objects.count(), 1)

    def test_class_filter(self):
        from attendance.models import SchoolClass
        other = SchoolClass.objects.create(name="ט1")
        AlertRule.objects.create(name="x", metric="late_any", threshold=1, class_filter=other)
        self.late(1)
        self.assertEqual(Alert.objects.count(), 0)
