"""Assistant flows: analysis signals, brief builder, reference style,
diagnostics taxonomy, covers, and artwork guardrails.

Pure tests run everywhere; live FFmpeg tests need the system toolchain
(and loopback for the HTTP ones) and skip otherwise. Meta calls are
always stubbed; nothing here marks an integration verified.
"""
import copy
import json
import os
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from unittest import mock

import analyze
import server as server_module
from meta_client import MetaAPIError
from server import (
    ApiError,
    Handler,
    TimeoutHTTPServer,
    route_brief,
    route_plan,
    route_thumbnail,
)

MEDIA = {"duration": 30.0, "width": 640, "height": 480,
         "fps": 30.0, "has_audio": True}

_ISO_TMP = None
_ISO_STORAGE = None
_ISO_VERIFIED = None
_ISO_AT = None
_ISO_FAILED = None


def setUpModule():
    """Keep this file's mocked routes off the real marker files.

    route_brief/route_plan/route_thumbnail record verified/failed
    markers even for stubbed successes; without isolation those land
    in backend/storage and lie on the AI Status screen.
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


class SignalTest(unittest.TestCase):
    def test_frame_times_are_bounded_and_spread(self):
        times = analyze.frame_times(13.0)
        self.assertLessEqual(len(times), analyze.MAX_FRAMES)
        self.assertGreater(len(times), 1)
        self.assertTrue(all(0 < t < 13.0 for t in times))
        self.assertEqual(times, sorted(times))
        long_times = analyze.frame_times(60.0)
        self.assertEqual(len(long_times), analyze.MAX_FRAMES)
        for low, high in zip(long_times, long_times[1:]):
            self.assertGreaterEqual(high - low, analyze.MIN_FRAME_GAP)
        self.assertEqual(analyze.frame_times(0), [])
        self.assertEqual(len(analyze.frame_times(1.0)), 1)

    def test_classify_audio_branches(self):
        self.assertEqual(
            analyze.classify_audio(None)[0], "no_stream"
        )
        self.assertEqual(
            analyze.classify_audio(
                {"mean_db": -70.0, "max_db": -60.0})[0],
            "silence",
        )
        name, reason = analyze.classify_audio(
            {"mean_db": -20.0, "max_db": -3.0}
        )
        self.assertEqual(name, "non_silent")
        self.assertIn("transcription", reason)

    def test_content_hint_is_labeled_heuristic(self):
        hint = analyze.content_hint(13.0, True, "non_silent", 40, 1)
        self.assertEqual(hint["guess"], "talking")
        self.assertEqual(hint["confidence"], "low")
        silent = analyze.content_hint(13.0, False, "no_stream", 0, 0)
        self.assertEqual(silent["guess"], "silent")
        music = analyze.content_hint(13.0, True, "non_silent", 0, 0)
        self.assertEqual(music["guess"], "music_or_ambience")

    def test_summarize_for_prompt_stays_factual(self):
        text = analyze.summarize_for_prompt({
            "duration": 13.7, "orientation": "portrait",
            "width": 1080, "height": 1920,
            "audio": {"class": "non_silent"},
            "speech": {"present": True, "words": 40, "approximate": True},
            "cuts": [1.0, 5.0],
            "content_hint": {"guess": "talking", "confidence": "low"},
        })
        for token in ("13.7", "portrait", "40 words", "cuts: 2",
                      "low confidence"):
            self.assertIn(token, text)

    def test_summarize_includes_pauses_turns_and_cut_times(self):
        text = analyze.summarize_for_prompt({
            "duration": 30.0, "orientation": "landscape",
            "width": 640, "height": 360,
            "audio": {"class": "non_silent"},
            "speech": {
                "present": True, "words": 60, "approximate": False,
                "turns": [
                    {"text": "hello", "start": 1.0, "end": 2.0},
                    {"text": "world"},
                ],
            },
            "cuts": [4.0, 9.5],
            "pauses": [{"start": 5.0, "end": 7.5}],
            "content_hint": {"guess": "talking", "confidence": "low"},
        })
        for token in ("silent pauses: 1 (5.0-7.5s)",
                      "speech turns: 2 (1 with timestamps)",
                      "cuts: 2 (first: 4.0s, 9.5s)"):
            self.assertIn(token, text)


class ReviewSuggestionsTest(unittest.TestCase):
    def test_long_pauses_become_suggestions(self):
        out = server_module._review_suggestions(
            {"pauses": [
                {"start": 1.0, "end": 4.5},
                {"start": 10.0, "end": 10.5},
                {"start": 20.0, "end": 25.0},
            ]},
            30.0,
        )
        self.assertEqual(
            [(s["start"], s["end"]) for s in out],
            [(1.0, 4.5), (20.0, 25.0)],
        )
        self.assertIn("silence", str(out[0]["reason"]))

    def test_malformed_or_missing_evidence_yields_nothing(self):
        self.assertEqual(server_module._review_suggestions(None, 30.0), [])
        self.assertEqual(
            server_module._review_suggestions({"pauses": "nope"}, 30.0), []
        )
        self.assertEqual(
            server_module._review_suggestions(
                {"pauses": [{"start": 40.0, "end": 45.0}]}, 30.0
            ),
            [],
        )


class JobManagerTest(unittest.TestCase):
    def test_cancel_between_stages(self):
        manager = analyze.JobManager()
        entered = threading.Event()

        def runner(job):
            entered.set()
            for _ in range(200):
                job.check()
                time.sleep(0.05)
            return {}

        job = manager.start("f1", runner)
        self.assertTrue(entered.wait(timeout=5))
        self.assertTrue(manager.cancel(job.id))
        deadline = time.time() + 15
        while job.state not in ("cancelled", "done", "error"):
            self.assertLess(time.time(), deadline, "job did not settle")
            time.sleep(0.05)
        self.assertEqual(job.state, "cancelled")
        self.assertFalse(manager.cancel(job.id))

    def test_get_unknown_is_none(self):
        self.assertIsNone(analyze.JobManager().get("missing"))


class BriefValidationTest(unittest.TestCase):
    def test_bad_goal_and_language_rejected_without_meta(self):
        with mock.patch.object(
            server_module.APP, "client",
            side_effect=AssertionError("must not call Meta"),
        ):
            with self.assertRaises(ApiError):
                route_brief({"goal": "dance", "language": "en"})
            with self.assertRaises(ApiError):
                route_brief({"goal": "reel", "language": "xx"})
            with self.assertRaises(ApiError):
                route_brief({"goal": "reel", "language": "en",
                             "detail": "x" * 2001})

    def test_minutes_ambiguity_asks_first(self):
        with mock.patch.object(
            server_module.APP, "client",
            side_effect=AssertionError("must not call Meta"),
        ):
            out = route_brief({"goal": "custom", "language": "en",
                               "detail": "keep the first 5 minutes"})
        self.assertIn("minutes or seconds", out["question"])

    def test_name_ambiguity_asks_first(self):
        with mock.patch.object(
            server_module.APP, "client",
            side_effect=AssertionError("must not call Meta"),
        ):
            out = route_brief({"goal": "custom", "language": "en",
                               "detail": "zoom to the person called Arun"})
        self.assertIn("Arun", out["question"])

    def test_short_custom_asks_first(self):
        with mock.patch.object(
            server_module.APP, "client",
            side_effect=AssertionError("must not call Meta"),
        ):
            out = route_brief({"goal": "custom", "language": "en",
                               "detail": "make nice"})
        self.assertIn("Tell me a little more", out["question"])

    def test_captions_goal_without_speech_asks_for_titles(self):
        with mock.patch.object(
            server_module.APP, "client",
            side_effect=AssertionError("must not call Meta"),
        ):
            out = route_brief({
                "goal": "captions", "language": "en",
                "analysis": {
                    "duration": 10.0, "orientation": "landscape",
                    "width": 640, "height": 480,
                    "audio": {"class": "silence"},
                    "speech": {"present": False, "words": 0,
                               "approximate": True},
                    "cuts": [],
                    "content_hint": {"guess": "silent",
                                     "confidence": "medium"},
                },
            })
        self.assertIn("written titles", out["question"])

    def test_reference_without_reference_asks_first(self):
        with mock.patch.object(
            server_module.APP, "client",
            side_effect=AssertionError("must not call Meta"),
        ):
            out = route_brief({"goal": "reference", "language": "en"})
        self.assertIn("reference video", out["question"])

    def test_brief_uses_observations_and_detail(self):
        seen = {}

        def fake_chat(model, messages, response_format=None, timeout=None):
            seen["messages"] = messages
            return {"choices": [{"message": {"content": json.dumps({
                "brief": "Keep 5 seconds and add captions.",
                "explains": ["Trims the start", "Burns captions"],
                "question": None,
            })}}]}

        client = mock.Mock()
        client.chat_completions.side_effect = fake_chat
        analysis = {"duration": 13.7, "orientation": "portrait",
                    "width": 1080, "height": 1920,
                    "audio": {"class": "non_silent"},
                    "speech": {"present": True, "words": 40,
                               "approximate": True},
                    "cuts": [1.0],
                    "content_hint": {"guess": "talking",
                                     "confidence": "low"}}
        with mock.patch.object(
            server_module.APP, "client", return_value=client
        ):
            out = route_brief({
                "goal": "reel", "language": "en",
                "detail": "punchy", "analysis": analysis,
            })
        self.assertEqual(out["brief"], "Keep 5 seconds and add captions.")
        self.assertEqual(len(out["explains"]), 2)
        user_text = seen["messages"][1]["content"]
        self.assertIn("punchy", user_text)
        self.assertIn("40 words", user_text)
        self.assertIn("portrait", user_text)

    def test_model_question_returned_verbatim(self):
        client = mock.Mock()
        client.chat_completions.return_value = {"choices": [{"message": {
            "content": json.dumps({"brief": "", "explains": [],
                                   "question": "Which part?"})}}]}
        with mock.patch.object(
            server_module.APP, "client", return_value=client
        ):
            out = route_brief({"goal": "reel", "language": "ta",
                               "detail": "something nice please"})
        self.assertEqual(out, {"question": "Which part?"})

    def test_empty_brief_is_an_error_not_silence(self):
        client = mock.Mock()
        client.chat_completions.return_value = {"choices": [{"message": {
            "content": json.dumps({"brief": " ", "explains": [],
                                   "question": None})}}]}
        with mock.patch.object(
            server_module.APP, "client", return_value=client
        ):
            with self.assertRaises(ApiError) as ctx:
                route_brief({"goal": "reel", "language": "en",
                             "detail": "something nice please"})
        self.assertEqual(ctx.exception.status, 502)


class PlanAnalysisContextTest(unittest.TestCase):
    def test_observations_reach_the_planner(self):
        seen = {}

        def fake_spark(instruction, media, style, aspect, model,
                       footage_notes=""):
            seen["notes"] = footage_notes
            return ({"operations": [
                {"op": "trim", "start": 0.0, "end": 5.0}], "notes": ""},
                    [], [])

        with mock.patch.object(
            server_module, "_spark_plan", side_effect=fake_spark
        ):
            route_plan({
                "media": dict(MEDIA),
                "instruction": "keep only the first 5 seconds",
                "analysis": {
                    "duration": 13.7, "orientation": "portrait",
                    "width": 1080, "height": 1920,
                    "audio": {"class": "non_silent"},
                    "speech": {"present": True, "words": 40,
                               "approximate": True},
                    "cuts": [],
                    "content_hint": {"guess": "talking",
                                     "confidence": "low"},
                },
            })
        self.assertIn("Footage observations", seen["notes"])
        self.assertIn("40 words", seen["notes"])


class ArtworkGuardrailTest(unittest.TestCase):
    def test_unknown_name_asks_before_generating(self):
        with mock.patch.object(
            server_module.APP, "client",
            side_effect=AssertionError("must not call Meta"),
        ):
            with self.assertRaises(ApiError) as ctx:
                route_thumbnail({"prompt": "Air rakumaan poster"})
        self.assertEqual(ctx.exception.status, 422)
        self.assertIn("Air rakumaan", ctx.exception.message)
        self.assertIn("question", ctx.exception.details)

    def test_name_in_transcript_is_not_ambiguous(self):
        client = mock.Mock()
        client.generate_image.return_value = b"fakepng"
        tmp = tempfile.mkdtemp(prefix="23editor-art-")
        old_storage = server_module.APP.storage
        try:
            server_module.APP.storage = server_module.Storage(
                os.path.join(tmp, "storage")
            )
            with mock.patch.object(
                server_module.APP, "client", return_value=client
            ):
                out = route_thumbnail({
                    "prompt": "Arun Kumar celebrating",
                    "analysis": {"speech": {"transcript_excerpt":
                                            "arun kumar wins the match"}},
                })
            self.assertIn("url", out)
        finally:
            server_module.APP.storage = old_storage
            shutil.rmtree(tmp, ignore_errors=True)

    def test_plain_prompt_has_no_clarification(self):
        self.assertIsNone(server_module._artwork_clarification(
            "sunset over the ocean", None))

    def test_product_context_needs_no_clarification(self):
        # "Video creator app 23editor" names the product itself: the
        # software context is explicit, so no person/product question.
        self.assertIsNone(server_module._artwork_clarification(
            "Video creator app 23editor", None))
        self.assertIsNone(server_module._artwork_clarification(
            "Garden care company logo", None))

    def test_unanchored_name_still_asks(self):
        question = server_module._artwork_clarification(
            "Air rakumaan training hard", None)
        self.assertIsNotNone(question)
        assert question is not None
        self.assertIn("Air rakumaan", question)

    def test_policy_rejection_suggests_cover(self):
        client = mock.Mock()
        client.generate_image.side_effect = MetaAPIError(
            400, "Rejected by safety policy", error_type="moderation"
        )
        with mock.patch.object(
            server_module.APP, "client", return_value=client
        ):
            with self.assertRaises(ApiError) as ctx:
                route_thumbnail({"prompt": "a calm lake"})
        self.assertEqual(ctx.exception.status, 422)
        self.assertTrue(ctx.exception.details.get("suggest_cover"))
        self.assertIn("never bypassed", ctx.exception.message)

    def test_non_policy_errors_pass_through(self):
        client = mock.Mock()
        client.generate_image.side_effect = MetaAPIError(
            500, "Upstream exploded"
        )
        with mock.patch.object(
            server_module.APP, "client", return_value=client
        ):
            with self.assertRaises(MetaAPIError):
                route_thumbnail({"prompt": "a calm lake"})


class ProviderTaxonomyTest(unittest.TestCase):
    def test_language_failure_maps_to_422(self):
        err = server_module._provider_transcribe_error(MetaAPIError(
            400, "language not supported", code="bad_language"
        ))
        self.assertEqual(err.status, 422)
        self.assertEqual(
            err.details["diagnosis"]["class"], "unsupported_language"
        )

    def test_generic_failure_maps_to_502_with_diagnosis(self):
        err = server_module._provider_transcribe_error(MetaAPIError(
            500, "Upstream exploded"
        ))
        self.assertEqual(err.status, 502)
        self.assertEqual(
            err.details["diagnosis"]["class"], "provider_failure"
        )
        self.assertNotIn("No speech", err.message)


class _LiveBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ffmpeg = shutil.which("ffmpeg")
        cls.ffprobe = shutil.which("ffprobe")
        if not cls.ffmpeg or not cls.ffprobe:
            raise unittest.SkipTest("system ffmpeg/ffprobe not on PATH")
        cls.tmp = tempfile.mkdtemp(prefix="23editor-assistant-")
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

    def _get(self, path):
        with urllib.request.urlopen(
            f"http://127.0.0.1:{self.port}{path}", timeout=60
        ) as response:
            return response.status, json.loads(response.read() or b"{}")

    def _upload(self, path, filename="source.mp4", label=None):
        with open(path, "rb") as handle:
            payload = handle.read()
        boundary = "assistanttestboundary"
        body = (
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="file"; '
            f'filename="{filename}"\r\n'
            "Content-Type: application/octet-stream\r\n\r\n"
        ).encode("latin-1") + payload
        if label is not None:
            body += (
                f"\r\n--{boundary}\r\n"
                'Content-Disposition: form-data; name="label"\r\n\r\n'
                f"{label}"
            ).encode("latin-1")
        body += f"\r\n--{boundary}--\r\n".encode()
        status, obj = self._post(
            "/api/upload", body, f"multipart/form-data; boundary={boundary}"
        )
        self.assertEqual(status, 200, obj)
        return obj["file_id"], obj.get("label")

    def _make_source(self, name, audio="tone", size="320x240"):
        path = os.path.join(self.tmp, name)
        cmd = [self.ffmpeg, "-y", "-v", "error",
               "-f", "lavfi", "-i",
               f"testsrc=duration=12:size={size}:rate=10"]
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

    def _probe(self, path):
        done = subprocess.run(
            [self.ffprobe, "-v", "error", "-print_format", "json",
             "-show_format", "-show_streams", path],
            capture_output=True, text=True, timeout=60,
        )
        self.assertEqual(done.returncode, 0, done.stderr)
        return json.loads(done.stdout)


class AnalyzeLiveTest(_LiveBase):
    def test_silence_spans_find_the_gap(self):
        path = os.path.join(self.tmp, "gap.mp4")
        subprocess.run(
            [self.ffmpeg, "-y", "-v", "error",
             "-f", "lavfi", "-i", "testsrc=duration=7:size=320x240:rate=10",
             "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
             "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo:d=3",
             "-f", "lavfi", "-i", "sine=frequency=880:duration=2",
             "-filter_complex",
             "[1:a][2:a][3:a]concat=n=3:v=0:a=1[a]",
             "-map", "0:v", "-map", "[a]",
             "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
             "-shortest", path],
            check=True, timeout=180,
        )
        spans = analyze.silence_spans(self.ffmpeg, path)
        self.assertGreaterEqual(len(spans), 1)
        self.assertAlmostEqual(spans[0]["start"], 2.0, delta=0.4)
        self.assertAlmostEqual(spans[0]["end"], 5.0, delta=0.4)

    def _wait_job(self, job_id, timeout=120.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            status, obj = self._get(f"/api/analyze/{job_id}")
            self.assertEqual(status, 200, obj)
            if obj["state"] in ("done", "error", "cancelled"):
                return obj
            time.sleep(0.2)
        self.fail("analysis job did not finish")

    def test_silent_footage_skips_transcription(self):
        source = self._make_source("analysis-silent.mp4", audio="silent")
        file_id, _ = self._upload(source)
        client = mock.Mock()
        client.transcribe.side_effect = AssertionError("must not call STT")
        with mock.patch.object(
            server_module.APP, "client", return_value=client
        ):
            status, started = self._post(
                "/api/analyze/start", json.dumps(
                    {"file_id": file_id}).encode(), "application/json")
        self.assertEqual(started["state"], "queued", started)
        with mock.patch.object(
            server_module.APP, "client", return_value=client
        ):
            obj = self._wait_job(started["job_id"])
        self.assertEqual(obj["state"], "done", obj)
        result = obj["result"]
        self.assertEqual(result["audio"]["class"], "silence")
        self.assertFalse(result["speech"]["present"])
        self.assertEqual(result["content_hint"]["guess"], "silent")
        self.assertLessEqual(len(result["frames"]), analyze.MAX_FRAMES)
        self.assertTrue(all("url" in f for f in result["frames"]))
        client.transcribe.assert_not_called()

    def test_tone_audio_transcribes_with_stub(self):
        source = self._make_source("analysis-tone.mp4", audio="tone")
        file_id, _ = self._upload(source)
        client = mock.Mock()
        client.transcribe.return_value = {
            "transcript": "vanakkam nanbargale", "turns": []}
        with mock.patch.object(
            server_module.APP, "client", return_value=client
        ):
            status, started = self._post(
                "/api/analyze/start", json.dumps(
                    {"file_id": file_id}).encode(), "application/json")
            obj = self._wait_job(started["job_id"])
        self.assertEqual(obj["state"], "done", obj)
        result = obj["result"]
        self.assertEqual(result["audio"]["class"], "non_silent")
        self.assertTrue(result["speech"]["present"])
        self.assertIn("vanakkam", result["speech"]["transcript_excerpt"])
        for key in ("audio_ms", "pauses_ms", "transcribe_ms", "cuts_ms",
                    "frames_ms"):
            self.assertGreaterEqual(
                result["timings"][key], 0, result["timings"])
        # Cached: a second start returns done without new work.
        with mock.patch.object(
            server_module.APP, "client", return_value=client
        ):
            status, again = self._post(
                "/api/analyze/start", json.dumps(
                    {"file_id": file_id}).encode(), "application/json")
        self.assertEqual(again["state"], "done")
        self.assertIn("cached", again["stage"])

    def test_reference_labels_and_style(self):
        source = self._make_source("ref.mp4", audio="none", size="240x320")
        file_id, label = self._upload(source, "ref.mp4", label="reference")
        self.assertEqual(label, "reference")
        status, obj = self._post(
            "/api/reference/analyze",
            json.dumps({"file_id": file_id}).encode(), "application/json")
        self.assertEqual(status, 200, obj)
        style = obj["style"]
        self.assertEqual(style["aspect"], "9:16")
        self.assertGreaterEqual(style["shots"], 1)
        self.assertFalse(style["has_audio"])
        self.assertTrue(style["unsupported"])

    def test_frame_and_cover_are_real_images(self):
        source = self._make_source("cover.mp4", audio="none")
        file_id, _ = self._upload(source)
        status, frame = self._post(
            "/api/frame", json.dumps(
                {"file_id": file_id, "t": 3.0}).encode(),
            "application/json")
        self.assertEqual(status, 200, frame)
        with urllib.request.urlopen(
            f"http://127.0.0.1:{self.port}{frame['url']}", timeout=60
        ) as response:
            frame_bytes = response.read()
        self.assertTrue(frame_bytes.startswith(b"\xff\xd8"))
        status, cover = self._post(
            "/api/cover", json.dumps(
                {"file_id": file_id, "t": 3.0, "title": "வணக்கம் Hello",
                 "position": "bottom"}).encode(),
            "application/json")
        self.assertEqual(status, 200, cover)
        with urllib.request.urlopen(
            f"http://127.0.0.1:{self.port}{cover['url']}", timeout=60
        ) as response:
            cover_bytes = response.read()
        self.assertTrue(cover_bytes.startswith(b"\x89PNG"))
        # Title pixels change the image: cover differs from bare frame.
        self.assertNotEqual(len(cover_bytes), len(frame_bytes))
        status, bad = self._post(
            "/api/cover", json.dumps(
                {"file_id": file_id, "t": 99.0, "title": "x"}).encode(),
            "application/json")
        self.assertEqual(status, 400, bad)

    def test_silence_taxonomy_over_http(self):
        source = self._make_source("tax-silent.mp4", audio="silent")
        file_id, _ = self._upload(source)
        client = mock.Mock()
        client.transcribe.side_effect = AssertionError("must not call STT")
        with mock.patch.object(
            server_module.APP, "client", return_value=client
        ):
            status, obj = self._post(
                "/api/transcribe",
                json.dumps({"file_id": file_id}).encode(),
                "application/json")
        self.assertEqual(status, 422, obj)
        self.assertEqual(obj["diagnosis"]["class"], "silence")
        self.assertTrue(obj["diagnosis"]["suggest_titles"])

    def test_music_like_audio_reports_no_speech(self):
        source = self._make_source("tax-tone.mp4", audio="tone")
        file_id, _ = self._upload(source)
        client = mock.Mock()
        client.transcribe.return_value = {"transcript": "", "turns": []}
        with mock.patch.object(
            server_module.APP, "client", return_value=client
        ):
            status, obj = self._post(
                "/api/transcribe",
                json.dumps({"file_id": file_id}).encode(),
                "application/json")
        self.assertEqual(status, 422, obj)
        self.assertEqual(
            obj["diagnosis"]["class"], "no_speech_detected"
        )

    def test_sfx_tail_does_not_extend_output(self):
        source = self._make_source("tail.mp4", audio="none")
        file_id, _ = self._upload(source)
        status, obj = self._post(
            "/api/render", json.dumps({
                "file_id": file_id,
                "plan": {"operations": [
                    {"op": "trim", "start": 0.0, "end": 5.0},
                    {"op": "sfx", "asset": "pop", "at": 4.9},
                ]},
            }).encode(), "application/json")
        self.assertEqual(status, 200, obj)
        with urllib.request.urlopen(
            f"http://127.0.0.1:{self.port}{obj['url']}", timeout=60
        ) as response:
            out = os.path.join(self.tmp, "tail-out.mp4")
            with open(out, "wb") as handle:
                handle.write(response.read())
        duration = float(self._probe(out)["format"]["duration"])
        self.assertGreater(duration, 4.4)
        self.assertLess(duration, 5.6)


if __name__ == "__main__":
    unittest.main()
