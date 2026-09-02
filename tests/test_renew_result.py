import importlib.util
import sys
import types
import unittest
from pathlib import Path

# app.py imports SeleniumBase at module load; the result-classification tests
# exercise only pure functions, so a lightweight stub is sufficient.
seleniumbase = types.ModuleType("seleniumbase")
seleniumbase.SB = object
sys.modules.setdefault("seleniumbase", seleniumbase)

spec = importlib.util.spec_from_file_location("katabump_app", Path(__file__).parents[1] / "app.py")
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)

STATIC_WARNING = (
    "Warning: changing the server type will reset the startup command and "
    "environment variables to the new type's defaults. Your files will not be affected."
)


class RenewClassificationTests(unittest.TestCase):
    def test_static_server_type_warning_is_not_success_or_cooldown(self):
        status, detail = app._classify_renew(STATIC_WARNING, STATIC_WARNING, "")
        self.assertEqual(app.RENEW_UNCONFIRMED, status)
        self.assertIn("静态", detail)

    def test_expiration_date_advancing_confirms_success(self):
        before = "Server information\nExpiration date: 2026-09-05"
        after = STATIC_WARNING + "\nExpiration date: 2026-10-05"
        status, detail = app._classify_renew(STATIC_WARNING, after, before)
        self.assertEqual(app.RENEW_PASS, status)
        self.assertIn("2026-09-05", detail)
        self.assertIn("2026-10-05", detail)

    def test_new_cooldown_message_after_submission_confirms_success(self):
        before = "Server information\nRenew server"
        after = STATIC_WARNING + "\nYou can't renew this server until October 1, 2026"
        status, detail = app._classify_renew(STATIC_WARNING, after, before)
        self.assertEqual(app.RENEW_PASS, status)
        self.assertIn("冷却", detail)

    def test_existing_cooldown_remains_cooldown(self):
        text = "You can't renew this server until October 1, 2026"
        status, _ = app._classify_renew(text, text, text)
        self.assertEqual(app.RENEW_COOLDOWN, status)

    def test_explicit_success_message_confirms_success(self):
        status, _ = app._classify_renew("Server has been renewed successfully", "", "")
        self.assertEqual(app.RENEW_PASS, status)

    def test_unlabelled_dates_near_renew_button_are_not_expiry_proof(self):
        before = "Renew server\nLast login: 2026-09-01"
        after = STATIC_WARNING + "\nRenew server\nLast login: 2026-09-02"
        status, _ = app._classify_renew(STATIC_WARNING, after, before)
        self.assertEqual(app.RENEW_UNCONFIRMED, status)


if __name__ == "__main__":
    unittest.main()
