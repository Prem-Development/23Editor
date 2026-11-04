"""First-5-seconds workflow regression tests (live ffmpeg + HTTP).

Reproduces the real user-visible failure: an 11-second (and 13-second)
source with an instruction asking for only the first 5 seconds, where
the planner response OMITS the trim. The backend must reconcile the
requested interval into the plan and the downloaded output must be
about 5 seconds with audio. The Meta call is stubbed out, never sent:
zero spend. Skips cleanly without system ffmpeg/ffprobe or without
listening sockets. Uses an isolated temp dir; user storage is untouched.
"""
import copy
import json
import os
import shutil
import subprocess
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from unittest import mock

import server as server_module
from server import Handler, TimeoutHTTPServer

TRIMLESS_PLAN = {"operations": [{"op": "resize_pad", "aspect": "original"}],
                 "notes": ""}
INSTRUCTION = "keep only the first 5 seconds"


class OmittedTrimWorkflowTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ffmpeg = shutil.which("ffmpeg")
        cls.ffprobe = shutil.which("ffprobe")
        if not cls.ffmpeg or not cls.ffprobe:
            raise unittest.SkipTest("system ffmpeg/ffprobe not on PATH")
        cls.tmp = tempfile.mkdtemp(prefix="23editor-first5-")
        try:
            cls.httpd = TimeoutHTTPServer(("127.0.0.1", 0), Handler)
        except OSError:
            raise unittest.SkipTest(
                "listening sockets are blocked in this environment"
            )
        cls._old_storage = server_module.APP.storage
        server_module.APP.storage = server_module.Storage(
            os.path.join(cls.tmp, "storage")
        )
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(
            target=cls.httpd.serve_forever,
            kwargs={"poll_interval": 0.05},
            daemon=True,
        )
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        server_module.APP.storage = cls._old_storage
        cls.httpd.shutdown()
        cls.httpd.server_close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    # -- helpers ------------------------------------------------------
    def _post(self, path, body, content_type):
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}", data=body, method="POST",
            headers={"Content-Type": content_type},
        )
        try:
            with urllib.request.urlopen(request, timeout=180) as response:
                return response.status, json.loads(response.read() or b"{}")
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read() or b"{}")

    def _make_source(self, seconds):
        path = os.path.join(self.tmp, f"source-{seconds}.mp4")
        subprocess.run(
            [self.ffmpeg, "-y", "-v", "error",
             "-f", "lavfi",
             "-i", f"testsrc=duration={seconds}:size=320x240:rate=10",
             "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
             "-c:v", "libx264", "-pix_fmt", "yuv420p", "-g", "48",
             "-c:a", "aac", "-shortest", path],
            check=True, timeout=180,
        )
        return path

    def _upload(self, path):
        with open(path, "rb") as handle:
            payload = handle.read()
        boundary = "first5testboundary"
        body = (
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="file"; '
            'filename="source.mp4"\r\n'
            "Content-Type: application/octet-stream\r\n\r\n"
        ).encode("latin-1") + payload + f"\r\n--{boundary}--\r\n".encode()
        status, obj = self._post(
            "/api/upload", body, f"multipart/form-data; boundary={boundary}"
        )
        self.assertEqual(status, 200, obj)
        self.assertTrue((obj.get("media") or {}).get("has_audio"), obj)
        return obj["file_id"], float(obj["media"]["duration"])

    def _probe(self, path):
        done = subprocess.run(
            [self.ffprobe, "-v", "error", "-print_format", "json",
             "-show_format", "-show_streams", path],
            capture_output=True, text=True, timeout=60,
        )
        self.assertEqual(done.returncode, 0, done.stderr)
        return json.loads(done.stdout)

    def _run_workflow(self, seconds):
        source = self._make_source(seconds)
        file_id, probed = self._upload(source)
        self.assertAlmostEqual(probed, float(seconds), delta=0.3)
        with mock.patch.object(
            server_module, "_spark_plan",
            side_effect=lambda *a, **k: (copy.deepcopy(TRIMLESS_PLAN), [], []),
        ):
            status, planned = self._post(
                "/api/plan",
                json.dumps({
                    "file_id": file_id, "instruction": INSTRUCTION,
                    "style": "", "aspect_ratio": "original",
                }).encode(),
                "application/json",
            )
        self.assertEqual(status, 200, planned)
        ops = planned["plan"]["operations"]
        trims = [o for o in ops if o.get("op") == "trim"]
        self.assertEqual(len(trims), 1, planned)
        self.assertEqual(trims[0]["start"], 0.0)
        self.assertEqual(trims[0]["end"], 5.0)
        self.assertTrue(
            any("first-5s" in w for w in planned["adjustments"]), planned
        )
        status, obj = self._post(
            "/api/render",
            json.dumps({
                "file_id": file_id, "plan": planned["plan"],
                "instruction": INSTRUCTION,
            }).encode(),
            "application/json",
        )
        self.assertEqual(status, 200, obj)
        output = obj.get("output") or {}
        self.assertTrue(output.get("has_audio"), obj)
        self.assertAlmostEqual(
            float(output.get("duration", 0.0)), 5.0, delta=0.6
        )
        url = f"http://127.0.0.1:{self.port}" + obj["url"]
        with urllib.request.urlopen(url, timeout=60) as response:
            rendered = response.read()
        out_path = os.path.join(self.tmp, f"rendered-{seconds}.mp4")
        with open(out_path, "wb") as handle:
            handle.write(rendered)
        info = self._probe(out_path)
        kinds = {s["codec_type"] for s in info["streams"]}
        self.assertIn("video", kinds)
        self.assertIn("audio", kinds)
        duration = float(info["format"]["duration"])
        self.assertGreater(duration, 4.5, info)
        self.assertLess(duration, 5.6, info)
        return probed, duration

    # -- tests ----------------------------------------------------------
    def test_omitted_trim_still_renders_first_five_seconds(self):
        for seconds in (11, 13):
            with self.subTest(source_seconds=seconds):
                probed, rendered = self._run_workflow(seconds)
                print(f"\n  source={probed:.2f}s rendered={rendered:.2f}s")


if __name__ == "__main__":
    unittest.main()
