from datetime import date

from django.test import TestCase

from attendance.models import AuthorizedCaller, Contact, SchoolClass, SchoolSettings, Student


class Base(TestCase):
    def setUp(self):
        self.cls = SchoolClass.objects.create(name="ח1")
        self.st = Student.objects.create(tz="012345678", first_name="שרה", last_name="כהן", school_class=self.cls,
                                         name_recorded=True)
        self.s = SchoolSettings.get()
        self.s.phone_dir = "/7"
        self.s.save()
        Contact.objects.create(student=self.st, label="אמא", phone="0501234567", pin="1234")
        AuthorizedCaller.objects.create(name="מזכירה", phone="0527000000", pin="9999")
