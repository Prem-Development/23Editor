"""Supporting-video regressions: cutaway/pip/insert validation and rendering.

Pure validation/argv tests run everywhere. The live class needs system
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
from ffmpeg_render import (
    FFmpegError,
    build_render_args,
    expected_output_duration,
)
from plan_schema import MediaInfo, validate_plan
from server import Handler, TimeoutHTTPServer

SUPPORT_ID = "c" * 32
OTHER_SUPPORT_ID = "d" * 32

MEDIA = MediaInfo(
    duration=13.0, width=640, height=360, fps=30.0, has_audio=True
)


def _plan(*ops):
    return {"operations": list(ops)}


def _cutaway(**kw):
    op = {"op": "cutaway", "file_id": SUPPORT_ID, "at": 2.0,
          "duration": 2.0}
    op.update(kw)
    return op


def _pip(**kw):
    op = {"op": "pip", "file_id": SUPPORT_ID, "at": 2.0,
          "duration": 2.0, "position": "top_right"}
    op.update(kw)
    return op


def _insert(**kw):
    op = {"op": "insert", "file_id": SUPPORT_ID, "at": 4.0,
          "duration": 2.0}
    op.update(kw)
    return op


class SupportValidationTest(unittest.TestCase):
    def test_cutaway_pip_insert_validate(self):
        plan = _plan(
            {"op": "trim", "start": 0.0, "end": 10.0},
            _cutaway(),
            _pip(at=6.0),
            _insert(at=8.0),
        )
        checked = validate_plan(plan, MEDIA)
        self.assertTrue(checked.ok, checked.errors)

    def test_insert_extends_expected_duration(self):
        plan = _plan(
            {"op": "trim", "start": 0.0, "end": 5.0},
            _insert(at=5.0, duration=3.0),
        )
        checked = validate_plan(plan, MEDIA)
        self.assertTrue(checked.ok, checked.errors)
        self.assertAlmostEqual(
            expected_output_duration(plan, MEDIA.duration), 8.0
        )

    def test_support_needs_video_id_and_bounded_duration(self):
        bad = _plan(_cutaway(file_id="not-an-id"))
        checked = validate_plan(bad, MEDIA)
        self.assertFalse(checked.ok)
        self.assertTrue(
            any("uploaded video id" in e for e in checked.errors),
            checked.errors,
        )
        for op, low, high in (
            (_cutaway, 0.5, 30.0), (_pip, 0.5, 30.0),
            (_insert, 0.5, 60.0),
        ):
            for duration in (low - 0.1, high + 1.0):
                plan = _plan(op(duration=duration))
                checked = validate_plan(plan, MEDIA)
                self.assertFalse(checked.ok, (op, duration))
                self.assertTrue(
                    any("duration must be within" in e
                        for e in checked.errors),
                    checked.errors,
                )

    def test_audio_modes_are_per_op(self):
        for op, good, bad in (
            (_cutaway, ("mute", "mix", "use"), ("duck",)),
            (_pip, ("mute", "mix"), ("use",)),
            (_insert, ("mute", "use"), ("mix",)),
        ):
            for mode in good:
                checked = validate_plan(_plan(op(audio=mode)), MEDIA)
                self.assertTrue(checked.ok, (op, mode, checked.errors))
            for mode in bad:
                checked = validate_plan(_plan(op(audio=mode)), MEDIA)
                self.assertFalse(checked.ok, (op, mode))
                self.assertTrue(
                    any(".audio must be one of" in e
                        for e in checked.errors),
                    checked.errors,
                )

    def test_pip_position_is_a_corner(self):
        for position in (
            "top_left", "top_right", "bottom_left", "bottom_right"
        ):
            checked = validate_plan(
                _plan(_pip(position=position)), MEDIA
            )
            self.assertTrue(checked.ok, (position, checked.errors))
        for position in ("top", "bottom", "center", "middle"):
            checked = validate_plan(
                _plan(_pip(position=position)), MEDIA
            )
            self.assertFalse(checked.ok, position)
            self.assertTrue(
                any(".position must be one of" in e
                    for e in checked.errors),
                checked.errors,
            )

    def test_insert_rejects_zoom_and_transition(self):
        for other in (
            {"op": "zoom", "start": 0.0, "end": 2.0, "to": 1.5},
            {"op": "transition", "type": "fade", "duration": 0.5},
        ):
            plan = _plan(
                {"op": "keep", "start": 0.0, "end": 5.0},
                {"op": "keep", "start": 6.0, "end": 10.0},
                other,
                _insert(),
            )
            checked = validate_plan(plan, MEDIA)
            self.assertFalse(checked.ok, other)
            self.assertTrue(
                any("insert cannot combine" in e
                    for e in checked.errors),
                checked.errors,
            )

    def test_support_rejects_frame_subject(self):
        for op in (_cutaway(), _pip(), _insert()):
            plan = _plan(
                {"op": "frame_subject", "subject": "speaker",
                 "start": 0.0, "end": 5.0},
                op,
            )
            checked = validate_plan(plan, MEDIA)
            self.assertFalse(checked.ok, op)
            self.assertTrue(
                any("cannot combine with frame_subject" in e
                    for e in checked.errors),
                checked.errors,
            )

    def test_cutaways_must_not_overlap_but_pips_may(self):
        overlapping = _plan(
            _cutaway(at=2.0, duration=2.0),
            _cutaway(at=3.0, duration=2.0),
        )
        checked = validate_plan(overlapping, MEDIA)
        self.assertFalse(checked.ok)
        self.assertTrue(
            any("cutaways must not overlap" in e
                for e in checked.errors),
            checked.errors,
        )
        stacked = _plan(
            _pip(at=2.0, duration=2.0),
            _pip(at=3.0, duration=2.0),
        )
        checked = validate_plan(stacked, MEDIA)
        self.assertTrue(checked.ok, checked.errors)

    def test_insert_at_is_sequential_content_seconds(self):
        # Second `at` counts the first insert's extension.
        plan = _plan(
            {"op": "trim", "start": 0.0, "end": 5.0},
            _insert(at=5.0, duration=2.0),
            _insert(at=7.0, duration=1.0),
        )
        checked = validate_plan(plan, MEDIA)
        self.assertTrue(checked.ok, checked.errors)
        past_end = _plan(
            {"op": "trim", "start": 0.0, "end": 5.0},
            _insert(at=5.1, duration=1.0),
        )
        checked = validate_plan(past_end, MEDIA)
        self.assertFalse(checked.ok)
        self.assertTrue(
            any("past the" in e and "timeline" in e
                for e in checked.errors),
            checked.errors,
        )

    def test_overlay_at_is_output_seconds(self):
        # With a 2s intro + 5s of footage, output second 6.5 is the
        # last footage second: valid in output seconds.
        plan = _plan(
            {"op": "trim", "start": 0.0, "end": 5.0},
            {"op": "intro", "file_id": "e" * 32, "duration": 2.0},
            _cutaway(at=6.5, duration=0.5),
        )
        checked = validate_plan(plan, MEDIA)
        self.assertTrue(checked.ok, checked.errors)
        past_end = _plan(
            {"op": "trim", "start": 0.0, "end": 5.0},
            _cutaway(at=4.6, duration=0.5),
        )
        checked = validate_plan(past_end, MEDIA)
        self.assertFalse(checked.ok)
        self.assertTrue(
            any("output end" in e for e in checked.errors),
            checked.errors,
        )

    def test_support_counts_are_capped(self):
        makers = {"cutaway": _cutaway, "pip": _pip, "insert": _insert}
        for name, make in makers.items():
            ops = [
                make(at=float(i), duration=0.5) for i in range(5)
            ]
            checked = validate_plan(_plan(*ops), MEDIA)
            self.assertFalse(checked.ok, name)
            self.assertTrue(
                any("At most" in e for e in checked.errors),
                checked.errors,
            )


class SupportArgvTest(unittest.TestCase):
    MEDIA = {"duration": 10.0, "width": 640, "height": 360,
             "has_audio": True, "fps": 30.0}

    def _args(self, plan, **kw):
        return build_render_args(
            plan, dict(self.MEDIA), "main.mp4", "out.mp4", None, **kw
        )

    def test_missing_support_file_is_actionable(self):
        plan = _plan(_cutaway())
        with self.assertRaises(FFmpegError) as ctx:
            self._args(plan)
        self.assertIn("supporting video file", str(ctx.exception))

    def test_support_inputs_follow_banner_slots(self):
        plan = _plan(
            {"op": "banner", "file_id": "e" * 32, "start": 0.0,
             "end": 1.0, "position": "top"},
            _cutaway(),
        )
        argv, _ = self._args(
            plan, banner_paths=["banner.png"],
            support_paths={SUPPORT_ID: "support.mp4"},
        )
        inputs = [
            argv[i + 1] for i, a in enumerate(argv) if a == "-i"
        ]
        self.assertEqual(
            inputs, ["main.mp4", "banner.png", "support.mp4"]
        )
        graph = argv[argv.index("-filter_complex") + 1]
        self.assertIn("[2:v]trim=", graph)

    def test_cutaway_window_and_use_audio(self):
        plan = _plan(_cutaway(at=2.0, duration=2.0, audio="use"))
        argv, _ = self._args(
            plan, support_paths={SUPPORT_ID: "support.mp4"}
        )
        graph = argv[argv.index("-filter_complex") + 1]
        self.assertIn("overlay=0:0", graph)
        self.assertIn("between(t,2.000,4.000)", graph)
        # The voice is muted inside the window; the support segment
        # is delayed onto the same output seconds.
        self.assertIn("volume=0:enable='between(t,2.000,4.000)'", graph)
        self.assertIn("adelay=2000:all=1", graph)

    def test_muted_overlay_adds_no_audio(self):
        plan = _plan(_cutaway(at=2.0, duration=2.0, audio="mute"))
        argv, _ = self._args(
            plan, support_paths={SUPPORT_ID: "support.mp4"}
        )
        graph = argv[argv.index("-filter_complex") + 1]
        self.assertNotIn("adelay=", graph)
        self.assertNotIn("volume=0", graph)

    def test_pip_scales_to_corner(self):
        plan = _plan(_pip(at=1.0, duration=2.0,
                          position="bottom_right",
                          width_frac=0.25))
        argv, _ = self._args(
            plan, support_paths={SUPPORT_ID: "support.mp4"}
        )
        graph = argv[argv.index("-filter_complex") + 1]
        self.assertIn("scale=160:-1", graph)
        self.assertIn("overlay=W-w-24:H-h-24", graph)

    def test_insert_interleaves_and_extends(self):
        plan = _plan(
            {"op": "trim", "start": 0.0, "end": 10.0},
            _insert(at=4.0, duration=2.0, audio="use"),
        )
        argv, _ = self._args(
            plan, support_paths={SUPPORT_ID: "support.mp4"}
        )
        graph = argv[argv.index("-filter_complex") + 1]
        self.assertIn("trim=start=0:end=4.000", graph)
        self.assertIn("trim=start=4.000", graph)
        self.assertIn("concat=n=3:v=1:a=0", graph)


class LiveSupportVideoTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ffmpeg = shutil.which("ffmpeg")
        cls.ffprobe = shutil.which("ffprobe")
        if not cls.ffmpeg or not cls.ffprobe:
            raise unittest.SkipTest("system ffmpeg/ffprobe not on PATH")
        cls.tmp = tempfile.mkdtemp(prefix="23editor-support-")
        # 8s main: pattern video + quiet voice-level tone.
        cls.source = os.path.join(cls.tmp, "source.mp4")
        subprocess.run(
            [cls.ffmpeg, "-y", "-v", "error",
             "-f", "lavfi", "-i",
             "testsrc=duration=8:size=320x240:rate=10",
             "-f", "lavfi", "-i",
             "sine=frequency=440:duration=8,volume=0.2",
             "-c:v", "libx264", "-pix_fmt", "yuv420p",
             "-c:a", "aac", "-shortest", cls.source],
            check=True, timeout=180,
        )
        # 5s silent main for the synthesized-audio insert path.
        cls.silent = os.path.join(cls.tmp, "silent.mp4")
        subprocess.run(
            [cls.ffmpeg, "-y", "-v", "error",
             "-f", "lavfi", "-i",
             "testsrc=duration=5:size=320x240:rate=10",
             "-c:v", "libx264", "-pix_fmt", "yuv420p",
             "-an", cls.silent],
            check=True, timeout=120,
        )
        # 5s support: solid red + loud tone.
        cls.support = os.path.join(cls.tmp, "support.mp4")
        subprocess.run(
            [cls.ffmpeg, "-y", "-v", "error",
             "-f", "lavfi", "-i",
             "color=red:size=320x240:rate=10:duration=5",
             "-f", "lavfi", "-i", "sine=frequency=880:duration=5",
             "-c:v", "libx264", "-pix_fmt", "yuv420p",
             "-c:a", "aac", "-shortest", cls.support],
            check=True, timeout=180,
        )
        # 5s silent support for the auto-mute path.
        cls.quiet_support = os.path.join(cls.tmp, "quiet.mp4")
        subprocess.run(
            [cls.ffmpeg, "-y", "-v", "error",
             "-f", "lavfi", "-i",
             "color=blue:size=320x240:rate=10:duration=5",
             "-c:v", "libx264", "-pix_fmt", "yuv420p",
             "-an", cls.quiet_support],
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
        boundary = "supporttestboundary"
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

    def _strong_red(self, pixel):
        red, green, blue = pixel
        return red > 150 and red > green + 60 and red > blue + 60

    # -- tests --------------------------------------------------------
    def test_cutaway_covers_frame_in_window_only(self):
        file_id = self._upload(
            self.source, "source.mp4", label="source")["file_id"]
        support_id = self._upload(
            self.support, "support.mp4")["file_id"]
        plan = {"operations": [
            {"op": "cutaway", "file_id": support_id, "at": 2.0,
             "duration": 2.0},
        ]}
        status, obj = self._post("/api/render", json.dumps({
            "file_id": file_id, "plan": plan,
        }).encode())
        self.assertEqual(status, 200, obj)
        out = self._download(obj["url"], "cutaway.mp4")
        self.assertFalse(
            self._strong_red(self._pixel(out, 1.0, 160, 120)),
            "frame before the window shows the main video",
        )
        self.assertTrue(
            self._strong_red(self._pixel(out, 3.0, 160, 120)),
            "frame inside the window shows the support video",
        )
        self.assertFalse(
            self._strong_red(self._pixel(out, 5.0, 160, 120)),
            "frame after the window shows the main video again",
        )

    def test_pip_covers_corner_in_window_only(self):
        file_id = self._upload(
            self.source, "source.mp4", label="source")["file_id"]
        support_id = self._upload(
            self.support, "support.mp4")["file_id"]
        plan = {"operations": [
            {"op": "pip", "file_id": support_id, "at": 2.0,
             "duration": 2.0, "position": "top_left",
             "width_frac": 0.25},
        ]}
        status, obj = self._post("/api/render", json.dumps({
            "file_id": file_id, "plan": plan,
        }).encode())
        self.assertEqual(status, 200, obj)
        out = self._download(obj["url"], "pip.mp4")
        # Measure a window pixel where the source itself is not red, so
        # the after-window check proves the overlay left (testsrc has
        # red bands and animates, so a fixed pixel cannot assume that).
        spot = None
        for candidate in (
            (40, 40), (60, 40), (40, 60), (60, 60), (80, 50)
        ):
            if not self._strong_red(
                self._pixel(self.source, 5.0, *candidate)
            ):
                spot = candidate
                break
        self.assertIsNotNone(
            spot, "source needs a non-red pixel inside the pip window"
        )
        assert spot is not None
        x, y = spot
        self.assertTrue(
            self._strong_red(self._pixel(out, 3.0, x, y)),
            "corner inside the window shows the support video",
        )
        self.assertFalse(
            self._strong_red(self._pixel(out, 3.0, 280, 200)),
            "far corner still shows the main video",
        )
        got = self._pixel(out, 5.0, x, y)
        want = self._pixel(self.source, 5.0, x, y)
        self.assertFalse(
            self._strong_red(got),
            "corner after the window shows the main video again",
        )
        for got_c, want_c in zip(got, want):
            self.assertLess(
                abs(got_c - want_c), 40,
                f"corner pixel {got} matches the source {want} "
                "after the window",
            )

    def test_insert_extends_timeline_with_support_frames(self):
        file_id = self._upload(
            self.source, "source.mp4", label="source")["file_id"]
        support_id = self._upload(
            self.support, "support.mp4")["file_id"]
        plan = {"operations": [
            {"op": "insert", "file_id": support_id, "at": 4.0,
             "duration": 2.0},
        ]}
        status, obj = self._post("/api/render", json.dumps({
            "file_id": file_id, "plan": plan,
        }).encode())
        self.assertEqual(status, 200, obj)
        out = self._download(obj["url"], "insert.mp4")
        info = self._probe(out)
        duration = float(info["format"]["duration"])
        self.assertAlmostEqual(duration, 10.0, delta=0.4)
        self.assertFalse(
            self._strong_red(self._pixel(out, 2.0, 160, 120)),
            "frame before the insert shows the main video",
        )
        self.assertTrue(
            self._strong_red(self._pixel(out, 5.0, 160, 120)),
            "frame inside the insert shows the support video",
        )
        self.assertFalse(
            self._strong_red(self._pixel(out, 8.0, 160, 120)),
            "frame after the insert shows the main video again",
        )

    def test_cutaway_use_audio_replaces_voice_in_window(self):
        file_id = self._upload(
            self.source, "source.mp4", label="source")["file_id"]
        support_id = self._upload(
            self.support, "support.mp4")["file_id"]
        plan = {"operations": [
            {"op": "cutaway", "file_id": support_id, "at": 4.0,
             "duration": 2.0, "audio": "use"},
        ]}
        status, obj = self._post("/api/render", json.dumps({
            "file_id": file_id, "plan": plan,
        }).encode())
        self.assertEqual(status, 200, obj)
        out = self._download(obj["url"], "useaudio.mp4")
        kinds = {s["codec_type"] for s in self._probe(out)["streams"]}
        self.assertIn("audio", kinds)
        outside = self._max_volume_db(out, 0.5, 2.0)
        inside = self._max_volume_db(out, 4.5, 1.0)
        self.assertGreater(
            inside, outside + 6.0,
            f"loud support ({inside}dB) replaces quiet voice "
            f"({outside}dB) inside the window",
        )

    def test_insert_use_audio_over_silent_main_keeps_sound(self):
        file_id = self._upload(
            self.silent, "silent.mp4", label="source")["file_id"]
        support_id = self._upload(
            self.support, "support.mp4")["file_id"]
        plan = {"operations": [
            {"op": "insert", "file_id": support_id, "at": 2.0,
             "duration": 2.0, "audio": "use"},
        ]}
        status, obj = self._post("/api/render", json.dumps({
            "file_id": file_id, "plan": plan,
        }).encode())
        self.assertEqual(status, 200, obj)
        out = self._download(obj["url"], "silentinsert.mp4")
        info = self._probe(out)
        kinds = {s["codec_type"] for s in info["streams"]}
        self.assertIn("audio", kinds)
        self.assertAlmostEqual(
            float(info["format"]["duration"]), 7.0, delta=0.4
        )
        self.assertFalse(
            self._strong_red(self._pixel(out, 1.0, 160, 120)),
            "frame before the insert shows the main video, "
            "not the support clip",
        )
        self.assertTrue(
            self._strong_red(self._pixel(out, 3.0, 160, 120)),
            "frame inside the insert shows the support video",
        )
        self.assertGreater(
            self._max_volume_db(out, 2.5, 1.0), -30.0,
            "inserted support audio is audible",
        )

    def test_short_support_is_rejected_with_numbers(self):
        file_id = self._upload(
            self.source, "source.mp4", label="source")["file_id"]
        support_id = self._upload(
            self.support, "support.mp4")["file_id"]
        plan = {"operations": [
            {"op": "insert", "file_id": support_id, "at": 2.0,
             "duration": 2.0, "src_start": 4.0},
        ]}
        status, obj = self._post("/api/render", json.dumps({
            "file_id": file_id, "plan": plan,
        }).encode())
        self.assertEqual(status, 400, obj)
        self.assertIn("needs", obj.get("error", ""))

    def test_silent_support_is_muted_with_warning(self):
        file_id = self._upload(
            self.source, "source.mp4", label="source")["file_id"]
        support_id = self._upload(
            self.quiet_support, "quiet.mp4")["file_id"]
        plan = {"operations": [
            {"op": "cutaway", "file_id": support_id, "at": 2.0,
             "duration": 2.0, "audio": "use"},
        ]}
        status, obj = self._post("/api/render", json.dumps({
            "file_id": file_id, "plan": plan,
        }).encode())
        self.assertEqual(status, 200, obj)
        self.assertTrue(
            any("muted" in w for w in obj.get("warnings", [])),
            obj.get("warnings"),
        )


if __name__ == "__main__":
    unittest.main()
