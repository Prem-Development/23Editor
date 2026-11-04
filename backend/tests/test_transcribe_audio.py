"""Transcription audio-requirement regression tests.

Two layers:
- ProbeAudioTest pins stream-selection logic with fixture ffprobe JSON
  (patched _run; no binaries, runs everywhere).
- LiveTranscribeAudioTest drives the real HTTP upload + transcribe routes
  against real synthesized media. It skips cleanly without system
  ffmpeg/ffprobe, without listening sockets, or without a configured key.
  The Meta call itself is captured, never sent: zero spend.
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
from ffmpeg_render import (
    FFmpegError,
    probe_audio,
    probe_media,
)
from server import Handler, TimeoutHTTPServer

BOTH = {
    "streams": [
        {"codec_type": "video", "codec_name": "hevc", "width": 1920,
         "height": 1080, "r_frame_rate": "30/1", "duration": "13.640000"},
        {"codec_type": "audio", "codec_name": "aac", "duration": "13.670000",
         "sample_rate": "44100", "channels": 2},
    ],
    "format": {"duration": "13.670000"},
}
AUDIO_ONLY = {
    "streams": [
        {"codec_type": "audio", "codec_name": "aac", "duration": "7.730000",
         "sample_rate": "44100", "channels": 1},
    ],
    "format": {"duration": "7.730000"},
}
VIDEO_ONLY = {
    "streams": [
        {"codec_type": "video", "codec_name": "h264", "width": 640,
         "height": 480, "r_frame_rate": "30/1", "duration": "5.000000"},
    ],
    "format": {"duration": "5.000000"},
}


def _completed(info):
    return subprocess.CompletedProcess(
        args=["ffprobe"], returncode=0,
        stdout=json.dumps(info), stderr="",
    )


class ProbeAudioTest(unittest.TestCase):
    def test_video_with_audio_probes_audio(self):
        with mock.patch("ffmpeg_render._run", return_value=_completed(BOTH)):
            audio = probe_audio("ffprobe", "clip.mp4")
        self.assertAlmostEqual(audio["duration"], 13.67, places=2)
        self.assertEqual(audio["audio_codec"], "aac")
        self.assertTrue(audio["has_audio"])

    def test_audio_only_probes_audio(self):
        with mock.patch(
            "ffmpeg_render._run", return_value=_completed(AUDIO_ONLY)
        ):
            audio = probe_audio("ffprobe", "voice.m4a")
        self.assertAlmostEqual(audio["duration"], 7.73, places=2)

    def test_video_without_audio_reports_no_audio(self):
        with mock.patch(
            "ffmpeg_render._run", return_value=_completed(VIDEO_ONLY)
        ):
            with self.assertRaises(FFmpegError) as ctx:
                probe_audio("ffprobe", "silent.mp4")
        self.assertEqual(str(ctx.exception), "No audio stream found.")

    def test_video_probe_still_requires_video(self):
        # Render/plan paths intentionally keep the video requirement.
        with mock.patch(
            "ffmpeg_render._run", return_value=_completed(AUDIO_ONLY)
        ):
            with self.assertRaises(FFmpegError) as ctx:
                probe_media("ffprobe", "voice.m4a")
        self.assertEqual(str(ctx.exception), "No video stream found.")

    def test_tool_failure_still_reports_tool_error(self):
        failed = subprocess.CompletedProcess(
            args=["ffprobe"], returncode=1, stdout="", stderr="boom"
        )
        with mock.patch("ffmpeg_render._run", return_value=failed):
            with self.assertRaises(FFmpegError) as ctx:
                probe_audio("ffprobe", "x.mp4")
        self.assertIn("ffprobe failed", str(ctx.exception))


class LiveTranscribeAudioTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ffmpeg = shutil.which("ffmpeg")
        cls.ffprobe = shutil.which("ffprobe")
        if not cls.ffmpeg or not cls.ffprobe:
            raise unittest.SkipTest("system ffmpeg/ffprobe not on PATH")
        if not server_module.APP.settings.has_api_key:
            raise unittest.SkipTest("no API key configured in backend/.env")
        cls.tmp = tempfile.mkdtemp(prefix="23editor-av-")
        cls.media = os.path.join(cls.tmp, "media")
        os.makedirs(cls.media)
        both = os.path.join(cls.media, "both.mp4")
        silent = os.path.join(cls.media, "silent.mp4")
        voice = os.path.join(cls.media, "voice.m4a")
        subprocess.run(
            [cls.ffmpeg, "-y", "-v", "error",
             "-f", "lavfi", "-i", "testsrc=duration=2:size=320x240:rate=10",
             "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
             "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
             "-shortest", both],
            check=True, timeout=120,
        )
        subprocess.run(
            [cls.ffmpeg, "-y", "-v", "error",
             "-f", "lavfi", "-i", "testsrc=duration=2:size=320x240:rate=10",
             "-c:v", "libx264", "-pix_fmt", "yuv420p", "-an", silent],
            check=True, timeout=120,
        )
        subprocess.run(
            [cls.ffmpeg, "-y", "-v", "error",
             "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
             "-c:a", "aac", voice],
            check=True, timeout=120,
        )
        cls.files = {"both": both, "silent": silent, "voice": voice}
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
            with urllib.request.urlopen(request, timeout=30) as response:
                return response.status, json.loads(response.read() or b"{}")
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read() or b"{}")

    def _upload(self, path, filename):
        with open(path, "rb") as handle:
            payload = handle.read()
        boundary = "avtestboundary"
        body = (
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="file"; '
            f'filename="{filename}"\r\n'
            "Content-Type: application/octet-stream\r\n\r\n"
        ).encode("latin-1") + payload + f"\r\n--{boundary}--\r\n".encode()
        return self._post(
            "/api/upload", body, f"multipart/form-data; boundary={boundary}"
        )

    def _transcribe(self, file_id, mode="ENDPOINTING"):
        return self._post(
            "/api/transcribe",
            json.dumps({"file_id": file_id, "mode": mode}).encode(),
            "application/json",
        )

    # -- tests ----------------------------------------------------------
    def test_video_with_audio_transcribes(self):
        status, obj = self._upload(self.files["both"], "both.mp4")
        self.assertEqual(status, 200, obj)
        captured = {}

        def fake_transcribe(wav_bytes, request_part, session_id=None):
            captured["wav"] = wav_bytes
            captured["request"] = request_part
            return {"transcript": "hello world", "turns": []}

        with mock.patch.object(
            server_module.MetaClient, "transcribe",
            side_effect=fake_transcribe,
        ):
            status, obj = self._transcribe(obj["file_id"])
        self.assertEqual(status, 200, obj)
        self.assertNotIn("No video stream found", json.dumps(obj))
        self.assertEqual(obj.get("text"), "hello world")
        for key in ("convert_ms", "classify_ms", "stt_ms"):
            self.assertGreaterEqual(obj["timings"][key], 0, obj["timings"])
        # Real ffmpeg conversion reached the (captured) Meta call.
        self.assertTrue(captured["wav"].startswith(b"RIFF"))
        self.assertEqual(captured["request"]["audioEncoding"], "WAV")
        self.assertEqual(
            captured["request"]["model"], "muse-voice-transcribe-1.0"
        )

    def test_video_without_audio_is_rejected_clearly(self):
        status, obj = self._upload(self.files["silent"], "silent.mp4")
        self.assertEqual(status, 200, obj)
        with mock.patch.object(
            server_module.MetaClient, "transcribe",
            side_effect=AssertionError("must not reach Meta"),
        ):
            status, obj = self._transcribe(obj["file_id"])
        self.assertEqual(status, 400)
        self.assertEqual(obj.get("error"), "No audio stream found.")

    def test_audio_only_voice_clip_transcribes(self):
        status, obj = self._upload(self.files["voice"], "voice.m4a")
        self.assertEqual(status, 200, obj)

        def fake_transcribe(wav_bytes, request_part, session_id=None):
            return {"transcript": "ok", "turns": []}

        with mock.patch.object(
            server_module.MetaClient, "transcribe",
            side_effect=fake_transcribe,
        ):
            status, obj = self._transcribe(obj["file_id"], mode="PUSH_TO_TALK")
        self.assertEqual(status, 200, obj)
        self.assertEqual(obj.get("text"), "ok")


if __name__ == "__main__":
    unittest.main()
