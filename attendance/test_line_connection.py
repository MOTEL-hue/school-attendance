from django.test import SimpleTestCase

from .forms import LineConnectionForm


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
