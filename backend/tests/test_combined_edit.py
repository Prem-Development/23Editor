"""Combined-edit proof: one render with keeps, title, zoom, captions,
music, sfx, banner, and intro — verified by frames and audio, not just
duration.

The plan is shaped exactly as the app builds it: planner cutting ops
plus control-origin extras (edited caption lines, music, sfx, intro,
banner) appended at render time. Four renders isolate each effect:
the full edit, one without visual extras, one without music, and one
without the sfx. Needs system ffmpeg/ffprobe, a Tamil-capable font,
and loopback; skips otherwise. Meta is never called (explicit caption
segments, no transcription). Isolated temp storage only.
"""
import hashlib
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
from ass_subtitles import find_tamil_font
from server import Handler, TimeoutHTTPServer

TITLE = "My day \u0b8e\u0ba9\u0bcd \u0ba8\u0bbe\u0bb3\u0bcd"
CAPTION_TAMIL = "hello \u0bb5\u0ba3\u0b95\u0bcd\u0b95\u0bae\u0bcd"


def _md5(path):
    digest = hashlib.md5()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class CombinedEditLiveTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ffmpeg = shutil.which("ffmpeg")
        cls.ffprobe = shutil.which("ffprobe")
        if not cls.ffmpeg or not cls.ffprobe:
            raise unittest.SkipTest("system ffmpeg/ffprobe not on PATH")
        name, _ = find_tamil_font()
        if name is None:
            raise unittest.SkipTest("no Tamil-capable system font")
        cls.tmp = tempfile.mkdtemp(prefix="23editor-combined-")
        # 13s talking-style source: pattern video + steady tone voice.
        cls.source = os.path.join(cls.tmp, "source.mp4")
        subprocess.run(
            [cls.ffmpeg, "-y", "-v", "error",
             "-f", "lavfi", "-i",
             "testsrc=duration=13:size=640x360:rate=10",
             "-f", "lavfi", "-i", "sine=frequency=440:duration=13",
             "-c:v", "libx264", "-pix_fmt", "yuv420p",
             "-c:a", "aac", "-shortest", cls.source],
            check=True, timeout=240,
        )
        cls.source_md5 = _md5(cls.source)
        cls.music_wav = os.path.join(cls.tmp, "music.wav")
        subprocess.run(
            [cls.ffmpeg, "-y", "-v", "error",
             "-f", "lavfi", "-i", "sine=frequency=880:duration=13",
             cls.music_wav],
            check=True, timeout=120,
        )
        cls.intro_png = os.path.join(cls.tmp, "intro.png")
        cls.banner_png = os.path.join(cls.tmp, "banner.png")
        subprocess.run(
            [cls.ffmpeg, "-y", "-v", "error",
             "-f", "lavfi", "-i", "color=red:size=640x360:rate=1",
             "-frames:v", "1", cls.intro_png],
            check=True, timeout=120,
        )
        subprocess.run(
            [cls.ffmpeg, "-y", "-v", "error",
             "-f", "lavfi", "-i", "color=green:size=160x120:rate=1",
             "-frames:v", "1", cls.banner_png],
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
            with urllib.request.urlopen(request, timeout=600) as response:
                return response.status, json.loads(response.read() or b"{}")
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read() or b"{}")

    def _upload(self, path, filename, label="asset"):
        with open(path, "rb") as handle:
            payload = handle.read()
        boundary = "combinedtestboundary"
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
        return obj["file_id"]

    def _render(self, file_id, operations, tag):
        status, obj = self._post("/api/render", json.dumps({
            "file_id": file_id,
            "plan": {"operations": operations},
            "instruction": "make a highlight reel of the good parts",
        }).encode())
        self.assertEqual(status, 200, obj)
        with urllib.request.urlopen(
            f"http://127.0.0.1:{self.port}{obj['url']}", timeout=120
        ) as response:
            data = response.read()
        path = os.path.join(self.tmp, f"{tag}.mp4")
        with open(path, "wb") as handle:
            handle.write(data)
        return path, obj

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

    def _extract_audio_md5(self, video, start, length, name):
        dest = os.path.join(self.tmp, name)
        subprocess.run(
            [self.ffmpeg, "-y", "-v", "error", "-ss", f"{start:.2f}",
             "-t", f"{length:.2f}", "-i", video, "-vn",
             "-c:a", "pcm_s16le", dest],
            check=True, timeout=120,
        )
        return _md5(dest)

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

    # -- the combined scenario ----------------------------------------
    def test_combined_edit_end_to_end(self):
        file_id = self._upload(self.source, "source.mp4", label="source")
        music_id = self._upload(self.music_wav, "music.wav")
        intro_id = self._upload(self.intro_png, "intro.png")
        banner_id = self._upload(self.banner_png, "banner.png")

        # Output timeline: 2s intro, then 10s of kept footage
        # (source 0-4, then 7-13). Timestamps below are output seconds
        # unless noted as source seconds (echoing the app's labels).
        keeps = [
            {"op": "keep", "origin": "control", "start": 0.0, "end": 4.0},
            {"op": "keep", "origin": "control", "start": 7.0, "end": 13.0},
        ]
        title = {"op": "text", "origin": "instruction", "text": TITLE,
                 "start": 0.0, "end": 2.0, "position": "top",
                 "animation": "fade", "size": "large"}
        zoom = {"op": "zoom", "origin": "instruction", "start": 7.2,
                "end": 8.0, "to": 1.6}
        captions = {"op": "captions", "origin": "control", "segments": [
            {"start": 0.5, "end": 2.0, "text": CAPTION_TAMIL},
            {"start": 8.0, "end": 10.0, "text": "second part"},
        ]}
        music = {"op": "music", "origin": "control", "file_id": music_id,
                 "volume_db": -12.0, "duck": True}
        sfx = {"op": "sfx", "origin": "control", "asset": "pop", "at": 3.0}
        banner = {"op": "banner", "origin": "control", "file_id": banner_id,
                  "start": 9.5, "end": 11.0, "position": "bottom"}
        intro = {"op": "intro", "origin": "control", "file_id": intro_id,
                 "duration": 2.0}
        full = keeps + [title, zoom, captions, music, sfx, banner, intro]
        bare_visual = keeps + [music, sfx, intro]
        no_music = [op for op in full if op.get("op") != "music"]
        no_sfx = [op for op in full if op.get("op") != "sfx"]

        combined, answer = self._render(file_id, full, "combined")
        info = self._probe(combined)
        kinds = {s["codec_type"] for s in info["streams"]}
        self.assertEqual(kinds, {"video", "audio"})
        video = next(
            s for s in info["streams"] if s["codec_type"] == "video"
        )
        audio = next(
            s for s in info["streams"] if s["codec_type"] == "audio"
        )
        self.assertEqual((video["width"], video["height"]), (640, 360))
        duration = float(info["format"]["duration"])
        self.assertGreater(duration, 11.4)
        self.assertLess(duration, 12.6)
        self.assertAlmostEqual(
            float(video.get("duration", duration)),
            float(audio.get("duration", duration)),
            delta=0.6,
        )
        self.assertAlmostEqual(
            float(answer["output"]["duration"]), duration, places=1
        )

        # The intro shows the still, not footage.
        intro_frame = self._extract_frame(combined, 1.0, "f_intro.png")
        self.assertGreater(
            self._ssim_all(intro_frame, self.intro_png), 0.95
        )

        # Each visual effect is isolated by timestamp against the same
        # render without visual extras: title+caption at 3.0s (output),
        # zoom alone at 6.5s, banner alone at 10.0s.
        bare, _ = self._render(file_id, bare_visual, "combined_bare")
        for at, name in (
            (3.0, "title_caption"),
            (6.5, "zoom"),
            (10.0, "banner"),
        ):
            with self.subTest(effect=name):
                on = self._extract_frame(combined, at, f"f_{name}_on.png")
                off = self._extract_frame(bare, at, f"f_{name}_off.png")
                self.assertLess(self._ssim_all(on, off), 1.0)

        # Music and the sfx each change the mixed audio.
        musicless, _ = self._render(file_id, no_music, "combined_nomusic")
        self.assertNotEqual(
            self._extract_audio_md5(combined, 8.0, 1.0, "a_music.wav"),
            self._extract_audio_md5(musicless, 8.0, 1.0, "a_nomusic.wav"),
        )
        sfxless, _ = self._render(file_id, no_sfx, "combined_nosfx")
        self.assertNotEqual(
            self._extract_audio_md5(combined, 3.0, 0.4, "a_sfx.wav"),
            self._extract_audio_md5(sfxless, 3.0, 0.4, "a_nosfx.wav"),
        )

        # Export round-trip: the downloaded bytes reopen as the same
        # verified output, and the source is untouched.
        reopened = self._probe(combined)
        self.assertEqual(
            {s["codec_type"] for s in reopened["streams"]},
            {"video", "audio"},
        )
        self.assertEqual(self.source_md5, _md5(self.source))


if __name__ == "__main__":
    unittest.main()
