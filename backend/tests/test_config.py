"""Config tests: .env parsing and secret-safe status views."""
import os
import tempfile
import unittest

from config import load_dotenv, load_settings


class DotenvTest(unittest.TestCase):
    def test_parses_values_and_ignores_comments(self):
        with tempfile.NamedTemporaryFile(
            "w", suffix=".env", delete=False, encoding="utf-8"
        ) as handle:
            handle.write("# comment\nMETA_API_KEY='LLM_1_x'\nEMPTY=\n")
            path = handle.name
        try:
            values = load_dotenv(path)
        finally:
            os.unlink(path)
        self.assertEqual(values["META_API_KEY"], "LLM_1_x")
        self.assertEqual(values["EMPTY"], "")

    def test_missing_file_returns_empty(self):
        self.assertEqual(load_dotenv("/nonexistent-23editor.env"), {})

    def test_redacted_view_never_contains_key(self):
        settings = load_settings()
        view = settings.redacted()
        self.assertNotIn("api_key", view)
        blob = repr(view)
        if settings.api_key:
            self.assertNotIn(settings.api_key, blob)


if __name__ == "__main__":
    unittest.main()
