"""Image-attachment regressions: intro/outro/banner validation and rendering.

Pure validation tests run everywhere. The live class needs system
ffmpeg/ffprobe plus loopback for HTTP, and skips otherwise. Meta is
never called. Isolated temp storage only.
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

import server as server_module
from ffmpeg_render import expected_output_duration
from plan_schema import MediaInfo, validate_plan
from server import Handler, TimeoutHTTPServer

IMAGE_ID = "a" * 32
OTHER_IMAGE_ID = "b" * 32

MEDIA = MediaInfo(
    duration=13.0, width=640, height=360, fps=30.0, has_audio=True
)


def _plan(*ops):
    return {"operations": list(ops)}


class AttachmentValidationTest(unittest.TestCase):
    def test_intro_outro_extend_expected_duration(self):
        plan = _plan(
            {"op": "trim", "start": 0.0, "end": 5.0},
            {"op": "intro", "file_id": IMAGE_ID, "duration": 2.0},
            {"op": "outro", "file_id": OTHER_IMAGE_ID, "duration": 1.0},
        )
        checked = validate_plan(plan, MEDIA)
        self.assertTrue(checked.ok, checked.errors)
        self.assertAlmostEqual(
            expected_output_duration(plan, MEDIA.duration), 8.0
        )

    def test_intro_needs_image_id_and_bounded_duration(self):
        bad_id = _plan(
            {"op": "trim", "start": 0.0, "end": 5.0},
            {"op": "intro", "file_id": "not-an-id", "duration": 2.0},
        )
        checked = validate_plan(bad_id, MEDIA)
        self.assertFalse(checked.ok)
        self.assertTrue(
            any("uploaded image id" in e for e in checked.errors),
            checked.errors,
        )
        for duration in (0.2, 10.5):
            plan = _plan(
                {"op": "trim", "start": 0.0, "end": 5.0},
                {"op": "intro", "file_id": IMAGE_ID, "duration": duration},
            )
            checked = validate_plan(plan, MEDIA)
            self.assertFalse(checked.ok, duration)
            self.assertTrue(
                any("duration must be within" in e
                    for e in checked.errors),
                checked.errors,
            )

    def test_only_one_intro_and_one_outro(self):
        plan = _plan(
            {"op": "trim", "start": 0.0, "end": 5.0},
            {"op": "intro", "file_id": IMAGE_ID, "duration": 2.0},
            {"op": "intro", "file_id": OTHER_IMAGE_ID, "duration": 2.0},
        )
        checked = validate_plan(plan, MEDIA)
        self.assertFalse(checked.ok)
        self.assertIn("Only one intro is supported.", checked.errors)

    def test_banner_uses_output_seconds_including_intro(self):
        # Content is 5s, intro adds 2s: banner 5.0-6.5s sits on footage
        # (output seconds), which source-second validation would reject.
        plan = _plan(
            {"op": "trim", "start": 0.0, "end": 5.0},
            {"op": "intro", "file_id": IMAGE_ID, "duration": 2.0},
            {"op": "banner", "file_id": IMAGE_ID, "start": 5.0,
             "end": 6.5, "position": "bottom"},
        )
        checked = validate_plan(plan, MEDIA)
        self.assertTrue(checked.ok, checked.errors)
        assert checked.plan is not None
        banner = next(
            o for o in checked.plan["operations"] if o["op"] == "banner"
        )
        self.assertEqual(banner["width_frac"], 0.4)

    def test_banner_past_final_end_is_rejected(self):
        plan = _plan(
            {"op": "trim", "start": 0.0, "end": 5.0},
            {"op": "banner", "file_id": IMAGE_ID, "start": 4.0,
             "end": 5.5, "position": "bottom"},
        )
        checked = validate_plan(plan, MEDIA)
        self.assertFalse(checked.ok)
        self.assertTrue(
            any("past the 5.0s output end" in e for e in checked.errors),
            checked.errors,
        )
        late = _plan(
            {"op": "trim", "start": 0.0, "end": 5.0},
            {"op": "banner", "file_id": IMAGE_ID, "start": 5.0,
             "end": 5.5, "position": "bottom"},
        )
        checked = validate_plan(late, MEDIA)
        self.assertFalse(checked.ok)
        self.assertTrue(
            any("at or past the 5.0s output end" in e
                for e in checked.errors),
            checked.errors,
        )

    def test_banner_rejects_bad_position_and_size(self):
        plan = _plan(
            {"op": "trim", "start": 0.0, "end": 5.0},
            {"op": "banner", "file_id": IMAGE_ID, "start": 1.0,
             "end": 2.0, "position": "middle", "width_frac": 2.0},
        )
        checked = validate_plan(plan, MEDIA)
        self.assertFalse(checked.ok)
        self.assertTrue(
            any("position must be one of" in e for e in checked.errors),
            checked.errors,
        )
        self.assertTrue(
            any("width_frac must be within" in e for e in checked.errors),
            checked.errors,
        )

    def test_at_most_two_banners(self):
        plan = _plan(
            {"op": "trim", "start": 0.0, "end": 13.0},
            *[
                {"op": "banner", "file_id": IMAGE_ID,
                 "start": float(i), "end": float(i + 1),
                 "position": "top"}
                for i in range(3)
            ],
        )
        checked = validate_plan(plan, MEDIA)
        self.assertFalse(checked.ok)
        self.assertIn("At most 2 banners are supported.", checked.errors)

    def test_sfx_bound_includes_stills(self):
        # Final span is 2s intro + 5s footage: 6.0s is audible there.
        plan = _plan(
            {"op": "trim", "start": 0.0, "end": 5.0},
            {"op": "intro", "file_id": IMAGE_ID, "duration": 2.0},
            {"op": "sfx", "asset": "pop", "at": 6.0},
        )
        checked = validate_plan(plan, MEDIA)
        self.assertTrue(checked.ok, checked.errors)
        past_end = _plan(
            {"op": "trim", "start": 0.0, "end": 5.0},
            {"op": "intro", "file_id": IMAGE_ID, "duration": 2.0},
            {"op": "sfx", "asset": "pop", "at": 7.0},
        )
        checked = validate_plan(past_end, MEDIA)
        self.assertFalse(checked.ok)
        self.assertTrue(
            any("7.0s output end" in e for e in checked.errors),
            checked.errors,
        )

    def test_stills_and_banner_reject_frame_subject(self):
        plan = _plan(
            {"op": "frame_subject", "subject": "dog",
             "start": 0.0, "end": 13.0},
            {"op": "banner", "file_id": IMAGE_ID, "start": 1.0,
             "end": 2.0, "position": "top"},
        )
        checked = validate_plan(plan, MEDIA)
        self.assertFalse(checked.ok)
        self.assertTrue(
            any("frame_subject" in e for e in checked.errors),
            checked.errors,
        )


class AttachmentLiveTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ffmpeg = shutil.which("ffmpeg")
        cls.ffprobe = shutil.which("ffprobe")
        if not cls.ffmpeg or not cls.ffprobe:
            raise unittest.SkipTest("system ffmpeg/ffprobe not on PATH")
        cls.tmp = tempfile.mkdtemp(prefix="23editor-attach-")
        cls.source = os.path.join(cls.tmp, "source.mp4")
        subprocess.run(
            [cls.ffmpeg, "-y", "-v", "error",
             "-f", "lavfi", "-i",
             "testsrc=duration=6:size=320x240:rate=10",
             "-f", "lavfi", "-i", "sine=frequency=440:duration=6",
             "-c:v", "libx264", "-pix_fmt", "yuv420p",
             "-c:a", "aac", "-shortest", cls.source],
            check=True, timeout=180,
        )
        cls.tone_wav = os.path.join(cls.tmp, "tone.wav")
        subprocess.run(
            [cls.ffmpeg, "-y", "-v", "error",
             "-f", "lavfi", "-i", "sine=frequency=660:duration=2",
             cls.tone_wav],
            check=True, timeout=120,
        )
        cls.intro_png = os.path.join(cls.tmp, "intro.png")
        cls.outro_png = os.path.join(cls.tmp, "outro.png")
        cls.banner_png = os.path.join(cls.tmp, "banner.png")
        cls.banner_b_png = os.path.join(cls.tmp, "banner_b.png")
        for path, color, size in (
            (cls.intro_png, "red", "320x240"),
            (cls.outro_png, "blue", "320x240"),
            (cls.banner_png, "lime", "160x120"),
            (cls.banner_b_png, "magenta", "160x120"),
        ):
            subprocess.run(
                [cls.ffmpeg, "-y", "-v", "error",
                 "-f", "lavfi", "-i", f"color={color}:size={size}:rate=1",
                 "-frames:v", "1", path],
                check=True, timeout=120,
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
    def _post(self, path, body, content_type="application/json"):
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}", data=body, method="POST",
            headers={"Content-Type": content_type},
        )
        try:
            with urllib.request.urlopen(request, timeout=240) as response:
                return response.status, json.loads(response.read() or b"{}")
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read() or b"{}")

    def _upload(self, path, filename, label="asset"):
        with open(path, "rb") as handle:
            payload = handle.read()
        boundary = "attachtestboundary"
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
            capture_output=True, text=True, timeout=60, check=True,
        )
        return json.loads(done.stdout)

    def _extract_frame(self, video, at, name):
        dest = os.path.join(self.tmp, name)
        subprocess.run(
            [self.ffmpeg, "-y", "-v", "error", "-ss", f"{at:.2f}",
             "-i", video, "-frames:v", "1", dest],
            check=True, timeout=120,
        )
        return dest

    def _ssim_all(self, first, second):
        done = subprocess.run(
            [self.ffmpeg, "-v", "info", "-i", first, "-i", second,
             "-filter_complex",
             "[0]format=yuv420p[a];[1]format=yuv420p[b];[a][b]ssim",
             "-f", "null", "-"],
            capture_output=True, text=True, timeout=120, check=False,
        )
        for line in done.stderr.splitlines():
            match = re.search(r"SSIM.*All:([0-9.]+)", line)
            if match:
                return float(match.group(1))
        self.fail(f"no ssim score in output: {done.stderr[-300:]}")

    def _pixel(self, video, at, x, y):
        """RGB tuple of one pixel of the frame nearest `at` seconds."""
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
        return -100.0  # digital silence reports no numeric peak

    # -- tests --------------------------------------------------------
    def test_image_upload_reports_dimensions(self):
        obj = self._upload(self.banner_png, "banner.png")
        self.assertEqual(obj["label"], "asset")
        self.assertEqual(obj["media"]["width"], 160)
        self.assertEqual(obj["media"]["height"], 120)
        self.assertFalse(obj["media"]["has_audio"])

    def test_render_intro_banner_outro(self):
        file_id = self._upload(self.source, "source.mp4",
                               label="source")["file_id"]
        intro_id = self._upload(self.intro_png, "intro.png")["file_id"]
        outro_id = self._upload(self.outro_png, "outro.png")["file_id"]
        banner_id = self._upload(self.banner_png, "banner.png")["file_id"]
        banner_op = {"op": "banner", "file_id": banner_id, "start": 3.0,
                     "end": 5.0, "position": "bottom"}
        plan = {"operations": [
            {"op": "trim", "start": 0.0, "end": 5.0},
            {"op": "intro", "file_id": intro_id, "duration": 2.0},
            {"op": "outro", "file_id": outro_id, "duration": 1.0},
            banner_op,
        ]}
        status, obj = self._post("/api/render", json.dumps({
            "file_id": file_id, "plan": plan,
            "instruction": "keep only the first 5 seconds",
        }).encode())
        self.assertEqual(status, 200, obj)
        out = self._download(obj["url"], "attached.mp4")
        info = self._probe(out)
        kinds = {s["codec_type"] for s in info["streams"]}
        self.assertEqual(kinds, {"video", "audio"})
        video = next(
            s for s in info["streams"] if s["codec_type"] == "video"
        )
        self.assertEqual((video["width"], video["height"]), (320, 240))
        duration = float(info["format"]["duration"])
        self.assertGreater(duration, 7.4)
        self.assertLess(duration, 8.6)
        # Intro frames show the red still, not footage.
        intro_frame = self._extract_frame(out, 1.0, "intro_frame.png")
        self.assertGreater(self._ssim_all(intro_frame, self.intro_png), 0.95)
        # Outro frames show the blue still.
        outro_frame = self._extract_frame(out, 7.5, "outro_frame.png")
        self.assertGreater(self._ssim_all(outro_frame, self.outro_png), 0.95)
        # The banner changes pixels versus the same render bare.
        bare_plan = {"operations": [
            {"op": "trim", "start": 0.0, "end": 5.0},
            {"op": "intro", "file_id": intro_id, "duration": 2.0},
            {"op": "outro", "file_id": outro_id, "duration": 1.0},
        ]}
        status, bare = self._post("/api/render", json.dumps({
            "file_id": file_id, "plan": bare_plan,
            "instruction": "keep only the first 5 seconds",
        }).encode())
        self.assertEqual(status, 200, bare)
        bare_out = self._download(bare["url"], "bare.mp4")
        with_banner = self._extract_frame(out, 4.0, "banner_on.png")
        without_banner = self._extract_frame(bare_out, 4.0, "banner_off.png")
        self.assertLess(
            self._ssim_all(with_banner, without_banner), 1.0
        )
        # Voice stays with its footage: the intro span is near-silent
        # while the footage span carries the tone.
        intro_db = self._max_volume_db(out, 0.0, 1.5)
        voice_db = self._max_volume_db(out, 2.5, 1.5)
        self.assertLess(intro_db, voice_db - 10.0)

    def test_banner_timing_and_photo_replace(self):
        file_id = self._upload(self.source, "source.mp4",
                               label="source")["file_id"]
        green_id = self._upload(self.banner_png, "banner.png")["file_id"]
        magenta_id = self._upload(
            self.banner_b_png, "banner_b.png")["file_id"]

        def render_with(banner_id, name):
            status, obj = self._post("/api/render", json.dumps({
                "file_id": file_id,
                "plan": {"operations": [
                    {"op": "trim", "start": 0.0, "end": 5.0},
                    {"op": "banner", "file_id": banner_id,
                     "start": 1.0, "end": 4.0, "position": "bottom"},
                ]},
                "instruction": "keep only the first 5 seconds",
            }).encode())
            self.assertEqual(status, 200, obj)
            return self._download(obj["url"], name)

        first = render_with(green_id, "banner_a.mp4")
        # The bottom banner covers x 96..224, y 104..200 (a 40px safe
        # margin stays clear): measure a pixel inside it where the
        # source itself is not banner-green, so the before/after
        # checks prove the timing instead of the test pattern.
        spot = None
        for candidate in (
            (160, 150), (130, 140), (190, 160), (110, 125), (210, 180)
        ):
            before_src = self._pixel(self.source, 0.5, *candidate)
            after_src = self._pixel(self.source, 4.5, *candidate)
            if not any(
                px[1] > 200 and px[0] < 80 and px[2] < 80
                for px in (before_src, after_src)
            ):
                spot = candidate
                break
        self.assertIsNotNone(
            spot, "source needs a non-green pixel inside the banner"
        )
        assert spot is not None
        x, y = spot
        before = self._pixel(first, 0.5, x, y)
        during = self._pixel(first, 2.5, x, y)
        after = self._pixel(first, 4.5, x, y)
        self.assertGreater(during[1], 200)
        self.assertLess(during[0], 80)
        self.assertLess(during[2], 80)
        for frame in (before, after):
            self.assertFalse(
                frame[1] > 200 and frame[0] < 80 and frame[2] < 80,
                f"banner visible outside its interval: {frame}",
            )
        # Replacing photo A with photo B changes the export to B.
        second = render_with(magenta_id, "banner_b.mp4")
        replaced = self._pixel(second, 2.5, x, y)
        self.assertGreater(replaced[0], 200)
        self.assertLess(replaced[1], 80)
        self.assertGreater(replaced[2], 200)

    def test_banner_rejects_non_image_file(self):
        file_id = self._upload(self.source, "source.mp4",
                               label="source")["file_id"]
        audio_id = self._upload(self.tone_wav, "tone.wav")["file_id"]
        status, obj = self._post("/api/render", json.dumps({
            "file_id": file_id,
            "plan": {"operations": [
                {"op": "trim", "start": 0.0, "end": 5.0},
                {"op": "banner", "file_id": audio_id, "start": 1.0,
                 "end": 2.0, "position": "top"},
            ]},
            "instruction": "keep only the first 5 seconds",
        }).encode())
        self.assertEqual(status, 400, obj)
        self.assertIn("No image found", obj.get("error", ""))


if __name__ == "__main__":
    unittest.main()
