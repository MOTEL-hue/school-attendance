from datetime import timedelta
from unittest import mock

from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import Client, TestCase
from django.utils import timezone

from attendance import tenant
from attendance.calendar_utils import today_il
from attendance.models import (Alert, AlertRule, Attendance, AuthorizedCaller, Contact, School, SchoolClass,
                               SchoolSettings, Student, hash_pin)
from attendance.phone import handle
from attendance.services import create_record
from attendance.views_public import create_school, delete_school

from .base import Base, make_school


def in_school(school):
    """הקשר בית ספר זמני בתוך בדיקה."""
    class Ctx:
        def __enter__(self):
            self.t = tenant.activate(school)

        def __exit__(self, *a):
            tenant.deactivate(self.t)
    return Ctx()


class TwoSchools(Base):
    def setUp(self):
        super().setUp()
        self.other = make_school("בית ספר ב", secret="other-secret", email="b@x.com")
        with in_school(self.other):
            self.cls_b = SchoolClass.objects.create(name="ח1")  # אותו שם כיתה: מותר בבית ספר אחר
            self.st_b = Student.objects.create(tz="012345678", first_name="דינה", last_name="לוי",
                                               school_class=self.cls_b)  # אותה ת.ז.: מותר
            create_record(student=self.st_b, kind="absent", date=today_il())

    def test_queries_are_scoped(self):
        self.assertEqual(Student.objects.get().first_name, "שרה")
        self.assertEqual(Attendance.objects.count(), 0)
        with in_school(self.other):
            self.assertEqual(Student.objects.get().first_name, "דינה")
            self.assertEqual(Attendance.objects.count(), 1)
        self.assertEqual(Student.all_objects.count(), 2)

    def test_no_school_context_sees_nothing_and_cannot_save(self):
        token = tenant.activate(None)
        try:
            self.assertEqual(Student.objects.count(), 0)
            with self.assertRaises(RuntimeError):
                SchoolClass.objects.create(name="x")
        finally:
            tenant.deactivate(token)

    def test_web_user_cannot_see_other_school(self):
        c = Client()
        c.login(username="a@x.com", password="pw")
        body = c.get("/students/").content.decode()
        self.assertIn("שרה", body)
        self.assertNotIn("דינה", body)
        self.assertEqual(c.get(f"/students/{self.st_b.pk}/").status_code, 404)
        self.assertEqual(c.post(f"/records/{Attendance.all_objects.get(school=self.other).pk}/delete/").status_code, 404)
        self.assertTrue(Attendance.all_objects.filter(school=self.other).exists())

    def test_phone_secret_selects_school(self):
        c = Client()
        # באותה ת.ז. בשני בתי הספר: כל אחד רואה את התלמידה שלו
        r = c.get("/phone/other-secret/", {"ApiPhone": "0521111111", "ApiCallId": "1", "tz1_1": "012345678"})
        self.assertIn("=k1,", r.content.decode())
        r = c.get("/phone/other-secret/", {"ApiPhone": "0521111111", "ApiCallId": "1", "tz1_1": "012345678", "k1": "1"})
        with in_school(self.other):
            self.assertEqual(Attendance.objects.filter(kind="late_school").count(), 1)
        self.assertEqual(Attendance.objects.filter(kind="late_school").count(), 0)
        self.assertEqual(c.get("/phone/nope/").status_code, 404)

    def test_pin_and_phone_auth_are_per_school(self):
        with in_school(self.other):
            SchoolSettings.get().__class__.objects.update(auth_mode="phone")
            AuthorizedCaller.objects.create(name="ב", phone="0549999999")
        self.s.auth_mode = "phone"
        self.s.save()
        # המזכירה של בית ספר א' לא מורשית בבית ספר ב'
        out = handle({"ApiPhone": "0527000000", "ApiCallId": "x"})
        self.assertIn("=tz1_1,", out)
        with in_school(self.other):
            self.assertIn("go_to_folder=hangup", handle({"ApiPhone": "0527000000", "ApiCallId": "x"}))

    def test_alerts_and_rules_scoped(self):
        with in_school(self.other):
            AlertRule.objects.create(name="כלל ב", metric="absent_any", threshold=1)
            create_record(student=self.st_b, kind="absent", date=today_il() - timedelta(days=1))
            self.assertEqual(Alert.objects.count(), 1)
        self.assertEqual(Alert.objects.count(), 0)
        self.assertEqual(AlertRule.objects.count(), 0)

    def test_duplicate_tz_in_same_school_rejected_by_form(self):
        c = Client()
        c.login(username="a@x.com", password="pw")
        r = c.post("/students/new/", {"tz": "012345678", "first_name": "x", "last_name": "y",
                                      "school_class": self.cls.pk, "active": "on"})
        self.assertContains(r, "כבר קיים")
        self.assertEqual(Student.objects.count(), 1)

    def test_import_goes_to_current_school_only(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        c = Client()
        c.login(username="b@x.com", password="pw")
        csv = "ת.ז.,שם פרטי,שם משפחה,כיתה\n123456782,נועה,גל,ט2\n".encode("utf-8-sig")
        c.post("/students-import/", {"file": SimpleUploadedFile("s.csv", csv)})
        self.assertEqual(Student.objects.count(), 1)  # בית ספר א' לא השתנה
        with in_school(self.other):
            self.assertEqual(Student.objects.count(), 2)

    def test_support_code_works_only_for_its_school(self):
        c = Client()
        c.login(username="a@x.com", password="pw")
        c.post("/help/support-code/")
        msgs = [str(m) for m in c.get("/help/").context["messages"]]
        code = next(m.split("קוד התמיכה: ")[1].split(" ")[0] for m in msgs if "קוד התמיכה" in m)
        digits = code.split("-")[1]
        s = Client()
        self.assertEqual(s.post("/support/", {"code": f"{self.other.pk}-{digits}"}).status_code, 200)  # בית ספר אחר: נדחה
        self.assertEqual(s.post("/support/", {"code": code}).status_code, 302)
        body = s.get("/students/").content.decode()
        self.assertIn("שרה", body)
        self.assertNotIn("דינה", body)
        self.assertEqual(s.post("/records/new/", {}).status_code, 403)


class Lifecycle(TestCase):
    def setUp(self):
        cache.clear()

    def test_register_pending_then_approved(self):
        c = Client()
        r = c.post("/register/", {"school_name": "סמינר חדש", "contact_name": "רחל", "contact_phone": "050",
                                  "email": "New@Test.com", "password": "Str0ng-pass!", "password2": "Str0ng-pass!",
                                  "accept": "on"})
        self.assertContains(r, "הבקשה התקבלה")
        school = School.objects.get(name="סמינר חדש")
        self.assertEqual(school.status, "pending")
        self.assertTrue(c.login(username="new@test.com", password="Str0ng-pass!"))
        self.assertContains(c.get("/"), "ממתינים לאישור", status_code=403)
        # הטלפון חסום עד האישור
        self.assertIn("go_to_folder=hangup", c.get(f"/phone/{school.phone_secret}/").content.decode())
        owner = User.objects.create_superuser("admin", password="ownerpass1")
        oc = Client()
        oc.login(username="admin", password="ownerpass1")
        self.assertContains(oc.get("/platform/"), "סמינר חדש")
        oc.post(f"/platform/{school.pk}/approve/")
        self.assertEqual(c.get("/").status_code, 200)
        self.assertEqual(School.objects.get(pk=school.pk).status, "active")
        # השעיה חוסמת גם את האתר
        oc.post(f"/platform/{school.pk}/suspend/")
        self.assertEqual(c.get("/").status_code, 403)
        self.assertIn("go_to_folder=hangup", c.get(f"/phone/{school.phone_secret}/").content.decode())

    def test_register_validation(self):
        c = Client()
        base = {"school_name": "ב", "contact_name": "ר", "contact_phone": "1", "email": "x@y.com",
                "password": "Str0ng-pass!", "password2": "Str0ng-pass!", "accept": "on"}
        self.assertContains(c.post("/register/", {**base, "password": "123", "password2": "123"}), "err")
        self.assertContains(c.post("/register/", {**base, "password2": "other"}), "לא זהות")
        self.assertContains(c.post("/register/", {k: v for k, v in base.items() if k != "accept"}), "לאשר את התנאים")
        self.assertEqual(School.objects.count(), 0)
        c.post("/register/", base)
        self.assertContains(c.post("/register/", base), "כבר רשומה")

    def test_register_rate_limited(self):
        c = Client()
        for i in range(6):
            c.post("/register/", {"school_name": f"s{i}", "contact_name": "ר", "contact_phone": "1",
                                  "email": f"u{i}@y.com", "password": "Str0ng-pass!", "password2": "Str0ng-pass!",
                                  "accept": "on"})
        self.assertEqual(School.objects.count(), 5)

    def test_login_throttled_after_failures(self):
        make_school()
        c = Client()
        for _ in range(6):
            c.post("/login/", {"username": "a@x.com", "password": "wrong"})
        r = c.post("/login/", {"username": "a@x.com", "password": "pw"})
        self.assertContains(r, "יותר מדי ניסיונות")
        cache.clear()
        self.assertEqual(c.post("/login/", {"username": "a@x.com", "password": "pw"}).status_code, 302)

    def test_platform_is_owner_only(self):
        make_school()
        c = Client()
        c.login(username="a@x.com", password="pw")
        self.assertEqual(c.get("/platform/").status_code, 403)
        self.assertEqual(c.post("/platform/1/approve/").status_code, 403)
        self.assertEqual(Client().get("/platform/").status_code, 302)

    def test_owner_without_school_is_redirected_and_can_enter(self):
        s = make_school()
        User.objects.create_superuser("admin", password="ownerpass1")
        oc = Client()
        oc.login(username="admin", password="ownerpass1")
        self.assertEqual(oc.get("/students/").status_code, 302)
        oc.post(f"/platform/{s.pk}/enter/")
        self.assertEqual(oc.get("/students/").status_code, 200)
        oc.post("/platform/exit/")
        self.assertEqual(oc.get("/students/").status_code, 302)

    def test_owner_creates_active_school(self):
        User.objects.create_superuser("admin", password="ownerpass1")
        oc = Client()
        oc.login(username="admin", password="ownerpass1")
        oc.post("/platform/create/", {"school_name": "ישיר", "email": "d@x.com"})
        s = School.objects.get(name="ישיר")
        self.assertEqual(s.status, "active")
        self.assertTrue(User.objects.filter(username="d@x.com").exists())

    def test_school_can_delete_itself_only_with_exact_name(self):
        s = make_school("למחיקה")
        other = make_school("נשאר", secret="k", email="o@x.com")
        with in_school(s):
            cls = SchoolClass.objects.create(name="א")
            st = Student.objects.create(tz="000000001", first_name="א", last_name="ב", school_class=cls)
            create_record(student=st, kind="absent", date=today_il())
        with in_school(other):
            SchoolClass.objects.create(name="ב")
        c = Client()
        c.login(username="a@x.com", password="pw")
        c.post("/settings/delete-school/", {"confirm": "שם שגוי"})
        self.assertTrue(School.objects.filter(pk=s.pk).exists())
        r = c.post("/settings/delete-school/", {"confirm": "למחיקה"})
        self.assertContains(r, "הנתונים נמחקו")
        self.assertFalse(School.objects.filter(pk=s.pk).exists())
        self.assertFalse(User.objects.filter(username="a@x.com").exists())
        self.assertEqual(Student.all_objects.count(), 0)
        self.assertEqual(SchoolClass.all_objects.count(), 1)  # בית הספר האחר לא נפגע

    def test_secret_rotation_invalidates_old_url(self):
        s = make_school()
        old = s.phone_secret
        c = Client()
        c.login(username="a@x.com", password="pw")
        c.post("/settings/rotate-secret/")
        s.refresh_from_db()
        self.assertNotEqual(s.phone_secret, old)
        self.assertEqual(c.get(f"/phone/{old}/").status_code, 404)
        self.assertEqual(c.get(f"/phone/{s.phone_secret}/").status_code, 200)

    def test_pins_are_hashed(self):
        s = make_school()
        c = Client()
        c.login(username="a@x.com", password="pw")
        c.post("/manage/callers/new/", {"name": "מזכירה", "phone": "0521234567", "pin_input": "4321", "active": "on"})
        with in_school(s):
            ac = AuthorizedCaller.objects.get()
        self.assertEqual(ac.pin, hash_pin("4321"))
        self.assertNotIn("4321", ac.pin)
        self.assertNotContains(c.get(f"/manage/callers/{ac.pk}/"), "4321")
        c.post(f"/manage/callers/{ac.pk}/", {"name": "מזכירה", "phone": "0521234567", "pin_input": "", "active": "on"})
        with in_school(s):
            self.assertEqual(AuthorizedCaller.objects.get().pin, hash_pin("4321"))  # ריק = לא משנים

    def test_public_pages_and_healthz(self):
        c = Client()
        for url in ["/login/", "/register/", "/terms/", "/privacy/", "/healthz"]:
            self.assertEqual(c.get(url).status_code, 200, url)
        self.assertContains(c.get("/privacy/"), "טיוטה")

    def test_onboarding_steps_progress(self):
        s = make_school()
        c = Client()
        c.login(username="a@x.com", password="pw")
        self.assertContains(c.get("/"), "נשארו 4 מתוך 4")
        with in_school(s):
            st = SchoolSettings.get()
            st.yemot_number, st.yemot_password, st.phone_dir, st.extension_done = "077", "p", "/7", True
            st.save()
            cls = SchoolClass.objects.create(name="א")
            student = Student.objects.create(tz="000000001", first_name="א", last_name="ב", school_class=cls)
            create_record(student=student, kind="late_school", date=today_il(), source="phone")
        self.assertNotContains(c.get("/"), "ההתקנה עוד לא הושלמה")
