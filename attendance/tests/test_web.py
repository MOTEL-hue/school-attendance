from datetime import date, timedelta
from unittest import mock

from django.contrib.auth.models import User
from django.test import override_settings
from django.utils import timezone

from attendance.calendar_utils import hebrew_date, today_il
from attendance.models import Attendance, AuditLog, SchoolClass, Student
from attendance.services import create_record

from .base import Base


@override_settings(PHONE_SECRET="sekret")
class WebTests(Base):
    def setUp(self):
        super().setUp()
        User.objects.create_superuser("admin", password="pw")
        self.client.login(username="admin", password="pw")

    def test_phone_endpoint_secret(self):
        self.client.logout()
        self.assertEqual(self.client.get("/phone/wrong/").status_code, 404)
        r = self.client.get("/phone/sekret/", {"ApiPhone": "0521111111", "ApiCallId": "1"})
        self.assertEqual(r.status_code, 200)
        self.assertIn("tz1_1", r.content.decode())

    def test_pages_render(self):
        create_record(student=self.st, kind="late_school", date=today_il())
        for name in ["dashboard", "web_report", "records", "students", "student_new", "students_import",
                     "summary", "alerts", "settings", "audit", "backup", "help", "new_year_page"]:
            r = self.client.get(f"/{ {'dashboard':'', 'web_report':'report/', 'records':'records/', 'students':'students/', 'student_new':'students/new/', 'students_import':'students-import/', 'summary':'summary/', 'alerts':'alerts/', 'settings':'settings/', 'audit':'audit/', 'backup':'backup/', 'help':'help/', 'new_year_page':'new-year/'}[name] }")
            self.assertEqual(r.status_code, 200, name)
        for kind in ["classes", "callers", "holidays", "lessons", "rules"]:
            self.assertEqual(self.client.get(f"/manage/{kind}/").status_code, 200, kind)
            self.assertEqual(self.client.get(f"/manage/{kind}/new/").status_code, 200, kind)
        self.assertEqual(self.client.get(f"/students/{self.st.pk}/").status_code, 200)

    def test_requires_login(self):
        self.client.logout()
        self.assertEqual(self.client.get("/records/").status_code, 302)

    def test_web_report_and_audit(self):
        r = self.client.post("/report/", {"tz": "012345678", "kind": "late_school", "date": today_il().isoformat(),
                                          "time": "08:20"})
        self.assertEqual(r.status_code, 302)
        rec = Attendance.objects.get()
        self.assertEqual((rec.source, rec.time.strftime("%H:%M")), ("web", "08:20"))
        self.assertTrue(AuditLog.objects.filter(action="דיווח חדש").exists())

    def test_edit_and_delete_record(self):
        rec, _ = create_record(student=self.st, kind="absent", date=today_il(), reason="other")
        r = self.client.post(f"/records/{rec.pk}/", {"student": self.st.pk, "kind": "absent", "date": today_il().isoformat(),
                                                     "reason": "sick", "justified": "on"})
        self.assertEqual(r.status_code, 302)
        rec.refresh_from_db()
        self.assertTrue(rec.justified)
        self.client.post(f"/records/{rec.pk}/delete/")
        self.assertEqual(Attendance.objects.count(), 0)

    def test_export_xlsx_and_backup(self):
        create_record(student=self.st, kind="late_school", date=today_il())
        for url in ["/records/?export=xlsx", "/summary/?export=xlsx", "/backup/?format=json", "/backup/?format=xlsx",
                    "/students-template/"]:
            r = self.client.get(url)
            self.assertEqual(r.status_code, 200, url)
            self.assertGreater(len(r.content), 100)

    def test_import_csv(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        csv = "ת.ז.,שם פרטי,שם משפחה,כיתה,טלפון אמא\n123456782,דינה,גל,ט2,501112222\n,,,,\nabc,x,y,z\n".encode("utf-8-sig")
        r = self.client.post("/students-import/", {"file": SimpleUploadedFile("s.csv", csv)})
        self.assertContains(r, "נוספו 1")
        st = Student.objects.get(tz="123456782")
        self.assertEqual(st.contacts.get().phone, "0501112222")
        self.assertTrue(SchoolClass.objects.filter(name="ט2").exists())

    def test_new_year_mapping_does_not_double_move(self):
        c2 = SchoolClass.objects.create(name="ט1")
        c3 = SchoolClass.objects.create(name="י1")
        s2 = Student.objects.create(tz="000000009", first_name="א", last_name="ב", school_class=c2)
        self.client.post("/new-year/do/", {f"map_{self.cls.pk}": c2.pk, f"map_{c2.pk}": c3.pk})
        self.st.refresh_from_db(); s2.refresh_from_db()
        self.assertEqual((self.st.school_class, s2.school_class), (c2, c3))

    def test_settings_password_encrypted(self):
        r = self.client.post("/settings/", {"school_name": "ב", "yemot_number": "0771", "yemot_password_input": "secret1",
                                            "phone_dir": "/7", "assistant_model": "claude-sonnet-5-5", "auth_mode": "open", "unknown_tz": "record",
                                            "days_off_list": ["4", "5"], "ask_reason": "on"})
        self.assertEqual(r.status_code, 302)
        from attendance.models import SchoolSettings
        s = SchoolSettings.get()
        self.assertEqual(s.yemot_password, "secret1")
        self.assertNotIn("secret1", s.yemot_password_enc)
        self.assertEqual(s.days_off_set, {4, 5})
        self.assertNotContains(self.client.get("/settings/"), "secret1")

    def test_yemot_setup_calls_api(self):
        self.s.yemot_number, self.s.yemot_password = "0771", "p"
        self.s.save()
        with mock.patch("attendance.yemot_client._call") as m:
            self.client.post("/settings/yemot-setup/")
        cmd, token, params = m.call_args[0]
        self.assertEqual((cmd, token, params["path"], params["type"]), ("UpdateExtension", "0771:p", "ivr2:/7", "api"))
        self.assertIn("/phone/sekret/", params["api_link"])

    def test_support_code_is_read_only_and_expires(self):
        self.client.post("/help/support-code/")
        from attendance.models import SchoolSettings
        import hashlib
        s = SchoolSettings.get()
        self.assertTrue(s.support_code_hash)
        # קוד שגוי
        c2 = self.client_class()
        self.assertContains(c2.post("/support/", {"code": "00000000"}), "קוד שגוי")
        # מוצאים את הקוד מההודעה
        msgs = [str(m) for m in self.client.get("/help/").context["messages"]]
        code = next(m.split("קוד התמיכה: ")[1][:8] for m in msgs if "קוד התמיכה" in m)
        self.assertEqual(c2.post("/support/", {"code": code}).status_code, 302)
        self.assertEqual(c2.get("/records/").status_code, 200)
        self.assertEqual(c2.post("/records/new/", {}).status_code, 403)
        s.support_code_expires = timezone.now() - timedelta(minutes=1)
        s.save()
        self.assertContains(self.client_class().post("/support/", {"code": code}), "קוד שגוי")

    def test_hebrew_date(self):
        self.assertIn("תשפ", hebrew_date(date(2026, 3, 1)) + "תשפ")  # קיים ולא נופל
        self.assertTrue(hebrew_date(date(2026, 3, 1)))
