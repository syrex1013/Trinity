import contextlib
import io
import json
import unittest
import urllib.error

import gemini_key_tester as tester


class _FakeResponse:
    def __init__(self, status, body):
        self.status, self._body = status, body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class GeminiKeyTesterTests(unittest.TestCase):
    def setUp(self):
        self._urlopen = tester.urllib.request.urlopen

    def tearDown(self):
        tester.urllib.request.urlopen = self._urlopen

    @staticmethod
    def _http_error(status, body):
        return urllib.error.HTTPError(
            "https://generativelanguage.googleapis.com", status, "err", {}, io.BytesIO(body.encode())
        )

    def _serve(self, payload, status=200):
        if status == 200:
            def fake(req, timeout=None):
                return _FakeResponse(status, json.dumps(payload).encode())
        else:
            def fake(req, timeout=None):
                raise self._http_error(status, json.dumps(payload))
        tester.urllib.request.urlopen = fake

    def test_api_disabled_in_project_reads_as_live_key(self):
        # the apk-typical denial: Google names the project, so the key itself is live
        self._serve({"error": {"code": 403, "status": "PERMISSION_DENIED", "message":
            "Gemini API has not been used in project 318182098261 before or it is disabled. "
            "Enable it by visiting https://console.developers.google.com/apis/api/"
            "generativelanguage.googleapis.com/overview?project=318182098261 then retry. "
            "If you enabled this API recently, wait a few minutes for the action to propagate "
            "to our systems and retry."}}, status=403)
        result = tester.probe("AIzaSyX", "hi", "gemini-2.0-flash")
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "api_disabled_in_project")
        self.assertTrue(result["key_live"])
        self.assertEqual(result["project"], "318182098261")
        self.assertEqual(
            result["enable_url"],
            "https://console.developers.google.com/apis/api/"
            "generativelanguage.googleapis.com/overview?project=318182098261",
        )

    def test_service_blocked_key_reads_as_live_key(self):
        # the Android-key-typical denial: the key authenticated and resolved
        # to a project, but its API restrictions block Generative Language
        self._serve({"error": {"code": 403, "status": "PERMISSION_DENIED", "message":
            "Requests to this API generativelanguage.googleapis.com method "
            "google.ai.generativelanguage.v1beta.GenerativeService.GenerateContent are blocked.",
            "details": [
                {"@type": "type.googleapis.com/google.rpc.ErrorInfo", "reason": "API_KEY_SERVICE_BLOCKED",
                 "domain": "googleapis.com",
                 "metadata": {"service": "generativelanguage.googleapis.com",
                              "consumer": "projects/318182098261"}},
                {"@type": "type.googleapis.com/google.rpc.LocalizedMessage", "locale": "en-US",
                 "message": "Requests to this API generativelanguage.googleapis.com method "
                            "google.ai.generativelanguage.v1beta.GenerativeService.GenerateContent are blocked."},
            ]}}, status=403)
        result = tester.probe("AIzaSyX", "hi", "gemini-2.0-flash")
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "key_restricted_from_service")
        self.assertTrue(result["key_live"])
        self.assertEqual(result["project"], "318182098261")
        self.assertNotIn("enable_url", result)

    def test_dead_key_detected_from_google_400(self):
        # Google rejects a bad key with 400, not 401/403
        self._serve({"error": {"code": 400, "status": "INVALID_ARGUMENT", "message":
            "API key not valid. Please pass a valid API key."}}, status=400)
        result = tester.probe("AIzaSyX", "hi", "gemini-2.0-flash")
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "invalid_key")
        self.assertNotIn("key_live", result)

    def test_other_permission_denials_stay_restricted(self):
        self._serve({"error": {"code": 403, "status": "PERMISSION_DENIED", "message":
            "The caller does not have permission"}}, status=403)
        result = tester.probe("AIzaSyX", "hi", "gemini-2.0-flash")
        self.assertEqual(result["reason"], "key_blocked_or_restricted")
        self.assertNotIn("key_live", result)

    def test_rate_limit_and_model_not_found_keep_their_reasons(self):
        self._serve({"error": {"code": 429, "message": "Resource exhausted"}}, status=429)
        self.assertEqual(tester.probe("AIzaSyX", "hi", "gemini-2.0-flash")["reason"], "rate_limited")
        self._serve({"error": {"code": 404, "message": "model not found"}}, status=404)
        self.assertEqual(tester.probe("AIzaSyX", "hi", "gemini-2.0-flash")["reason"], "model_not_found")

    def test_working_key_extracts_reply(self):
        self._serve({"candidates": [{"content": {"parts": [{"text": " Gemini key works."}]}}]})
        result = tester.probe("AIzaSyX", "hi", "gemini-2.0-flash")
        self.assertTrue(result["ok"])
        self.assertEqual(result["text"], "Gemini key works.")

    def test_exit_codes_separate_working_live_and_unusable(self):
        original = tester.probe
        try:
            cases = [
                ({"ok": True, "status": 200, "model": "m", "text": "hi"}, 0),
                ({"ok": False, "status": 403, "model": "m", "reason": "api_disabled_in_project",
                  "key_live": True, "project": "1", "enable_url": "https://x", "error": {}}, 2),
                ({"ok": False, "status": 403, "model": "m", "reason": "key_restricted_from_service",
                  "key_live": True, "project": "1", "error": {}}, 2),
                ({"ok": False, "status": 400, "model": "m", "reason": "invalid_key", "error": {}}, 1),
            ]
            for outcome, code in cases:
                tester.probe = lambda key, prompt, model, timeout=20.0, _o=outcome: _o
                with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                    self.assertEqual(tester.main(["AIzaX"]), code)
        finally:
            tester.probe = original


if __name__ == "__main__":
    unittest.main()
