"""Editing-operations tests (pure: no binaries, no network).

Validator rules, timeline remapping, the renderer capability catalog,
and ASS subtitle generation for the Slice 1 operations.
"""
import unittest

from ass_subtitles import build_ass
from plan_schema import (
    OP_CATALOG,
    MediaInfo,
    _CHECKS,
    capability_catalog_text,
    content_ranges,
    kept_ranges,
    output_segments,
    remap_interval_output,
    remap_moment_output,
    validate_plan,
)

MEDIA = MediaInfo(duration=30.0, width=1920, height=1080,
                  fps=30.0, has_audio=True)
SILENT = MediaInfo(duration=30.0, width=1920, height=1080,
                   fps=30.0, has_audio=False)

TEXT = {"op": "text", "text": "Hello", "start": 1.0, "end": 3.0,
        "position": "bottom", "animation": "fade", "size": "medium"}


def check(ops, media=MEDIA):
    return validate_plan({"operations": ops}, media)


class KeepRemoveTest(unittest.TestCase):
    def test_keep_joins_in_order(self):
        result = check([
            {"op": "keep", "start": 10.0, "end": 12.0},
            {"op": "keep", "start": 0.0, "end": 5.0},
        ])
        self.assertTrue(result.ok, result.errors)
        self.assertEqual(
            kept_ranges(result.plan["operations"], 30.0),
            [(0.0, 5.0), (10.0, 12.0)],
        )

    def test_overlapping_keeps_rejected(self):
        result = check([
            {"op": "keep", "start": 0.0, "end": 5.0},
            {"op": "keep", "start": 4.0, "end": 6.0},
        ])
        self.assertFalse(result.ok)
        self.assertIn("must not overlap", " ".join(result.errors))

    def test_remove_complement_ranges(self):
        result = check([{"op": "remove", "start": 5.0, "end": 10.0}])
        self.assertTrue(result.ok, result.errors)
        self.assertEqual(
            kept_ranges(result.plan["operations"], 30.0),
            [(0.0, 5.0), (10.0, 30.0)],
        )

    def test_overlapping_removes_rejected(self):
        result = check([
            {"op": "remove", "start": 0.0, "end": 5.0},
            {"op": "remove", "start": 4.0, "end": 6.0},
        ])
        self.assertFalse(result.ok)

    def test_two_cutting_modes_rejected(self):
        for ops in (
            [{"op": "trim", "start": 0.0, "end": 5.0},
             {"op": "keep", "start": 0.0, "end": 5.0}],
            [{"op": "trim", "start": 0.0, "end": 5.0},
             {"op": "remove", "start": 6.0, "end": 7.0}],
            [{"op": "keep", "start": 0.0, "end": 5.0},
             {"op": "remove", "start": 6.0, "end": 7.0}],
        ):
            result = check(ops)
            self.assertFalse(result.ok, ops)
            self.assertIn("cutting mode", " ".join(result.errors))

    def test_trim_range(self):
        self.assertEqual(
            kept_ranges([{"op": "trim", "start": 2.0, "end": 9.0}], 30.0),
            [(2.0, 9.0)],
        )
        self.assertEqual(kept_ranges([], 30.0), [(0.0, 30.0)])


class RemapTest(unittest.TestCase):
    def test_interval_clips_and_shifts(self):
        out = output_segments(
            [{"op": "keep", "start": 0.0, "end": 5.0},
             {"op": "keep", "start": 10.0, "end": 12.0}], 30.0)
        self.assertEqual(
            remap_interval_output(1.0, 11.0, out),
            [(1.0, 5.0), (5.0, 6.0)],
        )
        self.assertEqual(remap_interval_output(6.0, 9.0, out), [])

    def test_moment_mapping(self):
        out = output_segments(
            [{"op": "trim", "start": 10.0, "end": 20.0}], 30.0)
        self.assertEqual(remap_moment_output(12.0, out), 2.0)
        self.assertIsNone(remap_moment_output(5.0, out))

    def test_transition_shifts_later_pieces(self):
        out = output_segments(
            [{"op": "keep", "start": 0.0, "end": 5.0},
             {"op": "keep", "start": 10.0, "end": 15.0}], 30.0, 0.5)
        self.assertEqual(out, [(0.0, 5.0, 0.0), (10.0, 15.0, 4.5)])
        self.assertEqual(remap_moment_output(11.0, out), 5.5)

    def test_content_ranges_prefer_tracked_segments(self):
        ops = [
            {"op": "trim", "start": 0.0, "end": 20.0},
            {"op": "frame_subject", "subject": "dog",
             "start": 2.0, "end": 4.0},
        ]
        self.assertEqual(content_ranges(ops, 30.0), [(2.0, 4.0)])


class TextOpTest(unittest.TestCase):
    def test_valid_text(self):
        self.assertTrue(check([dict(TEXT)]).ok)

    def test_bad_enums_rejected(self):
        for key, value in (("position", "left"), ("animation", "spin"),
                           ("size", "huge")):
            op = dict(TEXT)
            op[key] = value
            result = check([op])
            self.assertFalse(result.ok, (key, value))

    def test_empty_and_long_text_rejected(self):
        bad = dict(TEXT, text="  ")
        self.assertFalse(check([bad]).ok)
        bad = dict(TEXT, text="x" * 141)
        self.assertFalse(check([bad]).ok)

    def test_text_with_tracking_rejected(self):
        result = check([
            dict(TEXT),
            {"op": "frame_subject", "subject": "dog",
             "start": 0.0, "end": 5.0},
        ])
        self.assertFalse(result.ok)


class ZoomOpTest(unittest.TestCase):
    def test_valid_zoom_fills_defaults(self):
        result = check([
            {"op": "zoom", "start": 3.0, "end": 5.0, "to": 1.5},
        ])
        self.assertTrue(result.ok, result.errors)
        op = result.plan["operations"][0]
        self.assertEqual((op["from"], op["x"], op["y"]), (1.0, 0.5, 0.5))

    def test_equal_from_to_rejected(self):
        result = check([
            {"op": "zoom", "start": 3.0, "end": 5.0, "from": 1.5, "to": 1.5},
        ])
        self.assertFalse(result.ok)

    def test_out_of_range_rejected(self):
        result = check([
            {"op": "zoom", "start": 3.0, "end": 5.0, "to": 4.0},
        ])
        self.assertFalse(result.ok)

    def test_zoom_spanning_plain_cut_is_continuous(self):
        # A zoom across a plain cut renders continuously; the old
        # one-kept-range rejection was the phone failure.
        result = check([
            {"op": "keep", "start": 0.0, "end": 4.0},
            {"op": "keep", "start": 10.0, "end": 14.0},
            {"op": "zoom", "start": 3.0, "end": 11.0, "to": 1.5},
        ])
        self.assertTrue(result.ok, result.errors)

    def test_zoom_crossing_dissolve_asks_to_split(self):
        result = check([
            {"op": "keep", "start": 0.0, "end": 4.0},
            {"op": "keep", "start": 10.0, "end": 14.0},
            {"op": "transition", "type": "fade", "duration": 0.5},
            {"op": "zoom", "start": 3.0, "end": 11.0, "to": 1.5},
        ])
        self.assertFalse(result.ok)
        self.assertIn("split it", " ".join(result.errors))

    def test_overlapping_zooms_rejected(self):
        result = check([
            {"op": "zoom", "start": 1.0, "end": 3.0, "to": 1.5},
            {"op": "zoom", "start": 2.0, "end": 4.0, "to": 1.2},
        ])
        self.assertFalse(result.ok)
        self.assertIn("must not overlap", " ".join(result.errors))

    def test_two_separate_zooms_accepted(self):
        result = check([
            {"op": "zoom", "start": 1.0, "end": 2.0, "to": 1.5},
            {"op": "zoom", "start": 5.0, "end": 7.0, "to": 1.2},
        ])
        self.assertTrue(result.ok, result.errors)

    def test_zoom_cut_entirely_rejected(self):
        result = check([
            {"op": "trim", "start": 0.0, "end": 5.0},
            {"op": "zoom", "start": 8.0, "end": 10.0, "to": 1.5},
        ])
        self.assertFalse(result.ok)
        self.assertIn("was cut", " ".join(result.errors))

    def test_zoom_with_crop_rejected(self):
        result = check([
            {"op": "zoom", "start": 3.0, "end": 5.0, "to": 1.5},
            {"op": "crop", "x": 0, "y": 0, "w": 100, "h": 100},
        ])
        self.assertFalse(result.ok)


class GradeTransitionTest(unittest.TestCase):
    def test_valid_grade(self):
        result = check([
            {"op": "grade", "preset": "warm", "intensity": 0.6},
        ])
        self.assertTrue(result.ok, result.errors)

    def test_bad_grade_rejected(self):
        self.assertFalse(
            check([{"op": "grade", "preset": "neon",
                    "intensity": 0.5}]).ok
        )
        self.assertFalse(
            check([{"op": "grade", "preset": "warm",
                    "intensity": 2.0}]).ok
        )

    def test_transition_needs_joins(self):
        result = check([
            {"op": "trim", "start": 0.0, "end": 5.0},
            {"op": "transition", "type": "fade", "duration": 0.5},
        ])
        self.assertFalse(result.ok)
        self.assertIn("two joined", " ".join(result.errors))

    def test_transition_too_long_rejected(self):
        result = check([
            {"op": "keep", "start": 0.0, "end": 5.0},
            {"op": "keep", "start": 10.0, "end": 10.4},
            {"op": "transition", "type": "fade", "duration": 0.5},
        ])
        self.assertFalse(result.ok)

    def test_valid_transition(self):
        result = check([
            {"op": "keep", "start": 0.0, "end": 5.0},
            {"op": "keep", "start": 10.0, "end": 15.0},
            {"op": "transition", "type": "fade", "duration": 0.5},
        ])
        self.assertTrue(result.ok, result.errors)


class CaptionsOpTest(unittest.TestCase):
    def test_auto_needs_audio(self):
        self.assertTrue(
            check([{"op": "captions", "source": "auto"}]).ok
        )
        result = check([{"op": "captions", "source": "auto"}], SILENT)
        self.assertFalse(result.ok)

    def test_source_and_segments_rejected(self):
        result = check([{
            "op": "captions", "source": "auto",
            "segments": [{"start": 0.0, "end": 1.0, "text": "hi"}],
        }])
        self.assertFalse(result.ok)

    def test_explicit_segments_validated(self):
        good = [{"start": 0.0, "end": 1.5, "text": "வணக்கம் hello"}]
        self.assertTrue(
            check([{"op": "captions", "segments": good}]).ok
        )
        bad = [{"start": 5.0, "end": 1.5, "text": "hi"}]
        self.assertFalse(
            check([{"op": "captions", "segments": bad}]).ok
        )


class MusicSfxOpTest(unittest.TestCase):
    FILE_ID = "9dbab62e7bac4965ad44b92aefabd05a"

    def test_valid_music(self):
        result = check([{
            "op": "music", "file_id": self.FILE_ID,
            "volume_db": -12.0, "duck": True,
        }])
        self.assertTrue(result.ok, result.errors)

    def test_bad_music_rejected(self):
        self.assertFalse(check([{
            "op": "music", "file_id": "nope",
            "volume_db": -12.0, "duck": True,
        }]).ok)
        self.assertFalse(check([{
            "op": "music", "file_id": self.FILE_ID,
            "volume_db": 6.0, "duck": False,
        }]).ok)
        silent_duck = check([{
            "op": "music", "file_id": self.FILE_ID,
            "volume_db": -12.0, "duck": True,
        }], SILENT)
        # Ducking degrades to a warning on silent footage instead of
        # blocking the whole edit; the music still renders.
        self.assertTrue(silent_duck.ok, silent_duck.errors)
        self.assertTrue(
            any("duck" in w for w in silent_duck.warnings),
            silent_duck.warnings,
        )

    def test_valid_sfx_asset(self):
        result = check([{"op": "sfx", "asset": "pop", "at": 2.0}])
        self.assertTrue(result.ok, result.errors)

    def test_valid_sfx_file(self):
        result = check([{
            "op": "sfx", "file_id": self.FILE_ID, "at": 2.0,
        }])
        self.assertTrue(result.ok, result.errors)

    def test_sfx_needs_exactly_one_source(self):
        self.assertFalse(check([{"op": "sfx", "at": 2.0}]).ok)
        self.assertFalse(check([{
            "op": "sfx", "asset": "pop", "file_id": self.FILE_ID, "at": 2.0,
        }]).ok)
        self.assertFalse(
            check([{"op": "sfx", "asset": "laser", "at": 2.0}]).ok
        )
        self.assertFalse(
            check([{"op": "sfx", "asset": "pop", "at": 99.0}]).ok
        )


class FitUnsupportedTest(unittest.TestCase):
    def test_fit_defaults_to_pad(self):
        result = check([{"op": "resize_pad", "aspect": "1:1"}])
        self.assertTrue(result.ok, result.errors)
        self.assertEqual(result.plan["operations"][0]["fit"], "pad")

    def test_bad_fit_rejected(self):
        result = check([{"op": "resize_pad", "aspect": "1:1", "fit": "fill"}])
        self.assertFalse(result.ok)

    def test_unsupported_list_passes_through(self):
        result = validate_plan({
            "operations": [{"op": "trim", "start": 0.0, "end": 5.0}],
            "unsupported": ["slow motion"],
        }, MEDIA)
        self.assertTrue(result.ok, result.errors)
        self.assertEqual(result.plan["unsupported"], ["slow motion"])

    def test_bad_unsupported_rejected(self):
        result = validate_plan({
            "operations": [{"op": "trim", "start": 0.0, "end": 5.0}],
            "unsupported": "slow motion",
        }, MEDIA)
        self.assertFalse(result.ok)


class CatalogTest(unittest.TestCase):
    def test_every_check_has_catalog_entry(self):
        names = {entry["op"] for entry in OP_CATALOG}
        self.assertEqual(names, set(_CHECKS))

    def test_catalog_text_names_every_op(self):
        text = capability_catalog_text()
        for name in _CHECKS:
            self.assertIn(f"- {name}:", text)


class AssBuilderTest(unittest.TestCase):
    def test_overlay_events_with_animation(self):
        doc = build_ass([{
            "start": 0.0, "end": 2.0, "text": "My day",
            "position": "top", "size": "large", "animation": "fade",
        }], 640, 480)
        self.assertIn("PlayResX: 640", doc)
        self.assertIn("PlayResY: 480", doc)
        self.assertIn("Nirmala UI", doc)
        self.assertIn("\\an8", doc)
        self.assertIn("\\fad(", doc)
        self.assertIn("My day", doc)
        self.assertIn("0:00:00.00,0:00:02.00", doc)

    def test_slide_and_tamil_text(self):
        doc = build_ass([{
            "start": 1.0, "end": 4.0, "text": "வணக்கம்",
            "position": "bottom", "size": "medium", "animation": "slide",
        }], 640, 480)
        self.assertIn("\\an2", doc)
        self.assertIn("\\move(", doc)
        self.assertIn("வணக்கம்", doc)

    def test_braces_are_neutralized(self):
        doc = build_ass([{
            "start": 0.0, "end": 1.0, "text": "{\\an8}hi",
            "position": "center", "size": "small", "animation": "none",
        }], 640, 480)
        self.assertIn("(\\an8)hi", doc)


if __name__ == "__main__":
    unittest.main()
