from django.contrib.auth.models import User
from django.test import TestCase

from attendance import tenant
from attendance.models import AuthorizedCaller, Contact, School, SchoolClass, SchoolSettings, SchoolUser, Student


def make_school(name="בית ספר א", secret="sekret", status="active", email="a@x.com"):
    school = School.objects.create(name=name, status=status, phone_secret=secret)
    SchoolSettings.all_objects.create(school=school, school_name=name)
    user = User.objects.create_user(email, password="pw")
    SchoolUser.objects.create(user=user, school=school)
    return school


class Base(TestCase):
    """בית ספר אחד פעיל, עם כיתה, תלמידה, הורה ומורשית. הקשר הבית-ספר פעיל לכל הבדיקה."""

    def setUp(self):
        self.school = make_school()
        token = tenant.activate(self.school)
        self.addCleanup(tenant.deactivate, token)
        self.cls = SchoolClass.objects.create(name="ח1")
        self.st = Student.objects.create(tz="012345678", first_name="שרה", last_name="כהן", school_class=self.cls,
                                         name_recorded=True)
        self.s = SchoolSettings.get()
        self.s.phone_dir = "/7"
        self.s.save()
        mom = Contact(student=self.st, label="אמא", phone="0501234567")
        mom.set_pin("1234")
        mom.save()
        sec = AuthorizedCaller(name="מזכירה", phone="0527000000")
        sec.set_pin("9999")
        sec.save()
