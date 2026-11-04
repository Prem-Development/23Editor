"""Stabilization regressions for observed phone failures.

Pure intent/caption/zoom/sfx/status tests plus live FFmpeg renders that
inspect real output frames and audio (existence and duration alone never
proved captions, zoom, grading, or music). No Meta calls: Spark and
transcription are stubbed. Uses an isolated temp dir; user storage is
untouched.
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
from plan_schema import MediaInfo, validate_plan
from server import (
    ApiError,
    Handler,
    SERVER_BUILD,
    TimeoutHTTPServer,
    route_capabilities,
    route_plan,
    status_payload,
)
from stt import strip_control_tokens
from trim_intent import mentions_extra

MEDIA = {"duration": 30.0, "width": 640, "height": 480,
         "fps": 30.0, "has_audio": True}
SILENT_MEDIA = dict(MEDIA, has_audio=False)
FILE_ID = "9dbab62e7bac4965ad44b92aefabd05a"

_ISO_TMP = None
_ISO_STORAGE = None
_ISO_VERIFIED = None
_ISO_AT = None
_ISO_FAILED = None


def setUpModule():
    """Keep this file's mocked route_plan calls off the real markers.

    route_plan records a verified marker even for stubbed successes;
    without isolation those land in backend/storage and lie on the
    AI Status screen.
    """
    global _ISO_TMP, _ISO_STORAGE, _ISO_VERIFIED, _ISO_AT, _ISO_FAILED
    _ISO_TMP = tempfile.mkdtemp(prefix="23editor-iso-")
    _ISO_STORAGE = server_module.APP.storage
    _ISO_VERIFIED = set(server_module.VERIFIED)
    _ISO_AT = dict(server_module.VERIFIED_AT)
    _ISO_FAILED = {
        key: dict(entry) for key, entry in server_module.FAILED.items()
    }
    server_module.APP.storage = server_module.Storage(
        os.path.join(_ISO_TMP, "storage")
    )


def tearDownModule():
    server_module.APP.storage = _ISO_STORAGE
    server_module.VERIFIED.clear()
    server_module.VERIFIED.update(_ISO_VERIFIED)
    server_module.VERIFIED_AT.clear()
    server_module.VERIFIED_AT.update(_ISO_AT)
    server_module.FAILED.clear()
    server_module.FAILED.update(_ISO_FAILED)
    shutil.rmtree(_ISO_TMP, ignore_errors=True)


def _read_bytes(path):
    try:
        with open(path, "rb") as handle:
            return handle.read()
    except OSError:
        return None


class MarkerIsolationTest(unittest.TestCase):
    def test_markers_stay_out_of_real_storage(self):
        real_root = os.path.realpath(
            server_module.APP.settings.storage_dir
        )
        active_root = os.path.realpath(server_module.APP.storage.root)
        self.assertNotEqual(active_root, real_root)
        before = (
            _read_bytes(os.path.join(real_root, "verified.json")),
            _read_bytes(os.path.join(real_root, "failed.json")),
        )
        server_module.mark_verified("isolation_probe")
        try:
            self.assertTrue(os.path.exists(os.path.join(
                active_root, "verified.json"
            )))
            after = (
                _read_bytes(os.path.join(real_root, "verified.json")),
                _read_bytes(os.path.join(real_root, "failed.json")),
            )
            self.assertEqual(after, before)
        finally:
            server_module.VERIFIED.discard("isolation_probe")
            server_module.VERIFIED_AT.pop("isolation_probe", None)


def _plan_request(instruction, style="", aspect="original", **extra):
    body = {"media": dict(MEDIA), "instruction": instruction,
            "style": style, "aspect_ratio": aspect}
    body.update(extra)
    return body


def _spark_returning(payload, warnings=None):
    return (copy.deepcopy(payload), list(warnings or []), [])


def _run_plan(payload, instruction, **kwargs):
    with mock.patch.object(
        server_module, "_spark_plan",
        return_value=_spark_returning(payload),
    ):
        return route_plan(_plan_request(instruction, **kwargs))


class MentionsExtraTest(unittest.TestCase):
    def test_english_and_tamil_cues(self):
        self.assertTrue(mentions_extra("add captions", "captions"))
        self.assertTrue(mentions_extra("சப்டைட்டில் சேர்", "captions"))
        self.assertTrue(mentions_extra("zoom in", "zoom"))
        self.assertTrue(mentions_extra("add music", "music"))
        self.assertTrue(mentions_extra("பாடல் சேர்", "music"))
        self.assertFalse(mentions_extra("keep the first 5 seconds", "zoom"))
        self.assertFalse(mentions_extra("make it pop", "captions"))
        self.assertFalse(mentions_extra("", "zoom"))
        self.assertFalse(mentions_extra("zoom", "nope"))


class IntentAuthorityTest(unittest.TestCase):
    TRIM = {"operations": [{"op": "trim", "start": 0.0, "end": 5.0}],
            "notes": ""}

    def _ops(self, out):
        return out["plan"]["operations"]

    def test_unrequested_captions_dropped_with_note(self):
        payload = {"operations": [
            {"op": "trim", "start": 0.0, "end": 5.0},
            {"op": "captions", "source": "auto"},
        ], "notes": ""}
        out = _run_plan(payload, "keep only the first 5 seconds")
        self.assertEqual([o["op"] for o in self._ops(out)], ["trim"])
        self.assertTrue(
            any("were not requested" in w for w in out["adjustments"]),
            out["adjustments"],
        )
        self.assertEqual(out["warnings"], [])

    def test_requested_captions_kept(self):
        payload = {"operations": [
            {"op": "trim", "start": 0.0, "end": 5.0},
            {"op": "captions", "source": "auto"},
        ], "notes": ""}
        out = _run_plan(payload, "keep 5 seconds and add captions")
        self.assertIn("captions", [o["op"] for o in self._ops(out)])

    def test_captions_off_suppresses_even_when_mentioned(self):
        payload = {"operations": [
            {"op": "trim", "start": 0.0, "end": 5.0},
            {"op": "captions", "source": "auto"},
        ], "notes": ""}
        out = _run_plan(
            payload, "keep 5 seconds and add captions", captions="off"
        )
        self.assertEqual([o["op"] for o in self._ops(out)], ["trim"])
        self.assertTrue(
            any("Captions are Off" in w for w in out["adjustments"]),
            out["adjustments"],
        )

    def test_captions_on_permits_unmentioned(self):
        payload = {"operations": [
            {"op": "trim", "start": 0.0, "end": 5.0},
            {"op": "captions", "source": "auto"},
        ], "notes": ""}
        out = _run_plan(payload, "keep only the first 5 seconds",
                        captions="on")
        self.assertIn("captions", [o["op"] for o in self._ops(out)])

    def test_bad_captions_flag_rejected(self):
        with self.assertRaises(ApiError) as ctx:
            _run_plan(self.TRIM, "keep 5 seconds", captions="sometimes")
        self.assertEqual(ctx.exception.status, 400)

    def test_model_caption_lines_rewritten_to_auto(self):
        payload = {"operations": [
            {"op": "trim", "start": 0.0, "end": 5.0},
            {"op": "captions", "segments": [
                {"start": 0.0, "end": 2.0, "text": "guessed line"},
            ]},
        ], "notes": ""}
        out = _run_plan(payload, "add captions for the speech")
        captions = [o for o in self._ops(out) if o["op"] == "captions"]
        self.assertEqual(len(captions), 1)
        self.assertEqual(captions[0]["source"], "auto")
        self.assertNotIn("segments", captions[0])
        self.assertTrue(
            any("guessed lines" in w for w in out["adjustments"]),
            out["adjustments"],
        )

    def test_requested_captions_without_audio_stay_pending(self):
        payload = {"operations": [
            {"op": "trim", "start": 0.0, "end": 5.0},
            {"op": "captions", "source": "auto"},
        ], "notes": ""}
        with mock.patch.object(
            server_module, "_spark_plan",
            return_value=_spark_returning(payload),
        ):
            body = _plan_request("add captions")
            body["media"] = dict(SILENT_MEDIA)
            out = route_plan(body)
        self.assertEqual([o["op"] for o in self._ops(out)], ["trim"])
        self.assertEqual(len(out["pending"]), 1)
        self.assertIn("no audio track", out["pending"][0])

    def test_unrequested_zoom_music_sfx_fade_dropped(self):
        payload = {"operations": [
            {"op": "trim", "start": 0.0, "end": 10.0},
            {"op": "zoom", "start": 3.0, "end": 5.0, "to": 1.5},
            {"op": "music", "file_id": FILE_ID,
             "volume_db": -12.0, "duck": False},
            {"op": "sfx", "asset": "pop", "at": 2.0},
            {"op": "fade", "in_sec": 0.5, "out_sec": 0.5},
        ], "notes": ""}
        out = _run_plan(payload, "keep only the first 10 seconds")
        self.assertEqual([o["op"] for o in self._ops(out)], ["trim"])
        notes = " ".join(out["adjustments"])
        for word in ("Zoom", "Music", "Sound effects", "Fades"):
            self.assertIn(word, notes)

    def test_requested_zoom_and_sfx_kept(self):
        payload = {"operations": [
            {"op": "trim", "start": 0.0, "end": 10.0},
            {"op": "zoom", "start": 3.0, "end": 5.0, "to": 1.5},
            {"op": "sfx", "asset": "pop", "at": 2.0},
        ], "notes": ""}
        out = _run_plan(
            payload, "keep 10 seconds, zoom 3 to 5, add a sound effect"
        )
        self.assertEqual(
            [o["op"] for o in self._ops(out)],
            ["trim", "zoom", "sfx"],
        )

    def test_model_music_dropped_even_when_mentioned(self):
        payload = {"operations": [
            {"op": "trim", "start": 0.0, "end": 5.0},
            {"op": "music", "file_id": FILE_ID,
             "volume_db": -12.0, "duck": False},
        ], "notes": ""}
        out = _run_plan(payload, "add music quietly")
        self.assertEqual([o["op"] for o in self._ops(out)], ["trim"])
        self.assertTrue(
            any("attach" in w for w in out["adjustments"]),
            out["adjustments"],
        )

    def test_model_sfx_file_id_dropped(self):
        payload = {"operations": [
            {"op": "trim", "start": 0.0, "end": 5.0},
            {"op": "sfx", "file_id": FILE_ID, "at": 2.0},
        ], "notes": ""}
        out = _run_plan(payload, "add a sound effect at 2 seconds")
        self.assertEqual([o["op"] for o in self._ops(out)], ["trim"])

    def test_origin_tags_cover_instruction_preset_control(self):
        payload = {"operations": [
            {"op": "trim", "start": 0.0, "end": 5.0},
        ], "notes": ""}
        out = _run_plan(payload, "keep only the first 5 seconds",
                        style="Cinematic", aspect="1:1", fit="crop")
        origins = {
            o["op"]: o.get("origin") for o in self._ops(out)
            if isinstance(o, dict)
        }
        self.assertEqual(origins["trim"], "instruction")
        self.assertEqual(origins["grade"], "preset")
        self.assertEqual(origins["fade"], "preset")
        self.assertEqual(origins["resize_pad"], "control")
        resize = next(
            o for o in self._ops(out) if o["op"] == "resize_pad"
        )
        self.assertEqual(
            (resize["aspect"], resize["fit"]), ("1:1", "crop")
        )

    def test_model_resize_replaced_by_requested_aspect(self):
        payload = {"operations": [
            {"op": "trim", "start": 0.0, "end": 5.0},
            {"op": "resize_pad", "aspect": "9:16", "fit": "crop"},
        ], "notes": ""}
        out = _run_plan(payload, "keep 5 seconds", aspect="1:1")
        resizes = [o for o in self._ops(out) if o["op"] == "resize_pad"]
        self.assertEqual(len(resizes), 1)
        self.assertEqual(resizes[0]["aspect"], "1:1")
        self.assertEqual(resizes[0]["fit"], "pad")
        self.assertTrue(
            any("replaced the planner" in w for w in out["adjustments"]),
            out["adjustments"],
        )

    def test_original_drops_model_resize(self):
        payload = {"operations": [
            {"op": "trim", "start": 0.0, "end": 5.0},
            {"op": "resize_pad", "aspect": "9:16", "fit": "crop"},
        ], "notes": ""}
        out = _run_plan(payload, "keep 5 seconds")
        self.assertEqual([o["op"] for o in self._ops(out)], ["trim"])
        self.assertTrue(
            any("unrequested 9:16" in w for w in out["adjustments"]),
            out["adjustments"],
        )

    def test_bad_fit_rejected(self):
        with self.assertRaises(ApiError) as ctx:
            _run_plan(self.TRIM, "keep 5 seconds",
                      aspect="1:1", fit="stretch")
        self.assertEqual(ctx.exception.status, 400)


class CaptionContentTest(unittest.TestCase):
    def test_control_tokens_stripped_speech_kept(self):
        self.assertEqual(
            strip_control_tokens("<|speaker_A|> வணக்கம் hello"),
            "வணக்கம் hello",
        )
        self.assertEqual(strip_control_tokens("plain"), "plain")
        self.assertEqual(strip_control_tokens("<|x|>"), "")

    def test_coarse_blob_splits_into_sentences(self):
        segments, approx = server_module._turns_to_caption_segments(
            {"text": "blob", "turns": [
                {"text": "First line. Second line! Third line?",
                 "raw": {}},
            ]},
            12.0,
        )
        self.assertTrue(approx)
        self.assertEqual(len(segments), 3)
        self.assertEqual(segments[0]["text"], "First line.")
        self.assertEqual(
            [(s["start"], s["end"]) for s in segments],
            [(0.0, 4.0), (4.0, 8.0), (8.0, 12.0)],
        )

    def test_long_sentence_splits_further(self):
        words = " ".join(f"word{i}" for i in range(30))
        segments, approx = server_module._turns_to_caption_segments(
            {"text": words, "turns": [{"text": words, "raw": {}}]},
            9.0,
        )
        self.assertTrue(approx)
        self.assertGreater(len(segments), 1)
        for entry in segments:
            self.assertLessEqual(len(entry["text"].split()), 12)


class SfxEndTimeTest(unittest.TestCase):
    MEDIA = MediaInfo(duration=12.0, width=320, height=240,
                      fps=30.0, has_audio=True)

    def test_sfx_at_exact_output_end_rejected(self):
        result = validate_plan({"operations": [
            {"op": "trim", "start": 0.0, "end": 5.0},
            {"op": "sfx", "asset": "pop", "at": 5.0},
        ]}, self.MEDIA)
        self.assertFalse(result.ok)
        self.assertIn("inaudible", " ".join(result.errors))

    def test_sfx_just_inside_output_accepted(self):
        result = validate_plan({"operations": [
            {"op": "trim", "start": 0.0, "end": 5.0},
            {"op": "sfx", "asset": "pop", "at": 4.9},
        ]}, self.MEDIA)
        self.assertTrue(result.ok, result.errors)

    def test_sfx_past_output_end_rejected(self):
        # sfx.at is edited-output seconds: past the output end is a
        # validation error, not a silent drop.
        result = validate_plan({"operations": [
            {"op": "trim", "start": 0.0, "end": 5.0},
            {"op": "sfx", "asset": "pop", "at": 9.0},
        ]}, self.MEDIA)
        self.assertFalse(result.ok)
        self.assertIn("output end", " ".join(result.errors))


class VersionAndStatusTest(unittest.TestCase):
    def test_build_markers_present(self):
        self.assertTrue(SERVER_BUILD)
        capabilities = route_capabilities()
        self.assertEqual(capabilities["server_build"], SERVER_BUILD)

    def test_status_carries_build_and_stamps(self):
        client = mock.Mock()
        client.list_models.return_value = ["muse-spark-1.3"]
        with mock.patch.object(
            server_module.APP, "client", return_value=client
        ):
            payload = status_payload()
        self.assertEqual(payload["server_build"], SERVER_BUILD)
        for entry in payload["integrations"]:
            self.assertIn("verified_at", entry)


class VerifiedPersistenceTest(unittest.TestCase):
    def test_markers_survive_reload_in_isolated_storage(self):
        tmp = tempfile.mkdtemp(prefix="23editor-verify-")
        old_storage = server_module.APP.storage
        old_verified = set(server_module.VERIFIED)
        old_at = dict(server_module.VERIFIED_AT)
        try:
            server_module.APP.storage = server_module.Storage(
                os.path.join(tmp, "storage")
            )
            server_module.VERIFIED.clear()
            server_module.VERIFIED_AT.clear()
            server_module.mark_verified("spark_plan")
            path = os.path.join(
                server_module.APP.storage.root, "verified.json"
            )
            self.assertTrue(os.path.exists(path))
            # Simulate a restart: memory cleared, file reloaded.
            server_module.VERIFIED.clear()
            server_module.VERIFIED_AT.clear()
            self.assertFalse(server_module.is_verified("spark_plan"))
            server_module._load_verified()
            self.assertTrue(server_module.is_verified("spark_plan"))
            self.assertTrue(server_module.verified_at("spark_plan"))
        finally:
            server_module.APP.storage = old_storage
            server_module.VERIFIED.clear()
            server_module.VERIFIED.update(old_verified)
            server_module.VERIFIED_AT.clear()
            server_module.VERIFIED_AT.update(old_at)
            shutil.rmtree(tmp, ignore_errors=True)


class _LiveBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ffmpeg = shutil.which("ffmpeg")
        cls.ffprobe = shutil.which("ffprobe")
        if not cls.ffmpeg or not cls.ffprobe:
            raise unittest.SkipTest("system ffmpeg/ffprobe not on PATH")
        cls.tmp = tempfile.mkdtemp(prefix="23editor-stabilize-")
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
        boundary = "stabilizetestboundary"
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

    def _frame_bytes(self, path, at):
        done = subprocess.run(
            [self.ffmpeg, "-y", "-v", "error", "-ss", str(at),
             "-i", path, "-frames:v", "1", "-f", "rawvideo",
             "-pix_fmt", "rgb24", "-"],
            capture_output=True, timeout=60,
        )
        self.assertEqual(done.returncode, 0, done.stderr)
        return done.stdout

    def _frame_diff_ratio(self, first, second):
        self.assertEqual(len(first), len(second))
        self.assertGreater(len(first), 0)
        changed = sum(1 for a, b in zip(first, second) if a != b)
        return changed / len(first)

    def _mean_volume_db(self, path):
        done = subprocess.run(
            [self.ffmpeg, "-v", "info", "-i", path, "-af", "volumedetect",
             "-f", "null", "-"],
            capture_output=True, text=True, timeout=60,
        )
        for line in done.stderr.splitlines():
            if "mean_volume" in line:
                return float(line.split("mean_volume:")[1].split("dB")[0])
        self.fail(f"no mean_volume in volumedetect output: {done.stderr}")

    def _make_source(self, name, audio="tone"):
        """12s 320x240 fixture: tone audio, silent audio, or none."""
        path = os.path.join(self.tmp, name)
        cmd = [self.ffmpeg, "-y", "-v", "error",
               "-f", "lavfi", "-i",
               "testsrc=duration=12:size=320x240:rate=10"]
        if audio == "tone":
            cmd += ["-f", "lavfi", "-i",
                    "sine=frequency=440:duration=12"]
        elif audio == "silent":
            cmd += ["-f", "lavfi", "-i",
                    "anullsrc=channel_layout=stereo:sample_rate=44100",
                    "-t", "12"]
        cmd += ["-c:v", "libx264", "-pix_fmt", "yuv420p"]
        if audio == "none":
            cmd += ["-an"]
        else:
            cmd += ["-c:a", "aac"]
        cmd += ["-shortest", path]
        subprocess.run(cmd, check=True, timeout=180)
        return path

    def _make_song(self, name="song.wav"):
        path = os.path.join(self.tmp, name)
        subprocess.run(
            [self.ffmpeg, "-y", "-v", "error",
             "-f", "lavfi", "-i", "sine=frequency=330:duration=12",
             "-c:a", "pcm_s16le", path],
            check=True, timeout=120,
        )
        return path


class SilentFootageLiveTest(_LiveBase):
    def test_trim_zoom_without_audio_track(self):
        source = self._make_source("noaudio.mp4", audio="none")
        file_id = self._upload(source)
        status, obj = self._render(file_id, [
            {"op": "trim", "start": 0.0, "end": 5.0},
            {"op": "zoom", "start": 1.0, "end": 3.0, "to": 1.5},
            {"op": "text", "text": "Silent", "start": 0.0, "end": 2.0,
             "position": "top", "animation": "none", "size": "medium"},
        ])
        self.assertEqual(status, 200, obj)
        out = self._download(obj["url"], "noaudio-out.mp4")
        info = self._probe(out)
        duration = float(info["format"]["duration"])
        self.assertGreater(duration, 4.4)
        self.assertLess(duration, 5.6)
        kinds = [s["codec_type"] for s in info["streams"]]
        self.assertIn("video", kinds)
        self.assertNotIn("audio", kinds)

    def test_trim_zoom_with_silent_audio_track(self):
        source = self._make_source("silentaudio.mp4", audio="silent")
        file_id = self._upload(source)
        status, obj = self._render(file_id, [
            {"op": "trim", "start": 0.0, "end": 5.0},
            {"op": "zoom", "start": 1.0, "end": 3.0, "to": 1.5},
        ])
        self.assertEqual(status, 200, obj)
        out = self._download(obj["url"], "silentaudio-out.mp4")
        info = self._probe(out)
        kinds = [s["codec_type"] for s in info["streams"]]
        self.assertIn("audio", kinds)

    def test_music_makes_silent_footage_audible(self):
        source = self._make_source("songless.mp4", audio="none")
        file_id = self._upload(source)
        music_id = self._upload(self._make_song(), filename="song.wav")
        status, obj = self._render(file_id, [
            {"op": "trim", "start": 0.0, "end": 5.0},
            {"op": "music", "file_id": music_id,
             "volume_db": -12.0, "duck": True},
        ])
        self.assertEqual(status, 200, obj)
        notes = " ".join(obj["warnings"]) + " ".join(obj["adjustments"])
        self.assertIn("Ducking", notes)
        out = self._download(obj["url"], "music-out.mp4")
        info = self._probe(out)
        kinds = [s["codec_type"] for s in info["streams"]]
        self.assertIn("audio", kinds)
        mean_db = self._mean_volume_db(out)
        self.assertGreater(mean_db, -50.0)

    def test_music_removal_leaves_silence(self):
        source = self._make_source("unmusicked.mp4", audio="none")
        file_id = self._upload(source)
        music_id = self._upload(self._make_song("song2.wav"),
                                filename="song.wav")
        status, with_music = self._render(file_id, [
            {"op": "trim", "start": 0.0, "end": 5.0},
            {"op": "music", "file_id": music_id,
             "volume_db": -6.0, "duck": False},
        ])
        self.assertEqual(status, 200, with_music)
        out_music = self._download(with_music["url"], "with-music.mp4")
        self.assertIn(
            "audio", [s["codec_type"]
                      for s in self._probe(out_music)["streams"]]
        )
        status, plain = self._render(file_id, [
            {"op": "trim", "start": 0.0, "end": 5.0},
        ])
        self.assertEqual(status, 200, plain)
        out_plain = self._download(plain["url"], "without-music.mp4")
        self.assertNotIn(
            "audio", [s["codec_type"]
                      for s in self._probe(out_plain)["streams"]]
        )

    def test_sfx_on_silent_footage_adds_audio(self):
        source = self._make_source("fxless.mp4", audio="none")
        file_id = self._upload(source)
        status, obj = self._render(file_id, [
            {"op": "trim", "start": 0.0, "end": 5.0},
            {"op": "sfx", "asset": "pop", "at": 1.0},
        ])
        self.assertEqual(status, 200, obj)
        out = self._download(obj["url"], "sfx-out.mp4")
        self.assertIn(
            "audio", [s["codec_type"]
                      for s in self._probe(out)["streams"]]
        )

    def test_auto_captions_without_audio_explains(self):
        source = self._make_source("captionless.mp4", audio="none")
        file_id = self._upload(source)
        status, obj = self._render(file_id, [
            {"op": "trim", "start": 0.0, "end": 5.0},
            {"op": "captions", "source": "auto"},
        ])
        self.assertEqual(status, 422, obj)
        self.assertIn("audio track", obj["error"])


class ZoomSpanLiveTest(_LiveBase):
    def test_zoom_across_plain_cut_renders_continuously(self):
        source = self._make_source("span.mp4")
        file_id = self._upload(source)
        status, obj = self._render(file_id, [
            {"op": "keep", "start": 0.0, "end": 4.0},
            {"op": "keep", "start": 6.0, "end": 10.0},
            {"op": "zoom", "start": 3.0, "end": 8.0, "to": 1.6},
        ])
        self.assertEqual(status, 200, obj)
        out = self._download(obj["url"], "span-out.mp4")
        duration = float(self._probe(out)["format"]["duration"])
        self.assertGreater(duration, 7.4)
        self.assertLess(duration, 8.6)

    def test_two_zooms_render(self):
        source = self._make_source("multi.mp4")
        file_id = self._upload(source)
        status, obj = self._render(file_id, [
            {"op": "trim", "start": 0.0, "end": 6.0},
            {"op": "zoom", "start": 0.5, "end": 1.5, "to": 1.5},
            {"op": "zoom", "start": 3.5, "end": 4.5, "to": 1.3},
        ])
        self.assertEqual(status, 200, obj)
        out = self._download(obj["url"], "multi-out.mp4")
        duration = float(self._probe(out)["format"]["duration"])
        self.assertGreater(duration, 5.4)
        self.assertLess(duration, 6.6)

    def test_zoom_changes_real_pixels(self):
        source = self._make_source("pixels.mp4")
        file_id = self._upload(source)
        status, plain = self._render(file_id, [
            {"op": "trim", "start": 0.0, "end": 5.0},
        ])
        self.assertEqual(status, 200, plain)
        status, zoomed = self._render(file_id, [
            {"op": "trim", "start": 0.0, "end": 5.0},
            {"op": "zoom", "start": 0.0, "end": 5.0, "to": 2.0},
        ])
        self.assertEqual(status, 200, zoomed)
        plain_frame = self._frame_bytes(
            self._download(plain["url"], "plain.mp4"), 2.5
        )
        zoomed_frame = self._frame_bytes(
            self._download(zoomed["url"], "zoomed.mp4"), 2.5
        )
        ratio = self._frame_diff_ratio(plain_frame, zoomed_frame)
        self.assertGreater(ratio, 0.05)

    def test_cinematic_grade_changes_real_pixels(self):
        source = self._make_source("grade.mp4")
        file_id = self._upload(source)
        status, plain = self._render(file_id, [
            {"op": "trim", "start": 0.0, "end": 5.0},
        ])
        self.assertEqual(status, 200, plain)
        status, graded = self._render(file_id, [
            {"op": "trim", "start": 0.0, "end": 5.0},
            {"op": "grade", "preset": "warm", "intensity": 0.5},
        ])
        self.assertEqual(status, 200, graded)
        # Compare well before any fade tail: a mid-output frame.
        plain_frame = self._frame_bytes(
            self._download(plain["url"], "grade-plain.mp4"), 2.5
        )
        graded_frame = self._frame_bytes(
            self._download(graded["url"], "grade-warm.mp4"), 2.5
        )
        ratio = self._frame_diff_ratio(plain_frame, graded_frame)
        self.assertGreater(ratio, 0.05)


class KeptAudioCaptionsLiveTest(_LiveBase):
    def test_trimmed_auto_captions_transcribe_kept_audio(self):
        source = self._make_source("keptcap.mp4")
        file_id = self._upload(source)
        seen: list[int] = []

        def fake_transcribe(wav_bytes, request_part):
            seen.append(len(wav_bytes))
            return {"transcript": "hello world",
                    "turns": [{"turnId": 1,
                               "transcript": "hello world"}]}

        client = mock.Mock()
        client.transcribe.side_effect = fake_transcribe
        with mock.patch.object(
            server_module.APP, "client", return_value=client
        ):
            status, obj = self._render(file_id, [
                {"op": "trim", "start": 0.0, "end": 5.0},
                {"op": "captions", "source": "auto"},
            ])
        self.assertEqual(status, 200, obj)
        # 5 kept seconds at 16kHz mono 16-bit: ~160KB, not the ~384KB
        # a full 12s transcription would send.
        self.assertEqual(len(seen), 1)
        self.assertLess(seen[0], 200000)
        self.assertGreater(seen[0], 100000)
        notes = " ".join(obj["warnings"])
        self.assertIn("approximate", notes)

    def test_silent_kept_audio_warns_and_renders_on(self):
        source = self._make_source("nospeech.mp4", audio="silent")
        file_id = self._upload(source)
        client = mock.Mock()
        client.transcribe.return_value = {"transcript": "", "turns": []}
        with mock.patch.object(
            server_module.APP, "client", return_value=client
        ):
            status, obj = self._render(file_id, [
                {"op": "trim", "start": 0.0, "end": 5.0},
                {"op": "captions", "source": "auto"},
            ])
        self.assertEqual(status, 200, obj)
        client.transcribe.assert_not_called()
        notes = " ".join(obj["warnings"])
        self.assertIn("The kept audio is silent", notes)
        out = self._download(obj["url"], "nospeech-out.mp4")
        duration = float(self._probe(out)["format"]["duration"])
        self.assertGreater(duration, 4.4)
        self.assertLess(duration, 5.6)


if __name__ == "__main__":
    unittest.main()
