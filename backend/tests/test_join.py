"""Multi-clip joining: /api/join validation, normalization, and cache.

Pure tests cover argv shape and request validation; live tests join
three mixed clips (landscape, portrait at another frame rate, silent)
and prove order, pillarbox padding, audio fill, cache reuse, and that
the join_id plans and renders like a single source. Isolated temp
storage only.
"""
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from unittest import mock

import server as server_module
from ffmpeg_render import FFmpegError, build_join_args
from server import (
    MAX_JOIN_CLIPS,
    ApiError,
    Handler,
    TimeoutHTTPServer,
    route_join,
)


class BuildJoinArgsTest(unittest.TestCase):
    def test_needs_two_clips(self):
        with self.assertRaises(FFmpegError):
            build_join_args(
                [{"path": "a.mp4", "duration": 2.0, "has_audio": True}],
                320, 240, 10.0, "out.mp4",
            )

    def test_argv_normalizes_and_concats_in_order(self):
        argv = build_join_args(
            [
                {"path": "a.mp4", "duration": 2.0, "has_audio": True},
                {"path": "b.mp4", "duration": 3.0, "has_audio": False},
            ],
            320, 240, 15.0, "out.mp4",
        )
        text = " ".join(argv)
        self.assertIn("-i a.mp4 -i b.mp4", text)
        self.assertIn("scale=320:240", text)
        self.assertIn("pad=320:240", text)
        self.assertIn("fps=15", text)
        self.assertIn("anullsrc", text)
        self.assertIn("concat=n=2:v=1:a=1", text)
        self.assertIn("libx264", text)
        # The silent clip's null audio spans its own duration.
        self.assertIn("anullsrc=r=44100:cl=stereo:d=3.000", text)


class JoinValidationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="23editor-joinval-")
        self._old_storage = server_module.APP.storage
        server_module.APP.storage = server_module.Storage(
            os.path.join(self.tmp, "storage")
        )

    def tearDown(self):
        server_module.APP.storage = self._old_storage
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_empty_and_oversized_lists_rejected(self):
        with self.assertRaises(ApiError) as ctx:
            route_join({"file_ids": []})
        self.assertEqual(ctx.exception.status, 400)
        with self.assertRaises(ApiError) as ctx:
            route_join({"file_ids": ["a" * 32] * (MAX_JOIN_CLIPS + 1)})
        self.assertEqual(ctx.exception.status, 400)
        self.assertIn("At most 8 clips", ctx.exception.message)

    def test_bad_hex_rejected(self):
        with self.assertRaises(ApiError) as ctx:
            route_join({"file_ids": ["not-an-id"]})
        self.assertEqual(ctx.exception.status, 400)

    def test_unknown_id_reports_which_is_missing(self):
        missing = "b" * 32
        with self.assertRaises(ApiError) as ctx:
            route_join({"file_ids": [missing]})
        self.assertEqual(ctx.exception.status, 404)
        self.assertEqual(ctx.exception.details.get("missing"), missing)

    def test_bad_aspect_rejected(self):
        with self.assertRaises(ApiError) as ctx:
            route_join({"file_ids": ["c" * 32], "aspect_ratio": "21:9"})
        self.assertEqual(ctx.exception.status, 400)

    def test_total_duration_capped(self):
        ids = []
        for name in ("one.mp4", "two.mp4"):
            path = os.path.join(self.tmp, name)
            with open(path, "wb") as handle:
                handle.write(b"fake-video-bytes")
            file_id = server_module.APP.storage.new_id()
            server_module.APP.storage.register(file_id, {
                "path": path, "name": name, "kind": "video",
            })
            ids.append(file_id)
        media = {"duration": 1000.0, "width": 640, "height": 360,
                 "fps": 30.0, "has_audio": True}
        with mock.patch.object(
            server_module, "probe_media", return_value=dict(media)
        ):
            with self.assertRaises(ApiError) as ctx:
                route_join({"file_ids": ids})
        self.assertEqual(ctx.exception.status, 400)
        self.assertIn("at most 1800s", ctx.exception.message)


class JoinLiveTest(unittest.TestCase):
    """Joins three mixed clips and renders from the joined timeline."""

    @classmethod
    def setUpClass(cls):
        cls.ffmpeg = shutil.which("ffmpeg")
        cls.ffprobe = shutil.which("ffprobe")
        if not cls.ffmpeg or not cls.ffprobe:
            raise unittest.SkipTest("ffmpeg/ffprobe are not installed")
        cls.tmp = tempfile.mkdtemp(prefix="23editor-join-")
        # A: landscape with tone; B: portrait, faster fps, other tone;
        # C: landscape, silent.
        specs = (
            ("a.mp4", "red", "320x240", "10", "2", "sine=frequency=440"),
            ("b.mp4", "lime", "240x320", "15", "2", "sine=frequency=660"),
            ("c.mp4", "blue", "320x240", "10", "2", None),
        )
        cls.clips = []
        for name, color, size, rate, dur, audio in specs:
            path = os.path.join(cls.tmp, name)
            cmd = ["-y", "-v", "error", "-f", "lavfi", "-i",
                   f"color={color}:size={size}:rate={rate}:duration={dur}"]
            if audio:
                cmd += ["-f", "lavfi", "-i", f"{audio}:duration={dur}"]
                cmd += ["-c:v", "libx264", "-pix_fmt", "yuv420p",
                        "-c:a", "aac", "-shortest", path]
            else:
                cmd += ["-c:v", "libx264", "-pix_fmt", "yuv420p", path]
            subprocess.run([cls.ffmpeg] + cmd, check=True, timeout=180)
            cls.clips.append(path)
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

    def _post(self, path, body, content_type="application/json"):
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}", data=body, method="POST",
            headers={"Content-Type": content_type},
        )
        try:
            with urllib.request.urlopen(request, timeout=600) as response:
                return response.status, json.loads(response.read() or b"{}")
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read() or b"{}")

    def _upload(self, path, filename, label="source"):
        with open(path, "rb") as handle:
            payload = handle.read()
        boundary = "jointestboundary"
        body = (
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="file"; '
            f'filename="{filename}"\r\n'
            "Content-Type: application/octet-stream\r\n\r\n"
        ).encode("latin-1") + payload + (
            f"\r\n--{boundary}\r\n"
            'Content-Disposition: form-data; name="label"\r\n\r\n'
            f"{label}\r\n--{boundary}--\r\n"
        ).encode()
        status, obj = self._post(
            "/api/upload", body, f"multipart/form-data; boundary={boundary}"
        )
        self.assertEqual(status, 200, obj)
        return obj

    def _pixel(self, video, at, x, y):
        done = subprocess.run(
            [self.ffmpeg, "-y", "-v", "error", "-ss", f"{at:.2f}",
             "-i", video, "-frames:v", "1",
             "-vf", f"crop=1:1:{x}:{y},format=rgb24",
             "-f", "rawvideo", "-"],
            capture_output=True, timeout=120, check=True,
        )
        self.assertEqual(len(done.stdout), 3, "one rgb pixel")
        return tuple(done.stdout)

    def _max_volume_db(self, video, start, length):
        done = subprocess.run(
            [self.ffmpeg, "-v", "info", "-ss", f"{start:.2f}",
             "-t", f"{length:.2f}", "-i", video,
             "-af", "volumedetect", "-f", "null", "-"],
            capture_output=True, text=True, timeout=120, check=False,
        )
        for line in done.stderr.splitlines():
            match = re.search(r"max_volume:\s*(-?[0-9.]+)\s*dB", line)
            if match:
                return float(match.group(1))
        return -100.0

    def _download(self, url, name):
        with urllib.request.urlopen(
            f"http://127.0.0.1:{self.port}{url}", timeout=120
        ) as response:
            data = response.read()
        path = os.path.join(self.tmp, name)
        with open(path, "wb") as handle:
            handle.write(data)
        return path

    def test_join_orders_normalizes_caches_and_renders(self):
        ids = [
            self._upload(path, name)["file_id"]
            for path, name in zip(self.clips, ("a.mp4", "b.mp4", "c.mp4"))
        ]
        status, joined = self._post(
            "/api/join", json.dumps({"file_ids": ids}).encode()
        )
        self.assertEqual(status, 200, joined)
        self.assertEqual(joined["cache"], "built")
        self.assertGreaterEqual(joined["timings"]["probe_ms"], 0)
        self.assertGreaterEqual(joined["timings"]["build_ms"], 0)
        self.assertAlmostEqual(joined["duration"], 6.0, delta=0.4)
        self.assertEqual(
            (joined["width"], joined["height"]), (320, 240)
        )
        self.assertEqual(joined["fps"], 15.0)
        self.assertTrue(joined["has_audio"])
        clips = joined["clips"]
        self.assertEqual([c["name"] for c in clips],
                         ["a.mp4", "b.mp4", "c.mp4"])
        self.assertEqual(
            [round(c["offset"], 2) for c in clips], [0.0, 2.0, 4.0]
        )
        self.assertEqual(
            [c["has_audio"] for c in clips], [True, True, False]
        )
        # Repeat join is a cache hit with the same stable id.
        status, again = self._post(
            "/api/join", json.dumps({"file_ids": ids}).encode()
        )
        self.assertEqual(status, 200, again)
        self.assertEqual(again["cache"], "hit")
        self.assertEqual(again["join_id"], joined["join_id"])
        self.assertGreaterEqual(again["timings"]["build_ms"], 0)
        # Order and normalization, read from joined pixels.
        joined_path = self._download(joined["url"], "joined.mp4")
        red = self._pixel(joined_path, 1.0, 160, 120)
        self.assertGreater(red[0], 200)
        green = self._pixel(joined_path, 3.0, 160, 120)
        self.assertGreater(green[1], 200)
        blue = self._pixel(joined_path, 5.0, 160, 120)
        self.assertGreater(blue[2], 200)
        # The portrait clip is pillarboxed: frame edges stay black.
        edge = self._pixel(joined_path, 3.0, 5, 120)
        self.assertLess(edge[0], 40)
        self.assertLess(edge[1], 40)
        self.assertLess(edge[2], 40)
        full = self._pixel(joined_path, 1.0, 5, 120)
        self.assertGreater(full[0], 200)
        # Audio order: tones on A/B, silence on C.
        loud = self._max_volume_db(joined_path, 0.2, 1.0)
        quiet = self._max_volume_db(joined_path, 4.5, 1.0)
        self.assertLess(quiet, loud - 10.0)
        # The join_id plans and renders like a single source.
        status, rendered = self._post("/api/render", json.dumps({
            "file_id": joined["join_id"],
            "plan": {"operations": [
                {"op": "trim", "start": 1.0, "end": 5.0},
            ]},
            "instruction": "keep the middle",
        }).encode())
        self.assertEqual(status, 200, rendered)
        self.assertGreaterEqual(rendered["timings"]["ffmpeg_ms"], 0)
        self.assertGreaterEqual(rendered["timings"]["verify_ms"], 0)
        out = self._download(rendered["url"], "joined_cut.mp4")
        info = json.loads(subprocess.run(
            [self.ffprobe, "-v", "error", "-print_format", "json",
             "-show_format", out],
            capture_output=True, text=True, timeout=60, check=True,
        ).stdout)
        self.assertAlmostEqual(
            float(info["format"]["duration"]), 4.0, delta=0.4
        )

    def test_single_clip_needs_no_transcode(self):
        file_id = self._upload(self.clips[0], "solo.mp4")["file_id"]
        status, joined = self._post(
            "/api/join", json.dumps({"file_ids": [file_id]}).encode()
        )
        self.assertEqual(status, 200, joined)
        self.assertEqual(joined["cache"], "single")
        self.assertEqual(joined["join_id"], file_id)
        self.assertGreaterEqual(joined["timings"]["probe_ms"], 0)
        self.assertNotIn("build_ms", joined["timings"])
        self.assertEqual(len(joined["clips"]), 1)
        self.assertEqual(joined["clips"][0]["offset"], 0.0)

    def test_repeated_filenames_stay_distinct(self):
        first = self._upload(self.clips[0], "clip.mp4")["file_id"]
        second = self._upload(self.clips[2], "clip.mp4")["file_id"]
        self.assertNotEqual(first, second)
        status, joined = self._post(
            "/api/join", json.dumps({"file_ids": [first, second]}).encode()
        )
        self.assertEqual(status, 200, joined)
        self.assertEqual(
            [c["name"] for c in joined["clips"]], ["clip.mp4", "clip.mp4"]
        )
        self.assertEqual(
            [c["file_id"] for c in joined["clips"]], [first, second]
        )

    def test_non_video_clip_rejected(self):
        tone = os.path.join(self.tmp, "tone.wav")
        subprocess.run(
            [self.ffmpeg, "-y", "-v", "error",
             "-f", "lavfi", "-i", "sine=frequency=660:duration=1",
             tone],
            check=True, timeout=120,
        )
        file_id = self._upload(tone, "tone.wav", label="asset")["file_id"]
        status, obj = self._post(
            "/api/join", json.dumps({"file_ids": [file_id]}).encode()
        )
        self.assertEqual(status, 400, obj)
        self.assertIn("no video stream", obj.get("error", ""))


if __name__ == "__main__":
    unittest.main()
