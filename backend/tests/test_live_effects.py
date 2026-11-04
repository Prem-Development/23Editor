"""Live effects tests (real ffmpeg + HTTP, skipped if absent).

Renders the Slice 1 operations end to end through POST /api/render and
verifies measured output properties with ffprobe. No Meta calls, no key
needed. Uses an isolated temp dir; user storage is untouched.
"""
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
from ass_subtitles import find_tamil_font
from server import Handler, TimeoutHTTPServer


class LiveEffectsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ffmpeg = shutil.which("ffmpeg")
        cls.ffprobe = shutil.which("ffprobe")
        if not cls.ffmpeg or not cls.ffprobe:
            raise unittest.SkipTest("system ffmpeg/ffprobe not on PATH")
        cls.tmp = tempfile.mkdtemp(prefix="23editor-effects-")
        cls.source = os.path.join(cls.tmp, "source.mp4")
        subprocess.run(
            [cls.ffmpeg, "-y", "-v", "error",
             "-f", "lavfi", "-i", "testsrc=duration=12:size=320x240:rate=10",
             "-f", "lavfi", "-i", "sine=frequency=440:duration=12",
             "-c:v", "libx264", "-pix_fmt", "yuv420p", "-g", "48",
             "-c:a", "aac", "-shortest", cls.source],
            check=True, timeout=180,
        )
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
    def _require_font(self):
        name, _ = find_tamil_font()
        if name is None:
            raise unittest.SkipTest("no Tamil-capable system font")

    def _post(self, path, body, content_type):
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}", data=body, method="POST",
            headers={"Content-Type": content_type},
        )
        try:
            with urllib.request.urlopen(request, timeout=240) as response:
                return response.status, json.loads(response.read() or b"{}")
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read() or b"{}")

    def _upload(self, path, filename="source.mp4"):
        with open(path, "rb") as handle:
            payload = handle.read()
        boundary = "effectstestboundary"
        body = (
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="file"; '
            f'filename="{filename}"\r\n'
            "Content-Type: application/octet-stream\r\n\r\n"
        ).encode("latin-1") + payload + f"\r\n--{boundary}--\r\n".encode()
        status, obj = self._post(
            "/api/upload", body, f"multipart/form-data; boundary={boundary}"
        )
        self.assertEqual(status, 200, obj)
        return obj["file_id"]

    def _render(self, file_id, operations, instruction=""):
        body = {"file_id": file_id, "plan": {"operations": operations}}
        if instruction:
            body["instruction"] = instruction
        return self._post("/api/render", json.dumps(body).encode(),
                          "application/json")

    def _download(self, url, name):
        with urllib.request.urlopen(
            f"http://127.0.0.1:{self.port}{url}", timeout=60
        ) as response:
            data = response.read()
        path = os.path.join(self.tmp, name)
        with open(path, "wb") as handle:
            handle.write(data)
        return path

    def _probe(self, path):
        done = subprocess.run(
            [self.ffprobe, "-v", "error", "-print_format", "json",
             "-show_format", "-show_streams", path],
            capture_output=True, text=True, timeout=60,
        )
        self.assertEqual(done.returncode, 0, done.stderr)
        return json.loads(done.stdout)

    def _max_volume_db(self, path, start, length):
        done = subprocess.run(
            [self.ffmpeg, "-v", "info", "-ss", f"{start:.2f}",
             "-t", f"{length:.2f}", "-i", path,
             "-af", "volumedetect", "-f", "null", "-"],
            capture_output=True, text=True, timeout=120,
        )
        for line in done.stderr.splitlines():
            if "max_volume:" in line:
                try:
                    return float(
                        line.split("max_volume:")[1].split("dB")[0]
                    )
                except ValueError:
                    pass
        return -100.0

    def _streams(self, info):
        return {s["codec_type"] for s in info["streams"]}

    # -- tests ----------------------------------------------------------
    def test_acceptance_trim_text_zoom_captions(self):
        self._require_font()
        file_id = self._upload(self.source)
        status, obj = self._render(file_id, [
            {"op": "trim", "start": 0.0, "end": 10.0},
            {"op": "text", "text": "My day", "start": 0.0, "end": 2.0,
             "position": "top", "animation": "fade", "size": "large"},
            {"op": "zoom", "start": 3.0, "end": 5.0, "to": 1.4},
            {"op": "captions", "segments": [
                {"start": 0.5, "end": 2.5, "text": "வணக்கம்"},
                {"start": 5.0, "end": 7.0, "text": "welcome back"},
            ]},
        ])
        self.assertEqual(status, 200, obj)
        out = self._download(obj["url"], "accept.mp4")
        info = self._probe(out)
        self.assertEqual(self._streams(info), {"video", "audio"})
        duration = float(info["format"]["duration"])
        self.assertGreater(duration, 9.4)
        self.assertLess(duration, 10.6)
        video = next(s for s in info["streams"]
                     if s["codec_type"] == "video")
        self.assertEqual((video["width"], video["height"]), (320, 240))
        self.assertAlmostEqual(
            float(obj["output"]["duration"]), 10.0, delta=0.6
        )

    def test_keep_join_with_transition(self):
        file_id = self._upload(self.source)
        status, obj = self._render(file_id, [
            {"op": "keep", "start": 0.0, "end": 3.0},
            {"op": "keep", "start": 6.0, "end": 9.0},
            {"op": "transition", "type": "fade", "duration": 0.5},
        ])
        self.assertEqual(status, 200, obj)
        out = self._download(obj["url"], "joined.mp4")
        info = self._probe(out)
        self.assertEqual(self._streams(info), {"video", "audio"})
        duration = float(info["format"]["duration"])
        self.assertGreater(duration, 5.0)
        self.assertLess(duration, 6.0)

    def test_remove_cuts_interval_with_grade(self):
        file_id = self._upload(self.source)
        status, obj = self._render(file_id, [
            {"op": "remove", "start": 4.0, "end": 8.0},
            {"op": "grade", "preset": "warm", "intensity": 0.6},
        ])
        self.assertEqual(status, 200, obj)
        out = self._download(obj["url"], "removed.mp4")
        info = self._probe(out)
        duration = float(info["format"]["duration"])
        self.assertGreater(duration, 7.4)
        self.assertLess(duration, 8.6)

    def test_music_duck_and_sfx(self):
        song = os.path.join(self.tmp, "song.wav")
        subprocess.run(
            [self.ffmpeg, "-y", "-v", "error",
             "-f", "lavfi", "-i", "sine=frequency=220:duration=8",
             "-c:a", "pcm_s16le", song],
            check=True, timeout=120,
        )
        file_id = self._upload(self.source)
        music_id = self._upload(song, filename="song.wav")
        status, obj = self._render(file_id, [
            {"op": "trim", "start": 0.0, "end": 5.0},
            {"op": "music", "file_id": music_id,
             "volume_db": -12.0, "duck": True},
            {"op": "sfx", "asset": "pop", "at": 2.0},
        ])
        self.assertEqual(status, 200, obj)
        out = self._download(obj["url"], "mixed.mp4")
        info = self._probe(out)
        self.assertEqual(self._streams(info), {"video", "audio"})
        duration = float(info["format"]["duration"])
        self.assertGreater(duration, 4.5)
        self.assertLess(duration, 5.6)

    def test_sfx_audible_at_edited_time(self):
        silent = os.path.join(self.tmp, "silent.mp4")
        subprocess.run(
            [self.ffmpeg, "-y", "-v", "error",
             "-f", "lavfi", "-i",
             "testsrc=duration=6:size=320x240:rate=10",
             "-c:v", "libx264", "-pix_fmt", "yuv420p",
             "-an", silent],
            check=True, timeout=120,
        )
        file_id = self._upload(silent)
        status, obj = self._render(file_id, [
            {"op": "sfx", "asset": "pop", "at": 2.0},
        ])
        self.assertEqual(status, 200, obj)
        out = self._download(obj["url"], "sfxonly.mp4")
        at_effect = self._max_volume_db(out, 1.9, 0.4)
        away = self._max_volume_db(out, 4.0, 0.4)
        self.assertGreater(
            at_effect, away + 10.0,
            f"pop peaks near 2.0s ({at_effect}dB), "
            f"silence elsewhere ({away}dB)",
        )

    def test_fit_crop_dimensions(self):
        file_id = self._upload(self.source)
        status, obj = self._render(file_id, [
            {"op": "trim", "start": 0.0, "end": 2.0},
            {"op": "resize_pad", "aspect": "1:1", "fit": "crop"},
        ])
        self.assertEqual(status, 200, obj)
        out = self._download(obj["url"], "cropped.mp4")
        info = self._probe(out)
        video = next(s for s in info["streams"]
                     if s["codec_type"] == "video")
        self.assertEqual((video["width"], video["height"]), (1080, 1080))

    def test_auto_captions_from_mocked_transcript(self):
        self._require_font()
        file_id = self._upload(self.source)
        # Raw provider shape: the trimmed plan transcribes the RETAINED
        # audio via client.transcribe, not the full-source route.
        canned = {"transcript": "hello world", "turns": [
            {"transcript": "hello", "startMs": 500, "endMs": 2500},
            {"transcript": "world", "startMs": 3000, "endMs": 5000},
        ]}
        client = mock.Mock()
        client.transcribe.return_value = canned
        with mock.patch.object(
            server_module.APP, "client", return_value=client
        ):
            status, obj = self._render(file_id, [
                {"op": "trim", "start": 0.0, "end": 6.0},
                {"op": "captions", "source": "auto"},
            ])
        client.transcribe.assert_called_once()
        self.assertEqual(status, 200, obj)
        self.assertIn("transcribe", obj["steps_used"])
        out = self._download(obj["url"], "autocap.mp4")
        info = self._probe(out)
        self.assertEqual(self._streams(info), {"video", "audio"})
        duration = float(info["format"]["duration"])
        self.assertGreater(duration, 5.4)
        self.assertLess(duration, 6.6)

    def test_capabilities_endpoint(self):
        with urllib.request.urlopen(
            f"http://127.0.0.1:{self.port}/api/capabilities", timeout=30
        ) as response:
            obj = json.loads(response.read() or b"{}")
        self.assertEqual(response.status, 200)
        names = [entry["op"] for entry in obj["operations"]]
        self.assertEqual(len(obj["operations"]), 21)
        for op in ("intro", "outro", "banner",
                   "cutaway", "pip", "insert"):
            self.assertIn(op, names)
        self.assertIn("pop", obj["sfx_assets"])
        self.assertIn("warm", obj["grade_presets"])
        self.assertIn("available", obj["tamil_font"])
        self.assertIn("bottom", obj["banner_positions"])


if __name__ == "__main__":
    unittest.main()
