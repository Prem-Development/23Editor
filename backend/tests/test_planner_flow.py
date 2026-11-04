"""Planner-flow tests (pure: Spark is stubbed, never called).

Covers style-effect injection, clarification responses, prompt catalog
content, and the gated visual-analysis step with its fallbacks.
"""
import copy
import os
import shutil
import tempfile
import unittest
from unittest import mock

import server as server_module
from meta_client import MetaAPIError
from plan_schema import build_plan_prompt, MediaInfo
from server import ApiError, route_plan

MEDIA = {"duration": 30.0, "width": 640, "height": 480,
         "fps": 30.0, "has_audio": True}

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


def _plan_request(instruction, style="", aspect="original"):
    return {"media": dict(MEDIA), "instruction": instruction,
            "style": style, "aspect_ratio": aspect}


def _spark_returning(payload, warnings=None):
    return (copy.deepcopy(payload), list(warnings or []), [])


class StyleEffectsTest(unittest.TestCase):
    def _run(self, payload, style, aspect="original"):
        with mock.patch.object(
            server_module, "_spark_plan",
            return_value=_spark_returning(payload),
        ):
            return route_plan(_plan_request("make it pop", style, aspect))

    def test_cinematic_adds_grade_and_fade(self):
        out = self._run(
            {"operations": [{"op": "trim", "start": 0.0, "end": 5.0}],
             "notes": ""},
            "Cinematic",
        )
        ops = [o["op"] for o in out["plan"]["operations"]]
        self.assertIn("grade", ops)
        self.assertIn("fade", ops)
        self.assertTrue(
            any("Cinematic" in w for w in out["adjustments"]),
            out["adjustments"],
        )
        self.assertEqual(out["warnings"], [])

    def test_unrequested_model_grade_yields_to_preset(self):
        # "make it pop" never asks for grading, so the model's mono
        # suggestion is left out and the explicit Product Ad preset
        # applies its vivid grade instead.
        out = self._run(
            {"operations": [
                {"op": "trim", "start": 0.0, "end": 5.0},
                {"op": "grade", "preset": "mono", "intensity": 0.8},
            ], "notes": ""},
            "Product Ad",
        )
        grades = [o for o in out["plan"]["operations"]
                  if o["op"] == "grade"]
        self.assertEqual(len(grades), 1)
        self.assertEqual(grades[0]["preset"], "vivid")
        self.assertEqual(grades[0]["origin"], "preset")
        # The preset's grade supersedes the stripped model grade: the
        # summary names the applied effect once, with its source.
        self.assertTrue(
            any("Product Ad" in w for w in out["adjustments"]),
            out["adjustments"],
        )
        self.assertFalse(
            any("left out" in w for w in out["adjustments"]),
            out["adjustments"],
        )
        self.assertEqual(out["warnings"], [])

    def test_requested_grade_is_not_duplicated(self):
        with mock.patch.object(
            server_module, "_spark_plan",
            return_value=_spark_returning(
                {"operations": [
                    {"op": "trim", "start": 0.0, "end": 5.0},
                    {"op": "grade", "preset": "mono", "intensity": 0.8},
                ], "notes": ""}
            ),
        ):
            out = route_plan(_plan_request(
                "make it black and white", "Product Ad",
            ))
        grades = [o for o in out["plan"]["operations"]
                  if o["op"] == "grade"]
        self.assertEqual(len(grades), 1)
        self.assertEqual(grades[0]["preset"], "mono")
        self.assertEqual(grades[0]["origin"], "instruction")

    def test_social_reel_frames_vertical_when_unset(self):
        out = self._run(
            {"operations": [{"op": "trim", "start": 0.0, "end": 5.0}],
             "notes": ""},
            "Social Reel",
        )
        resizes = [o for o in out["plan"]["operations"]
                   if o["op"] == "resize_pad"]
        self.assertEqual(len(resizes), 1)
        self.assertEqual(resizes[0]["aspect"], "9:16")

    def test_social_reel_keeps_user_aspect(self):
        # An explicitly chosen ratio is enforced deterministically; the
        # preset must not override it with its own 9:16 frame.
        out = self._run(
            {"operations": [{"op": "trim", "start": 0.0, "end": 5.0}],
             "notes": ""},
            "Social Reel", aspect="1:1",
        )
        resizes = [o for o in out["plan"]["operations"]
                   if o["op"] == "resize_pad"]
        self.assertEqual(len(resizes), 1)
        self.assertEqual(resizes[0]["aspect"], "1:1")
        self.assertEqual(resizes[0]["fit"], "pad")

    def test_tutorial_adds_no_captions(self):
        # Presets must never silently enable captions; Tutorial guides
        # planning only and adds no automatic operations.
        out = self._run(
            {"operations": [{"op": "trim", "start": 0.0, "end": 5.0}],
             "notes": ""},
            "Tutorial",
        )
        ops = [o["op"] for o in out["plan"]["operations"]]
        self.assertEqual(ops, ["trim"])

    def test_cleared_style_adds_nothing(self):
        out = self._run(
            {"operations": [{"op": "trim", "start": 0.0, "end": 5.0}],
             "notes": ""},
            "",
        )
        ops = [o["op"] for o in out["plan"]["operations"]]
        self.assertEqual(ops, ["trim"])

    def test_cinematic_fade_named_once_with_source(self):
        # The phone showed "fades were omitted" next to "Cinematic
        # added a fade". The preset fade supersedes the stripped
        # model fade: one fade op, one fade message, no warning.
        out = self._run(
            {"operations": [
                {"op": "trim", "start": 0.0, "end": 5.0},
                {"op": "fade", "in_sec": 1.0, "out_sec": 1.0},
            ], "notes": ""},
            "Cinematic",
        )
        fades = [o for o in out["plan"]["operations"]
                 if o["op"] == "fade"]
        self.assertEqual(len(fades), 1)
        self.assertEqual(fades[0]["origin"], "preset")
        fade_notes = [w for w in out["adjustments"]
                      if "fade" in w.lower()]
        self.assertEqual(len(fade_notes), 1, out["adjustments"])
        self.assertIn("Cinematic", fade_notes[0])
        self.assertEqual(out["warnings"], [])

    def test_social_reel_resize_supersedes_frame_note(self):
        out = self._run(
            {"operations": [
                {"op": "trim", "start": 0.0, "end": 5.0},
                {"op": "resize_pad", "aspect": "16:9", "fit": "pad"},
            ], "notes": ""},
            "Social Reel",
        )
        resizes = [o for o in out["plan"]["operations"]
                   if o["op"] == "resize_pad"]
        self.assertEqual(len(resizes), 1)
        self.assertEqual(resizes[0]["aspect"], "9:16")
        self.assertFalse(
            any("Removed the planner" in w
                for w in out["adjustments"]),
            out["adjustments"],
        )
        self.assertTrue(
            any("Social Reel" in w for w in out["adjustments"]),
            out["adjustments"],
        )

    def test_explicit_cut_note_survives_preset_resize(self):
        # The resize supersede markers must not eat the unrelated
        # explicit-cut note, which also mentions "planner's".
        with mock.patch.object(
            server_module, "_spark_plan",
            return_value=_spark_returning(
                {"operations": [
                    {"op": "trim", "start": 0.0, "end": 5.0},
                    {"op": "resize_pad", "aspect": "16:9", "fit": "pad"},
                ], "notes": ""}
            ),
        ):
            body = _plan_request("make it pop", "Social Reel")
            body["explicit_cuts"] = {
                "mode": "keep", "ranges": [[0.0, 5.0]],
            }
            out = route_plan(body)
        self.assertTrue(
            any("planner's cut" in w for w in out["adjustments"]),
            out["adjustments"],
        )

    def test_spark_notes_split_warnings_from_adjustments(self):
        # A schema-fallback note stays a warning; an aspect-alias
        # rewrite is routine info.
        with mock.patch.object(
            server_module, "_spark_plan",
            return_value=_spark_returning(
                {"operations": [
                    {"op": "trim", "start": 0.0, "end": 5.0}],
                 "notes": ""},
                warnings=[
                    "Strict plan schema was rejected; retried with the "
                    "compatibility schema.",
                    "operations[0].aspect 'square' → '1:1'.",
                ],
            ),
        ):
            out = route_plan(_plan_request("keep 5 seconds"))
        self.assertTrue(
            any("compatibility schema" in w for w in out["warnings"]),
            out["warnings"],
        )
        self.assertTrue(
            any("square" in w for w in out["adjustments"]),
            out["adjustments"],
        )

    def test_plan_reports_timings(self):
        out = self._run(
            {"operations": [{"op": "trim", "start": 0.0, "end": 5.0}],
             "notes": ""},
            "",
        )
        timings = out["timings"]
        self.assertIn("visual_analysis_ms", timings)
        self.assertIn("model_ms", timings)
        self.assertEqual(timings["visual_analysis_ms"], 0)
        self.assertGreaterEqual(timings["model_ms"], 0)
        self.assertEqual(timings["model_roundtrips"], 1)

    def test_wonder_retry_counts_roundtrips(self):
        good = {"operations": [
            {"op": "trim", "start": 0.0, "end": 5.0}], "notes": ""}
        with mock.patch.object(
            server_module, "_spark_plan",
            side_effect=[
                ({"operations": []}, [], ["bad plan"]),
                _spark_returning(good),
            ],
        ):
            body = _plan_request("keep 5 seconds")
            body["allow_wonder"] = True
            out = route_plan(body)
        self.assertEqual(out["timings"]["model_roundtrips"], 2)

    def test_schema_fallback_counts_roundtrips(self):
        with mock.patch.object(
            server_module, "_spark_plan",
            return_value=_spark_returning(
                {"operations": [
                    {"op": "trim", "start": 0.0, "end": 5.0}],
                 "notes": ""},
                warnings=["Strict plan schema was rejected; retried "
                          "with the compatibility schema."],
            ),
        ):
            out = route_plan(_plan_request("keep 5 seconds"))
        self.assertEqual(out["timings"]["model_roundtrips"], 2)

    def test_validation_warnings_reach_the_summary(self):
        # Tracked segments that leave part of the kept range uncovered
        # need a review: validation warnings must surface.
        out = self._run(
            {"operations": [
                {"op": "trim", "start": 0.0, "end": 10.0},
                {"op": "frame_subject", "subject": "person",
                 "start": 0.0, "end": 5.0},
            ], "notes": ""},
            "",
        )
        self.assertTrue(
            any("uncovered" in w for w in out["warnings"]),
            (out["warnings"], out["adjustments"]),
        )


class ClarificationTest(unittest.TestCase):
    def test_minutes_request_asks_before_planning(self):
        with mock.patch.object(
            server_module, "_spark_plan",
            side_effect=AssertionError("must not call Spark"),
        ):
            with self.assertRaises(ApiError) as ctx:
                route_plan(_plan_request("keep only the first 5 minutes"))
        self.assertEqual(ctx.exception.status, 422)
        self.assertIn("seconds", ctx.exception.message)
        self.assertIn("300", ctx.exception.message)

    def test_noop_plan_asks_for_clarification(self):
        with mock.patch.object(
            server_module, "_spark_plan",
            return_value=_spark_returning(
                {"operations": [
                    {"op": "resize_pad", "aspect": "original"},
                ], "notes": ""}
            ),
        ):
            with self.assertRaises(ApiError) as ctx:
                route_plan(_plan_request("do something nice"))
        self.assertEqual(ctx.exception.status, 422)
        self.assertIn("could not tell what to change", ctx.exception.message)

    def test_unsupported_effects_are_reported(self):
        with mock.patch.object(
            server_module, "_spark_plan",
            return_value=_spark_returning(
                {"operations": [
                    {"op": "trim", "start": 0.0, "end": 5.0},
                ], "notes": "", "unsupported": ["slow motion"]}
            ),
        ):
            out = route_plan(_plan_request("trim it and add slow motion"))
        self.assertEqual(out["plan"]["unsupported"], ["slow motion"])


class PromptCatalogTest(unittest.TestCase):
    def test_catalog_lists_new_operations(self):
        media = MediaInfo(duration=30.0, width=640, height=480)
        messages = build_plan_prompt("hello", media)
        system = str(messages[0]["content"])
        for op in ("keep", "remove", "text", "zoom", "grade", "transition",
                   "captions", "music", "sfx"):
            self.assertIn(f"- {op}:", system)
        self.assertIn("source-footage seconds", system)

    def test_footage_notes_are_appended(self):
        media = MediaInfo(duration=30.0, width=640, height=480)
        messages = build_plan_prompt("hi", media, footage_notes="dog at 2s")
        user = str(messages[1]["content"])
        self.assertIn("dog at 2s", user)

    def test_clean_take_cutting_rules(self):
        media = MediaInfo(duration=30.0, width=640, height=480)
        system = str(build_plan_prompt("clean this up", media)[0]["content"])
        for phrase in (
            "evidence agrees",
            "Never delete a moment merely",
            "preserve",
            "intentional laughter",
            "meaningful pauses",
            "uncertain",
        ):
            self.assertIn(phrase, system)


class VisualAnalysisTest(unittest.TestCase):
    def _media(self):
        return MediaInfo(duration=30.0, width=640, height=480,
                         fps=30.0, has_audio=True)

    def test_frames_and_transcript_shape_the_request(self):
        client = mock.Mock()
        client.chat_completions.return_value = {
            "choices": [{"message": {"content": "product visible at 4s"}}]
        }
        storage = mock.Mock()
        storage.get.return_value = {"path": "/tmp/clip.mp4"}
        storage.tmp = "/tmp"
        with mock.patch.object(server_module.APP, "storage", storage), \
                mock.patch.object(server_module.APP, "client",
                                  return_value=client), \
                mock.patch.object(
                    server_module, "_extract_frame") as extract, \
                mock.patch("builtins.open",
                           mock.mock_open(read_data=b"jpeg-bytes")), \
                mock.patch("os.remove"), \
                mock.patch("os.path.exists", return_value=True):
            notes, warnings = server_module._maybe_analyze_footage(
                {"file_id": "abc"}, self._media(),
                "zoom when the product appears",
            )
        self.assertEqual(notes, "product visible at 4s")
        self.assertTrue(any("sampled frames" in w for w in warnings))
        self.assertEqual(extract.call_count, 6)
        sent = client.chat_completions.call_args[0][1]
        blocks = sent[1]["content"]
        images = [b for b in blocks if b.get("type") == "image_url"]
        self.assertEqual(len(images), 6)
        self.assertTrue(
            images[0]["image_url"]["url"].startswith("data:image/jpeg;base64,")
        )

    def test_api_failure_degrades_to_warning(self):
        client = mock.Mock()
        client.chat_completions.side_effect = MetaAPIError(400, "no images")
        storage = mock.Mock()
        storage.get.return_value = {"path": "/tmp/clip.mp4"}
        storage.tmp = "/tmp"
        with mock.patch.object(server_module.APP, "storage", storage), \
                mock.patch.object(server_module.APP, "client",
                                  return_value=client), \
                mock.patch.object(
                    server_module, "_extract_frame"), \
                mock.patch("builtins.open",
                           mock.mock_open(read_data=b"jpeg-bytes")), \
                mock.patch("os.remove"), \
                mock.patch("os.path.exists", return_value=True):
            notes, warnings = server_module._maybe_analyze_footage(
                {"file_id": "abc"}, self._media(),
                "zoom when the product appears",
            )
        self.assertEqual(notes, "")
        self.assertTrue(any("unavailable" in w for w in warnings))

    def test_missing_file_skips_silently(self):
        storage = mock.Mock()
        storage.get.return_value = None
        with mock.patch.object(server_module.APP, "storage", storage):
            notes, warnings = server_module._maybe_analyze_footage(
                {"file_id": "abc"}, self._media(),
                "zoom when the product appears",
            )
        self.assertEqual((notes, warnings), ("", []))


class TurnCaptionSegmentsTest(unittest.TestCase):
    def test_timed_turns_keep_times(self):
        segments, approx = server_module._turns_to_caption_segments(
            {"text": "hi bye", "turns": [
                {"text": "hi", "raw": {"startMs": 500, "endMs": 1500}},
                {"text": "bye", "raw": {"startMs": 2000, "endMs": 3000}},
            ]},
            10.0,
        )
        self.assertFalse(approx)
        self.assertEqual(
            [(s["start"], s["end"], s["text"]) for s in segments],
            [(0.5, 1.5, "hi"), (2.0, 3.0, "bye")],
        )

    def test_missing_end_uses_next_start(self):
        segments, approx = server_module._turns_to_caption_segments(
            {"text": "hi bye", "turns": [
                {"text": "hi", "raw": {"startMs": 0}},
                {"text": "bye", "raw": {"startMs": 4000}},
            ]},
            10.0,
        )
        self.assertFalse(approx)
        self.assertEqual(segments[0]["end"], 4.0)
        self.assertEqual(segments[1]["end"], 10.0)

    def test_untimed_turns_split_evenly_as_approximate(self):
        segments, approx = server_module._turns_to_caption_segments(
            {"text": "hi bye", "turns": [
                {"text": "hi", "raw": {}},
                {"text": "bye", "raw": {}},
            ]},
            10.0,
        )
        self.assertTrue(approx)
        self.assertEqual(
            [(s["start"], s["end"]) for s in segments],
            [(0.0, 5.0), (5.0, 10.0)],
        )

    def test_empty_turns_yield_nothing(self):
        segments, _ = server_module._turns_to_caption_segments(
            {"text": "", "turns": []}, 10.0
        )
        self.assertEqual(segments, [])


class RequestedRangesTest(unittest.TestCase):
    def test_ranges_apply_when_model_omits_cut(self):
        # Live failure: "keep only seconds 0-3 and remove seconds
        # 3-12" made the model return no usable ops, and planning
        # 422'd with "I could not tell what to change". The explicit
        # ranges reconcile deterministically instead.
        with mock.patch.object(
            server_module, "_spark_plan",
            return_value=_spark_returning(
                {"operations": [], "notes": ""}),
        ):
            out = route_plan(_plan_request(
                "keep only seconds 0-3 and remove seconds 3-12"))
        ops = [(o["op"], o["start"], o["end"])
               for o in out["plan"]["operations"]]
        self.assertEqual(ops, [("keep", 0.0, 3.0)])
        self.assertTrue(
            any("keep 0-3s" in w for w in out["adjustments"]),
            out["adjustments"],
        )

    def test_section_from_phrasing_applies(self):
        # Live failure: "Keep only the section from 2 seconds to
        # 15 seconds of the original video" 422'd with "I could not
        # tell what to change" while first-N worked. The filler
        # noun and mid-range unit now parse deterministically.
        with mock.patch.object(
            server_module, "_spark_plan",
            return_value=_spark_returning(
                {"operations": [], "notes": ""}),
        ):
            out = route_plan(_plan_request(
                "Keep only the section from 2 seconds to 15 seconds "
                "of the original video"))
        ops = [(o["op"], o["start"], o["end"])
               for o in out["plan"]["operations"]]
        self.assertEqual(ops, [("keep", 2.0, 15.0)])
        self.assertTrue(
            any("2-15s" in w for w in out["adjustments"]),
            out["adjustments"],
        )


class CaptionMergeTest(unittest.TestCase):
    """Duplicate caption tracks merge into one before validation.

    Regression: a live Tamil captions brief made the model emit two
    caption tracks, and planning failed with "Only one captions track
    is supported." All timed lines union into one track so no Tamil
    caption is lost.
    """

    def test_explicit_tracks_union_sorted(self):
        plan = {"operations": [
            {"op": "trim", "start": 0.0, "end": 10.0},
            {"op": "captions", "origin": "instruction", "segments": [
                {"start": 5.0, "end": 7.0, "text": "இரண்டாம் வரி"},
                {"start": 0.5, "end": 2.0, "text": "வணக்கம்"},
            ]},
            {"op": "captions", "origin": "control", "segments": [
                {"start": 0.5, "end": 2.0, "text": "வணக்கம்"},
                {"start": 3.0, "end": 4.5, "text": "எப்படி இருக்கீங்க"},
            ]},
        ]}
        notes = server_module._dedupe_control_captions(plan)
        captions = [o for o in plan["operations"] if o["op"] == "captions"]
        self.assertEqual(len(captions), 1)
        merged = captions[0]
        self.assertNotIn("source", merged)
        self.assertEqual(merged["origin"], "control")
        self.assertEqual(
            [(s["start"], s["end"], s["text"])
             for s in merged["segments"]],
            [(0.5, 2.0, "வணக்கம்"),
             (3.0, 4.5, "எப்படி இருக்கீங்க"),
             (5.0, 7.0, "இரண்டாம் வரி")],
        )
        self.assertTrue(any("3 lines kept" in n for n in notes), notes)

    def test_auto_plus_explicit_keeps_lines(self):
        plan = {"operations": [
            {"op": "captions", "source": "auto", "origin": "instruction"},
            {"op": "captions", "origin": "control", "segments": [
                {"start": 1.0, "end": 2.0, "text": "வணக்கம்"},
            ]},
        ]}
        notes = server_module._dedupe_control_captions(plan)
        captions = [o for o in plan["operations"] if o["op"] == "captions"]
        self.assertEqual(len(captions), 1)
        self.assertNotIn("source", captions[0])
        self.assertEqual(len(captions[0]["segments"]), 1)
        self.assertEqual(captions[0]["origin"], "control")
        self.assertTrue(any("Merged 2" in n for n in notes), notes)

    def test_two_autos_collapse_to_one(self):
        plan = {"operations": [
            {"op": "trim", "start": 0.0, "end": 10.0},
            {"op": "captions", "source": "auto"},
            {"op": "captions", "source": "auto"},
        ]}
        server_module._dedupe_control_captions(plan)
        captions = [o for o in plan["operations"] if o["op"] == "captions"]
        self.assertEqual(len(captions), 1)
        self.assertEqual(captions[0]["source"], "auto")
        self.assertNotIn("segments", captions[0])

    def test_single_track_untouched(self):
        plan = {"operations": [
            {"op": "captions", "source": "auto", "origin": "instruction"},
        ]}
        notes = server_module._dedupe_control_captions(plan)
        self.assertEqual(notes, [])
        self.assertEqual(len(plan["operations"]), 1)

    def test_plan_route_merges_model_duplicate(self):
        # Phone repro: the model returned two caption tracks and the
        # plan failed validation. Now it merges and succeeds.
        payload = {"operations": [
            {"op": "trim", "start": 0.0, "end": 10.0},
            {"op": "captions", "source": "auto"},
            {"op": "captions", "segments": [
                {"start": 1.0, "end": 3.0, "text": "guessed line"},
            ]},
        ], "notes": ""}
        with mock.patch.object(
            server_module, "_spark_plan",
            return_value=_spark_returning(payload),
        ):
            out = route_plan(_plan_request("add captions for the speech"))
        captions = [o for o in out["plan"]["operations"]
                    if o["op"] == "captions"]
        self.assertEqual(len(captions), 1)
        self.assertEqual(captions[0]["source"], "auto")
        self.assertTrue(
            any("Merged 2" in w for w in out["adjustments"]),
            out["adjustments"],
        )
        self.assertFalse(
            any("Only one captions" in w for w in out["warnings"]),
            out["warnings"],
        )


if __name__ == "__main__":
    unittest.main()
