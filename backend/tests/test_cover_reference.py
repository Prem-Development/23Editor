"""Route-level regressions for covers and reference analysis.

Covers a real phone failure: NameError on _escape_ass_path (cover) and
MAX_CUTS_REPORTED (reference analysis). The path helper is pure and
tested everywhere; the route tests need system ffmpeg/ffprobe (plus a
Tamil-capable font for the cover title) and loopback for HTTP, and
skip otherwise. Meta is never called. Isolated temp storage only.
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

import analyze
import server as server_module
from ass_subtitles import find_tamil_font
from ffmpeg_render import escape_ass_path
from server import (
    Handler,
    TimeoutHTTPServer,
    route_cover,
    route_reference_analyze,
)


class EscapeAssPathTest(unittest.TestCase):
    def test_drive_letter_and_separators(self):
        self.assertEqual(
            escape_ass_path("C:\\tmp\\subs.ass"),
            "'C\\:/tmp/subs.ass'",
        )

    def test_spaces_survive_inside_quotes(self):
        self.assertEqual(
            escape_ass_path("C:\\my dir\\a b.ass"),
            "'C\\:/my dir/a b.ass'",
        )

    def test_unicode_passes_through(self):
        self.assertEqual(
            escape_ass_path("C:\\\u0bb5\u0bc0\u0b9f\u0bbf\u0baf\u0bcb\\"
                            "\u0ba4\u0bb2\u0bc8\u0baa\u0bcd\u0baa\u0bc1.ass"),
            "'C\\:/\u0bb5\u0bc0\u0b9f\u0bbf\u0baf\u0bcb/"
            "\u0ba4\u0bb2\u0bc8\u0baa\u0bcd\u0baa\u0bc1.ass'",
        )

    def test_single_quote_is_escaped(self):
        self.assertEqual(
            escape_ass_path("C:\\o'clock\\a.ass"),
            "'C\\:/o\\'clock/a.ass'",
        )

    def test_posix_path_passthrough(self):
        self.assertEqual(
            escape_ass_path("/tmp/a.ass"), "'/tmp/a.ass'"
        )

    def test_one_shared_implementation(self):
        self.assertIs(server_module.escape_ass_path, escape_ass_path)


class CoverReferenceRouteTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ffmpeg = shutil.which("ffmpeg")
        cls.ffprobe = shutil.which("ffprobe")
        if not cls.ffmpeg or not cls.ffprobe:
            raise unittest.SkipTest("system ffmpeg/ffprobe not on PATH")
        cls.tmp = tempfile.mkdtemp(prefix="23editor-coverref-")
        cls.source = os.path.join(cls.tmp, "source.mp4")
        subprocess.run(
            [cls.ffmpeg, "-y", "-v", "error",
             "-f", "lavfi", "-i",
             "testsrc=duration=5:size=640x480:rate=10",
             "-f", "lavfi", "-i", "sine=frequency=440:duration=5",
             "-c:v", "libx264", "-pix_fmt", "yuv420p",
             "-c:a", "aac", "-shortest", cls.source],
            check=True, timeout=180,
        )
        # Reference clip with one hard red->blue cut at ~3s.
        cls.ref = os.path.join(cls.tmp, "ref.mp4")
        subprocess.run(
            [cls.ffmpeg, "-y", "-v", "error",
             "-f", "lavfi", "-i",
             "color=red:size=640x360:rate=10:duration=3",
             "-f", "lavfi", "-i",
             "color=blue:size=640x360:rate=10:duration=3",
             "-f", "lavfi", "-i", "sine=frequency=330:duration=6",
             "-filter_complex", "[0][1]concat=n=2:v=1:a=0[v]",
             "-map", "[v]", "-map", "2:a",
             "-c:v", "libx264", "-pix_fmt", "yuv420p",
             "-c:a", "aac", "-shortest", cls.ref],
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

    def _upload(self, path, filename):
        with open(path, "rb") as handle:
            payload = handle.read()
        boundary = "coverreftestboundary"
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

    def _download(self, url, name):
        with urllib.request.urlopen(
            f"http://127.0.0.1:{self.port}{url}", timeout=60
        ) as response:
            data = response.read()
        path = os.path.join(self.tmp, name)
        with open(path, "wb") as handle:
            handle.write(data)
        return path

    def _probe_dims(self, path):
        done = subprocess.run(
            [self.ffprobe, "-v", "error", "-print_format", "json",
             "-show_streams", path],
            capture_output=True, text=True, timeout=60, check=True,
        )
        stream = json.loads(done.stdout)["streams"][0]
        return int(stream["width"]), int(stream["height"])

    def _difference_ssim_all(self, first, second):
        done = subprocess.run(
            [self.ffmpeg, "-v", "info", "-i", first, "-i", second,
             "-filter_complex",
             "[0]format=yuv420p[a];[1]format=yuv420p[b];"
             "[a][b]ssim",
             "-f", "null", "-"],
            capture_output=True, text=True, timeout=120, check=False,
        )
        import re
        for line in done.stderr.splitlines():
            match = re.search(r"SSIM.*All:([0-9.]+)", line)
            if match:
                return float(match.group(1))
        self.fail(f"no ssim score in output: {done.stderr[-300:]}")

    # -- cover --------------------------------------------------------
    def test_cover_with_tamil_english_title(self):
        name, _ = find_tamil_font()
        if name is None:
            raise unittest.SkipTest("no Tamil-capable system font")
        file_id = self._upload(self.source, "source.mp4")
        status, obj = self._post("/api/cover", json.dumps({
            "file_id": file_id, "t": 1.0,
            "title": "My day \u0b8e\u0ba9\u0bcd \u0ba8\u0bbe\u0bb3\u0bcd",
            "position": "bottom",
        }).encode())
        self.assertEqual(status, 200, obj)
        cover_path = self._download(obj["url"], "cover.png")
        self.assertGreater(os.path.getsize(cover_path), 0)
        status, frame = self._post("/api/frame", json.dumps({
            "file_id": file_id, "t": 1.0,
        }).encode())
        self.assertEqual(status, 200, frame)
        frame_path = self._download(frame["url"], "frame.jpg")
        self.assertEqual(
            self._probe_dims(cover_path), self._probe_dims(frame_path)
        )
        # The burned title must change pixels versus the plain
        # frame: identical images score SSIM All == 1.0.
        self.assertLess(
            self._difference_ssim_all(cover_path, frame_path), 1.0
        )

    def test_cover_validation(self):
        file_id = self._upload(self.source, "source.mp4")
        status, _ = self._post("/api/cover", json.dumps({}).encode())
        self.assertEqual(status, 400)
        status, _ = self._post("/api/cover", json.dumps(
            {"file_id": file_id}).encode())
        self.assertEqual(status, 400)
        status, _ = self._post("/api/cover", json.dumps(
            {"file_id": file_id, "title": "Hi",
             "position": "middle"}).encode())
        self.assertEqual(status, 400)

    # -- reference ----------------------------------------------------
    def test_reference_analysis_reports_cuts(self):
        file_id = self._upload(self.ref, "ref.mp4")
        status, obj = self._post("/api/reference/analyze", json.dumps(
            {"file_id": file_id}).encode())
        self.assertEqual(status, 200, obj)
        style = obj["style"]
        self.assertEqual(style["aspect"], "16:9")
        self.assertTrue(style["has_audio"])
        self.assertEqual(style["shots"], len(style["cuts"]) + 1)
        self.assertGreaterEqual(len(style["cuts"]), 1)
        self.assertLessEqual(len(style["cuts"]), 60)
        self.assertEqual(style["cuts"], sorted(style["cuts"]))
        for cut in style["cuts"]:
            self.assertGreater(cut, 0)
            self.assertLess(cut, style["duration"])
        self.assertTrue(
            any(abs(cut - 3.0) < 0.6 for cut in style["cuts"]),
            f"hard cut near 3s missing: {style['cuts']}",
        )
        self.assertEqual(style["fades_to_black"], 0)
        self.assertGreaterEqual(len(style["unsupported"]), 1)
        self.assertIn("brightness", style)
        self.assertIn("saturation", style)
        for key in ("cuts_ms", "color_ms", "fades_ms"):
            self.assertGreaterEqual(obj["timings"][key], 0, obj["timings"])
        transferable = style["transferable"]
        self.assertTrue(
            any("Framing" in item and "16:9" in item
                for item in transferable),
            transferable,
        )
        self.assertTrue(
            any("Pacing" in item for item in transferable),
            transferable,
        )
        self.assertTrue(
            any("Grade" in item for item in transferable),
            transferable,
        )
        self.assertFalse(
            any("Fades" in item for item in transferable),
            transferable,
        )

    def test_frame_color_reads_signalstats_metadata(self):
        png = os.path.join(self.tmp, "color-probe.png")
        analyze.extract_frame(
            self.ffmpeg, self.ref, 1.0, png, width=160)
        sample = analyze.frame_color(self.ffmpeg, png)
        self.assertIsNotNone(sample)
        assert sample is not None
        self.assertGreater(sample["brightness"], 0)
        self.assertLess(sample["brightness"], 255)
        self.assertGreater(sample["saturation"], 0)

    def test_reference_validation(self):
        status, _ = self._post(
            "/api/reference/analyze", json.dumps({}).encode()
        )
        self.assertEqual(status, 400)


class RouteWiringTest(unittest.TestCase):
    """Execute the previously-crashing route lines with ffmpeg stubbed.

    These run everywhere (no toolchain, no sockets) and prove the
    NameErrors are gone; the class above proves real end-to-end
    behavior where ffmpeg exists.
    """

    def test_reference_route_caps_reported_cuts(self):
        media = {"duration": 12.0, "width": 640, "height": 360,
                 "has_audio": True}
        many_cuts = [round(i * 0.1, 2) for i in range(1, 101)]
        with tempfile.TemporaryDirectory(
            prefix="23editor-routewire-"
        ) as tmp:
            storage = server_module.Storage(tmp)
            with mock.patch.object(
                server_module.APP, "storage", storage
            ), mock.patch.object(
                server_module.APP, "media_of",
                return_value=({"path": "ref.mp4"}, media),
            ), mock.patch.object(
                analyze, "scene_cuts", return_value=many_cuts
            ), mock.patch.object(
                analyze, "extract_frame", return_value=None
            ), mock.patch.object(
                analyze, "frame_color",
                return_value={"brightness": 50.0, "saturation": 10.0},
            ), mock.patch.object(
                analyze, "black_fades", return_value=0
            ):
                style = route_reference_analyze(
                    {"file_id": "f1"})["style"]
        self.assertEqual(style["aspect"], "16:9")
        # The cap bounds the reported list; shots still counts every
        # detected cut so totals describe the whole video.
        self.assertEqual(len(style["cuts"]), analyze.MAX_CUTS_REPORTED)
        self.assertEqual(style["cuts"], many_cuts[:60])
        self.assertEqual(style["shots"], 101)

    def test_transferable_traits_gate_on_evidence(self):
        full = server_module._transferable_traits(
            "9:16", 6, 2.0, 1, True)
        self.assertEqual(len(full), 4)
        self.assertTrue(any("9:16" in item for item in full))
        self.assertTrue(any("6 shots" in item for item in full))
        self.assertTrue(any("fade" in item for item in full))
        self.assertTrue(any("Grade" in item for item in full))
        # One static shot, no fades, no color samples: nothing
        # transferable, so the UI says evidence is insufficient.
        self.assertEqual(
            server_module._transferable_traits(
                "original", 1, 12.0, 0, False),
            [],
        )
        # Aspect alone still frames the output.
        aspect_only = server_module._transferable_traits(
            "1:1", 1, 12.0, 0, False)
        self.assertEqual(len(aspect_only), 1)
        self.assertIn("1:1", aspect_only[0])

    def test_cover_route_escapes_ass_filter_path(self):
        media = {"duration": 5.0, "width": 640, "height": 480}
        with tempfile.TemporaryDirectory(
            prefix="23editor-routewire-"
        ) as tmp:
            storage = server_module.Storage(tmp)
            frame_path = os.path.join(tmp, "frame.jpg")
            with open(frame_path, "wb") as handle:
                handle.write(b"fake-frame")
            storage.register("frame1", {"path": frame_path})
            seen_argv: list[list[str]] = []

            def fake_run(argv, **kwargs):
                seen_argv.append(list(argv))
                with open(argv[-1], "wb") as handle:
                    handle.write(b"fake-cover")
                return subprocess.CompletedProcess(
                    argv, 0, stdout="", stderr=""
                )

            with mock.patch.object(
                server_module.APP, "storage", storage
            ), mock.patch.object(
                server_module.APP, "media_of",
                return_value=({"path": "src.mp4"}, media),
            ), mock.patch.object(
                server_module, "route_frame",
                return_value={"image_id": "frame1"},
            ), mock.patch.object(
                subprocess, "run", side_effect=fake_run
            ):
                result = route_cover({
                    "file_id": "f1", "t": 1.0,
                    "title": "My day", "position": "top",
                })
        self.assertIn("image_id", result)
        self.assertEqual(len(seen_argv), 1)
        vf_arg = seen_argv[0][seen_argv[0].index("-vf") + 1]
        self.assertTrue(vf_arg.startswith("ass='"), vf_arg)
        # The temp .ass path must be filtergraph-escaped: no raw
        # backslashes or drive-letter colons may survive.
        self.assertNotIn("\\", vf_arg.replace("\\:", "").replace("\\'", ""))
        if ":" in tmp:
            self.assertIn("\\:", vf_arg)


if __name__ == "__main__":
    unittest.main()
