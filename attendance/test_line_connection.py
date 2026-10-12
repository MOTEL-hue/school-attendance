from unittest import mock
import urllib.error

from django.test import SimpleTestCase, override_settings

from . import yemot_client
from .forms import LineConnectionForm
from .models import SchoolSettings


class LineConnectionFormTests(SimpleTestCase):
    def test_extension_path_gets_leading_slash(self):
        form = LineConnectionForm(data={"phone_dir": "7"})
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["phone_dir"], "/7")

    def test_existing_slash_is_preserved(self):
        form = LineConnectionForm(data={"phone_dir": "/7"})
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["phone_dir"], "/7")

    def test_empty_extension_path_is_allowed(self):
        form = LineConnectionForm(data={"phone_dir": ""})
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["phone_dir"], "")

    def test_path_traversal_and_url_suffixes_are_rejected(self):
        for path in ("../7", "/7/../8", "/7?x=1", "/7#fragment", r"/7\\8", "///"):
            with self.subTest(path=path):
                form = LineConnectionForm(data={"phone_dir": path})
                self.assertFalse(form.is_valid())
                self.assertIn("phone_dir", form.errors)


@override_settings(SECRET_KEY="test-key-for-yemot-token-tests")
class YemotTokenCompatibilityTests(SimpleTestCase):
    def test_raw_api_key_is_used_when_system_number_is_empty(self):
        settings = SchoolSettings(yemot_number="")
        settings.yemot_password = "api-token-example"
        self.assertEqual(settings.yemot_token, "api-token-example")

    def test_legacy_system_number_and_password_format_is_preserved(self):
        settings = SchoolSettings(yemot_number="123456")
        settings.yemot_password = "legacy-password"
        self.assertEqual(settings.yemot_token, "123456:legacy-password")


class YemotClientSecurityTests(SimpleTestCase):
    def test_network_error_does_not_expose_api_token(self):
        token = "sensitive-api-token"
        error = urllib.error.URLError(
            f"https://www.call2all.co.il/ym/api/GetSession?token={token}"
        )
        with mock.patch("attendance.yemot_client.urllib.request.urlopen", side_effect=error):
            with self.assertRaises(yemot_client.YemotError) as raised:
                yemot_client._call("GetSession", token, {})
        self.assertNotIn(token, str(raised.exception))
        self.assertIn("בדקו את החיבור", str(raised.exception))
