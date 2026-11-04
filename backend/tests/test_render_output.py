"""Render-output regression tests (live ffmpeg + HTTP, skipped if absent).

Uploads a real synthesized video, renders trim + resize through POST
/api/render, then verifies the output is a real playable video with the
requested duration/aspect and preserved original audio — and that the
source bytes are untouched. The plan uses no SAM ops, so no Meta calls
(and no API key) are needed.
"""
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

import server as server_module
from server import Handler, TimeoutHTTPServer


def _md5(path):
    digest = hashlib.md5()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class LiveRenderOutputTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ffmpeg = shutil.which("ffmpeg")
        cls.ffprobe = shutil.which("ffprobe")
        if not cls.ffmpeg or not cls.ffprobe:
            raise unittest.SkipTest("system ffmpeg/ffprobe not on PATH")
        cls.tmp = tempfile.mkdtemp(prefix="23editor-render-")
        cls.source = os.path.join(cls.tmp, "source.mp4")
        subprocess.run(
            [cls.ffmpeg, "-y", "-v", "error",
             "-f", "lavfi", "-i", "testsrc=duration=2:size=320x240:rate=10",
             "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
             "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
             "-shortest", cls.source],
            check=True, timeout=120,
        )
        cls.source_md5 = _md5(cls.source)
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
            with urllib.request.urlopen(request, timeout=120) as response:
                return response.status, json.loads(response.read() or b"{}")
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read() or b"{}")

    def _upload_source(self, path=None):
        with open(path or self.source, "rb") as handle:
            payload = handle.read()
        boundary = "rendertestboundary"
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
        media = obj.get("media") or {}
        self.assertTrue(media.get("has_audio"), media)
        self.assertGreater(media.get("width", 0), 0)
        return obj["file_id"]

    def _probe(self, path):
        done = subprocess.run(
            [self.ffprobe, "-v", "error", "-print_format", "json",
             "-show_format", "-show_streams", path],
            capture_output=True, text=True, timeout=60,
        )
        self.assertEqual(done.returncode, 0, done.stderr)
        return json.loads(done.stdout)

    # -- tests ----------------------------------------------------------
    def test_render_produces_playable_video_with_audio(self):
        file_id = self._upload_source()
        status, obj = self._post(
            "/api/render",
            json.dumps({"file_id": file_id, "plan": {"operations": [
                {"op": "trim", "start": 0.0, "end": 1.0},
                {"op": "resize_pad", "aspect": "1:1"},
            ]}}).encode(),
            "application/json",
        )
        self.assertEqual(status, 200, obj)
        self.assertEqual(
            obj.get("steps_used"), ["plan_validated", "ffmpeg_render"]
        )
        timings = obj["timings"]
        self.assertEqual(timings["sam_ms"], 0)
        self.assertEqual(timings["transcribe_ms"], 0)
        self.assertGreaterEqual(timings["ffmpeg_ms"], 0)
        self.assertGreaterEqual(timings["verify_ms"], 0)
        url = f"http://127.0.0.1:{self.port}" + obj["url"]
        with urllib.request.urlopen(url, timeout=60) as response:
            rendered = response.read()
        self.assertGreater(len(rendered), 10000)
        out_path = os.path.join(self.tmp, "rendered.mp4")
        with open(out_path, "wb") as handle:
            handle.write(rendered)
        info = self._probe(out_path)
        video = next(
            s for s in info["streams"] if s["codec_type"] == "video"
        )
        audio = next(
            s for s in info["streams"] if s["codec_type"] == "audio"
        )
        self.assertEqual((video["width"], video["height"]), (1080, 1080))
        self.assertEqual(video["codec_name"], "h264")
        self.assertEqual(audio["codec_name"], "aac")
        duration = float(info["format"]["duration"])
        self.assertGreater(duration, 0.8)
        self.assertLess(duration, 1.3)

    def test_trim_first_five_seconds_of_thirteen_second_clip(self):
        long_src = os.path.join(self.tmp, "long.mp4")
        subprocess.run(
            [self.ffmpeg, "-y", "-v", "error",
             "-f", "lavfi", "-i", "testsrc=duration=13:size=320x240:rate=10",
             "-f", "lavfi", "-i", "sine=frequency=440:duration=13",
             "-c:v", "libx264", "-pix_fmt", "yuv420p", "-g", "48",
             "-c:a", "aac", "-shortest", long_src],
            check=True, timeout=180,
        )
        file_id = self._upload_source(long_src)
        status, obj = self._post(
            "/api/render",
            json.dumps({"file_id": file_id, "plan": {"operations": [
                {"op": "trim", "start": 0.0, "end": 5.0},
            ]}}).encode(),
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
        out_path = os.path.join(self.tmp, "trimmed.mp4")
        with open(out_path, "wb") as handle:
            handle.write(rendered)
        info = self._probe(out_path)
        kinds = {s["codec_type"] for s in info["streams"]}
        self.assertIn("video", kinds)
        self.assertIn("audio", kinds)
        duration = float(info["format"]["duration"])
        self.assertGreater(duration, 4.5)
        self.assertLess(duration, 5.6)
        audio = next(
            s for s in info["streams"] if s["codec_type"] == "audio"
        )
        audio_duration = float(audio.get("duration") or duration)
        self.assertGreater(audio_duration, 4.5)
        self.assertLess(audio_duration, 5.7)

    def test_source_bytes_untouched_by_render(self):
        file_id = self._upload_source()
        stored = [
            os.path.join(root, name)
            for root, _, names in os.walk(os.path.join(self.tmp, "storage"))
            for name in names if name.startswith(file_id)
        ]
        self.assertEqual(len(stored), 1)
        self.assertEqual(_md5(stored[0]), self.source_md5)
        status, _ = self._post(
            "/api/render",
            json.dumps({"file_id": file_id, "plan": {"operations": [
                {"op": "trim", "start": 0.0, "end": 1.0},
            ]}}).encode(),
            "application/json",
        )
        self.assertEqual(status, 200)
        self.assertEqual(_md5(stored[0]), self.source_md5)
        self.assertEqual(_md5(self.source), self.source_md5)


if __name__ == "__main__":
    unittest.main()
