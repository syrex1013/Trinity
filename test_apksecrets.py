import importlib.machinery
import os
import stat
import tempfile
import time
import unittest
import zipfile
from pathlib import Path

import json
import types

apksecrets = importlib.machinery.SourceFileLoader("apksecrets", "apksecrets").load_module()

class ApkSecretsTests(unittest.TestCase):
    def test_fdroid_selects_latest_and_unpack_rejects_escape(self):
        rows = apksecrets.fdroid_releases({"packages": {"x": [{"versionCode": 1, "apkName": "x-1.apk"}, {"versionCode": 2, "apkName": "x-2.apk"}]}})
        self.assertEqual(rows[0][2], "https://f-droid.org/repo/x-2.apk")
        with tempfile.TemporaryDirectory() as d:
            apk = Path(d) / "bad.apk"
            with zipfile.ZipFile(apk, "w") as z: z.writestr("../outside", "no")
            with self.assertRaises(apksecrets.Error): apksecrets.safe_unpack(apk, Path(d) / "out", 100)

    def test_fdroid_keywords_match_app_names(self):
        index = {
            "packages": {
                "org.molly": [{"versionCode": 1, "apkName": "org.molly-1.apk"}],
                "com.other": [{"versionCode": 1, "apkName": "com.other-1.apk"}],
            },
            "apps": [
                {"packageName": "org.molly", "localized": {"en-US": {"name": "Signal (fork)"}}},
                {"packageName": "com.other", "localized": {"en-US": {"name": "Something Else"}}},
            ],
        }
        releases = apksecrets.fdroid_releases(index)
        matches = [r for r in releases if any(k in r[0].lower() or k in r[3] for k in ["signal"])]
        self.assertEqual([r[0] for r in matches], ["org.molly"])

    def test_fdroid_repeats_do_not_requeue_known_urls(self):
        data = {"packages": {"x": [{"versionCode": 1, "apkName": "x-1.apk"}],
                             "y": [{"versionCode": 1, "apkName": "y-1.apk"}]}}
        with tempfile.TemporaryDirectory() as d:
            store = apksecrets.Store(Path(d) / "state")
            try:
                args = types.SimpleNamespace(source=["fdroid"], package=[], keyword=[],
                                             latest_artifact_limit=0, random=False)
                class FakeFetcher:
                    def get(self, url): return json.dumps(data).encode()
                run = lambda: list(apksecrets.discover(store, FakeFetcher(), args))
                run()
                self.assertEqual(len(store.jobs()), 2)
                run()
                self.assertEqual(len(store.jobs()), 2)
                data["packages"]["z"] = [{"versionCode": 1, "apkName": "z-1.apk"}]
                run()
                self.assertEqual(len(store.jobs()), 3)
            finally: store.close()

    def test_aptoide_queues_every_matching_release(self):
        data = {"datalist": {"list": [
            {"package": "app.one", "vername": "9.9-top", "file": {"vername": "2.0", "path": "https://x/app.one-2.apk"}},
            {"package": "app.two", "vername": "1.0", "file": {"path": "https://x/app.two-1.apk"}},
            {"package": "app.three", "vername": "1.0", "file": {}},
        ]}}
        releases = apksecrets.aptoide_releases(data)
        self.assertEqual(len(releases), 2)
        with tempfile.TemporaryDirectory() as d:
            store = apksecrets.Store(Path(d) / "state")
            try:
                args = types.SimpleNamespace(source=["aptoide"], package=[], keyword=["app"],
                                             latest_artifact_limit=0, random=False)
                class FakeFetcher:
                    def get(self, url): return json.dumps(data).encode()
                list(apksecrets.discover(store, FakeFetcher(), args))
                queued = {row["package"] for row in store.jobs()}
                self.assertEqual(queued, {"app.one", "app.two"})
                self.assertEqual(store.jobs()[0]["version"], "2.0")
            finally: store.close()

    def test_aptoide_keyword_matches_app_name_and_limit_caps_queued(self):
        pages = [
            {"datalist": {"list": [
                {"package": "a.one", "name": "Payment Store", "file": {"path": "https://x/a.one-1.apk"}},
                {"package": "a.two", "name": "OfficeSuite", "file": {"path": "https://x/a.two-1.apk"}},
            ], "next": 2}},
            {"datalist": {"list": [
                {"package": "a.three", "name": "Bill Payment", "file": {"path": "https://x/a.three-1.apk"}},
                {"package": "a.four", "name": "Payment Hub", "file": {"path": "https://x/a.four-1.apk"}},
            ]}},
        ]
        class FakeFetcher:
            def get(self, url): return json.dumps(pages[1] if "offset" in url else pages[0]).encode()
        with tempfile.TemporaryDirectory() as d:
            store = apksecrets.Store(Path(d) / "state")
            try:
                args = types.SimpleNamespace(source=["aptoide"], package=[], keyword=["payment"],
                                             latest_artifact_limit=2, random=False)
                list(apksecrets.discover(store, FakeFetcher(), args))
                self.assertEqual({row["package"] for row in store.jobs()}, {"a.one", "a.three"})
            finally: store.close()

    def test_aptoide_limit_is_shared_across_keywords(self):
        data = {"datalist": {"list": [
            {"package": "a.one", "name": "Payment Store", "file": {"path": "https://x/a.one-1.apk"}},
            {"package": "a.two", "name": "Wallet Hub", "file": {"path": "https://x/a.two-1.apk"}},
        ]}}
        class FakeFetcher:
            def get(self, url): return json.dumps(data).encode()
        with tempfile.TemporaryDirectory() as d:
            store = apksecrets.Store(Path(d) / "state")
            try:
                args = types.SimpleNamespace(source=["aptoide"], package=[], keyword=["payment", "wallet"],
                                             latest_artifact_limit=1, random=False)
                list(apksecrets.discover(store, FakeFetcher(), args))
                self.assertEqual([row["package"] for row in store.jobs()], ["a.one"])
            finally: store.close()

    def test_cli_packages_and_keywords_split_on_commas(self):
        args = apksecrets.parser().parse_args(["run", "--keyword", "payment, wallet", "--keyword", "bank",
                                               "--package", "com.a,com.b"])
        self.assertEqual(args.keyword, ["payment", "wallet", "bank"])
        self.assertEqual(args.package, ["com.a", "com.b"])

    def test_apkcombo_parsers(self):
        page = '<a href="/whatsapp/com.whatsapp/">WhatsApp</a> <a href="/search?q=1">x</a> <a href="../wechat/com.tencent.mm/">WeChat</a>'
        apps = apksecrets.apkcombo_apps(page, "https://apkcombo.com/search/whatsapp")
        self.assertEqual(apps, [("com.whatsapp", "https://apkcombo.com/whatsapp/com.whatsapp/"), ("com.tencent.mm", "https://apkcombo.com/wechat/com.tencent.mm/")])
        variant = '<a href="/r2?u=https%3A%2F%2Fcdn.example.com%2Fapp%2F2.0%2Ffile.apks%3Fx%3D1">dl</a>'
        class FakeFetcher:
            def get(self, url):
                assert "/download/" in url, url
                return (variant if url.endswith("download/apk") else "ver /download/phone-2.0.1-apk").encode()
        self.assertEqual(apksecrets.apkcombo_release(FakeFetcher(), "https://apkcombo.com/whatsapp/com.whatsapp/"),
                         ("https://cdn.example.com/app/2.0/file.apks?x=1", "2.0.1"))

    def test_discovery_failure_isolated_per_source(self):
        with tempfile.TemporaryDirectory() as d:
            store = apksecrets.Store(Path(d) / "state")
            try:
                data = {"packages": {"x": [{"versionCode": 1, "apkName": "x-1.apk"}]}}
                class FakeFetcher:
                    def get(self, url):
                        if "apkcombo" in url: raise apksecrets.Error("blocked")
                        return json.dumps(data).encode()
                args = types.SimpleNamespace(source=["fdroid", "apkcombo"], package=[], keyword=[],
                                             latest_artifact_limit=0, random=False)
                list(apksecrets.discover(store, FakeFetcher(), args))
                self.assertEqual(len(store.jobs()), 1)  # fdroid release queued, apkcombo failure skipped
            finally: store.close()

    def test_verify_secret_uses_expected_urls(self):
        class FakeProbe:
            def __init__(self): self.urls = []
            def __call__(self, url, headers=None): self.urls.append(url); return 200, b'{"ok": true}'
        probe = FakeProbe(); original = apksecrets._probe; apksecrets._probe = probe
        try:
            self.assertIs(apksecrets.verify_secret("TelegramBotToken", "123:abc"), True)
            self.assertIs(apksecrets.verify_secret("slack-webhook", "xoxb-1"), True)
            self.assertIs(apksecrets.verify_secret("Box", "D08A4F1810F34A82B6B9"), True)
            self.assertIn("api.telegram.org", probe.urls[0]); self.assertIn("slack.com", probe.urls[1])
            self.assertIn("api.box.com", probe.urls[2])
        finally:
            apksecrets._probe = original

    def _fake_probe(self, routes):
        def probe(url, headers=None, data=None):
            for host, answer in routes.items():
                if host in url: return answer
            raise AssertionError("unexpected probe " + url)
        original = apksecrets._probe; apksecrets._probe = probe
        return original

    def test_google_key_live_on_firebase_despite_gemini_rejection(self):
        restore = self._fake_probe({
            "generativelanguage": (400, b'{"error":{"reason":"API_KEY_INVALID"}}'),
            "identitytoolkit": (200, b'{"projectId":"1"}'),
        })
        try: self.assertIs(apksecrets.verify_secret("GoogleGeminiAPIKey", "AIzaSyX"), True)
        finally: apksecrets._probe = restore

    def test_google_key_expired_is_broken(self):
        restore = self._fake_probe({
            "generativelanguage": (400, b'{"error":{"reason":"API_KEY_INVALID"}}'),
            "identitytoolkit": (400, b'{"error":"API key expired"}'),
        })
        try: self.assertIs(apksecrets.verify_secret("GoogleGeminiAPIKey", "AIzaSyX"), False)
        finally: apksecrets._probe = restore

    def test_google_key_android_restricted_stays_unverdicted(self):
        restore = self._fake_probe({
            "generativelanguage": (400, b'{"error":{"reason":"API_KEY_INVALID"}}'),
            "identitytoolkit": (403, b"android client blocked"),
            "maps.googleapis": (200, b'{"status":"REQUEST_DENIED","error_message":"This API is not activated on your API project."}'),
        })
        try: self.assertIs(apksecrets.verify_secret("GoogleGeminiAPIKey", "AIzaSyX"), None)
        finally: apksecrets._probe = restore

    def test_google_key_live_on_maps_despite_gemini_rejection(self):
        restore = self._fake_probe({
            "generativelanguage": (400, b'{"error":{"reason":"API_KEY_INVALID"}}'),
            "identitytoolkit": (403, b"android client blocked"),
            "maps.googleapis": (200, b'{"status":"ZERO_RESULTS"}'),
        })
        try: self.assertIs(apksecrets.verify_secret("UnknownDetector", "AIzaSyX"), True)
        finally: apksecrets._probe = restore

    def test_unknown_detector_uses_value_prefix(self):
        restore = self._fake_probe({"api.groq.com": (200, b"[]")})
        try: self.assertIs(apksecrets.verify_secret("SomeDetector", "gsk_abcdef"), True)
        finally: apksecrets._probe = restore

    def test_sk_prefix_falls_through_to_deepseek(self):
        seen = []
        def probe(url, headers=None):
            seen.append(url)
            return (401, b"") if "api.openai.com" in url else (200, b"[]")
        original = apksecrets._probe; apksecrets._probe = probe
        try: self.assertIs(apksecrets.verify_secret("SomeDetector", "sk-nothing"), True)
        finally: apksecrets._probe = original
        self.assertIn("api.deepseek.com", seen[-1])

    def test_aws_key_is_sigv4_signed_against_sts(self):
        seen = {}
        def probe(url, headers=None):
            seen["url"], seen["headers"] = url, headers or {}
            return 403, b""
        original = apksecrets._probe; apksecrets._probe = probe
        try:
            key = "AKIAIOSFODNN7EXAMPLE:wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
            self.assertIs(apksecrets.verify_secret("AWS", key), False)
            self.assertIs(apksecrets.verify_secret("AWS", "not-an-aws-key"), None)
        finally: apksecrets._probe = original
        self.assertIn("sts.amazonaws.com", seen["url"])
        self.assertIn("AWS4-HMAC-SHA256 Credential=AKIAIOSFODNN7EXAMPLE/", seen["headers"]["Authorization"])
        self.assertIn("SignedHeaders=host;x-amz-date", seen["headers"]["Authorization"])

    def test_cloudflare_token_success_field_decides(self):
        restore = self._fake_probe({"api.cloudflare.com": (200, b'{"success":true,"result":{"status":"active"}}')})
        try: self.assertIs(apksecrets.verify_secret("CloudflareAPIToken", "v1.0-abc"), True)
        finally: apksecrets._probe = restore

    def test_short_detector_names_do_not_capture_longer_ones(self):
        restore = self._fake_probe({"api.box.com": (401, b""), "api.wit.ai": (400, b'{"code":"no-auth"}'),
                                    "api.t.ly": (401, b""), "api.trello.com": (401, b"invalid key"),
                                    "api.miro.com": (401, b""), "app.eraser.io": (401, b"Unauthenticated")})
        try:
            for name in ("Box", "Wit", "TLy", "TrelloApiKey", "Miro", "Eraser"):
                self.assertIs(apksecrets.verify_secret(name, "z" * 32), False, name)
            for name in ("BoxOauth", "Databox", "BuiltWith", "Rootly"):
                self.assertIs(apksecrets.verify_secret(name, "z" * 32), None, name)
        finally: apksecrets._probe = restore

    def test_trello_key_is_judged_by_the_error_body(self):
        restore = self._fake_probe({"api.trello.com": (401, b"invalid token")})
        try: self.assertIs(apksecrets.verify_secret("TrelloApiKey", "z" * 32), True)  # key accepted, token missing
        finally: apksecrets._probe = restore

    def test_onesignal_and_honeycomb_rules(self):
        restore = self._fake_probe({"api.onesignal.com": (401, b'{"errors":["Access denied"]}'),
                                    "api.honeycomb.io": (200, b'{"team":{"slug":"t"}}')})
        try:
            self.assertIs(apksecrets.verify_secret("Onesignal", "os_v2_app_" + "a" * 60), False)
            self.assertIs(apksecrets.verify_secret("Honeycomb", "a" * 32), True)
        finally: apksecrets._probe = restore

    def test_verifier_for_distinguishes_unknown_from_inconclusive(self):
        self.assertIsNone(apksecrets.verifier_for("MysteryDetector", "zzz"))
        self.assertIsNone(apksecrets.verify_secret("MysteryDetector", "zzz"))
        restore = self._fake_probe({"api.onesignal.com": (400, b"deprecated v1 token")})
        try: self.assertIs(apksecrets.verify_secret("Onesignal", "uuid-app-id"), None)  # probe ran, no verdict
        finally: apksecrets._probe = restore

    def test_auto_verifier_dedupes_values_and_reports_unchecked(self):
        with tempfile.TemporaryDirectory() as d:
            store = apksecrets.Store(Path(d) / "state")
            try:
                job = store.add("local", "local://t", "com.t")
                other = store.add("local", "local://t2", "com.t2")
                now = time.time()
                for job_id, detector, value in ((job["id"], "Slack", "xoxb-dup"), (other["id"], "Slack", "xoxb-dup"),
                                                (job["id"], "Mystery", "zzz")):
                    store.db.execute("INSERT INTO secrets(job_id,detector,value,file,verified,working,created_at) VALUES(?,?,?,?,0,NULL,?)",
                                     (job_id, detector, value, "f", now))
                store.db.commit()
                probes = []
                def fake_verifier_for(detector, value):
                    return None if detector == "Mystery" else (lambda v: probes.append(v) or True)
                original = apksecrets.verifier_for; apksecrets.verifier_for = fake_verifier_for
                verifier = apksecrets.AutoVerifier(store)
                try: verifier.pass_once()
                finally: apksecrets.verifier_for = original
                marked = {r["value"]: r["working"] for r in store.secrets()}
                self.assertEqual(marked, {"xoxb-dup": 1, "zzz": None})
                self.assertEqual(probes, ["xoxb-dup"])  # duplicate value probed once
                self.assertEqual(verifier.tried, {1, 2, 3})
                verifier.verify_now()  # manual pass re-arms every unchecked secret
                self.assertEqual(verifier.tried, set())
                self.assertTrue(verifier.wake.is_set())
            finally: store.close()

    def test_apks_bundle_is_expanded(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); bundle = root / "split.apks"
            nested = root / "base.apk"
            with zipfile.ZipFile(nested, "w") as z: z.writestr("assets/key.txt", "fixture")
            with zipfile.ZipFile(bundle, "w") as z: z.write(nested, "base.apk")
            out = root / "out"; out.mkdir()
            apksecrets.safe_unpack(bundle, out, 10000); apksecrets.safe_unpack_bundles(out, 10000)
            self.assertTrue((out / "base.apk.contents" / "assets" / "key.txt").is_file())

    def test_finding_count_is_json_only(self):
        with tempfile.TemporaryDirectory() as d:
            report = Path(d) / "r"; report.write_text('{"DetectorName":"x"}\nnoise\n')
            self.assertEqual(apksecrets.finding_count(report), 1)

    def test_local_run_writes_report_then_removes_clean_apk(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); incoming = root / "incoming.apk"
            with zipfile.ZipFile(incoming, "w") as z: z.writestr("assets/config.txt", "harmless")
            fake = root / "trufflehog"
            fake.write_text("#!/bin/sh\nexit 0\n")
            fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
            state = root / "state"
            self.assertEqual(apksecrets.main(["--state", str(state), "import", str(incoming)]), 0)
            self.assertEqual(apksecrets.main(["--state", str(state), "--trufflehog", str(fake), "run", "--source", "local", "--workers", "1"]), 0)
            store = apksecrets.Store(state)
            try:
                job = store.jobs(("clean",))[0]
                self.assertFalse(job["download_path"])
                self.assertTrue(Path(job["report_path"]).is_file())
            finally: store.close()


    def test_findings_job_also_deletes_apk(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); incoming = root / "incoming.apk"
            with zipfile.ZipFile(incoming, "w") as z: z.writestr("assets/k.txt", "xoxb-1")
            fake = root / "trufflehog"
            fake.write_text('#!/bin/sh\nprintf \'{"DetectorName":"Slack","Raw":"xoxb-1","Verified":true,"SourceMetadata":{"Data":{"Filesystem":{"file":"assets/k.txt"}}}}\\n\'\nexit 0\n')
            fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
            state = root / "state"
            apksecrets.main(["--state", str(state), "import", str(incoming)])
            apksecrets.main(["--state", str(state), "--trufflehog", str(fake), "run", "--source", "local", "--workers", "1"])
            store = apksecrets.Store(state)
            try:
                job = store.jobs(("findings",))[0]
                self.assertFalse(job["download_path"])  # apk deleted after secrets retained
                self.assertFalse((store.root / "apks").exists() and list((store.root / "apks").glob("*.apk")))
                self.assertEqual(len(store.secrets()), 1)
            finally: store.close()

    def test_run_retries_a_failed_scan(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); incoming = root / "incoming.apk"
            with zipfile.ZipFile(incoming, "w") as z: z.writestr("x", "ok")
            marker, fake = root / "once", root / "trufflehog"
            fake.write_text(f"#!/bin/sh\n[ -e {marker} ] || {{ touch {marker}; exit 1; }}\nexit 0\n")
            fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
            state = root / "state"
            apksecrets.main(["--state", str(state), "import", str(incoming)])
            apksecrets.main(["--state", str(state), "--trufflehog", str(fake), "run", "--source", "local", "--workers", "1", "--retries", "1"])
            store = apksecrets.Store(state)
            try: self.assertEqual(store.jobs(("clean",))[0]["retries"], 1)
            finally: store.close()

if __name__ == "__main__": unittest.main()
