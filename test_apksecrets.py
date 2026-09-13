import importlib.machinery
import os
import stat
import tempfile
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
                apksecrets.discover(store, FakeFetcher(), args)
                self.assertEqual(len(store.jobs()), 2)
                apksecrets.discover(store, FakeFetcher(), args)
                self.assertEqual(len(store.jobs()), 2)
                data["packages"]["z"] = [{"versionCode": 1, "apkName": "z-1.apk"}]
                apksecrets.discover(store, FakeFetcher(), args)
                self.assertEqual(len(store.jobs()), 3)
            finally: store.close()

    def test_aptoide_queues_every_matching_release(self):
        data = {"datalist": {"list": [
            {"package": "app.one", "vername": "2.0", "file": {"path": "https://x/app.one-2.apk"}},
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
                apksecrets.discover(store, FakeFetcher(), args)
                queued = {row["package"] for row in store.jobs()}
                self.assertEqual(queued, {"app.one", "app.two"})
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
