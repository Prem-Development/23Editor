"""Timeline contracts and explicit user-chosen cuts. Pure tests.

sfx.at is edited-output seconds (the one timeline exception); every
other plan timestamp is source-footage seconds. explicit_cuts in a
plan request replaces the planner's cutting ops so validation runs
against the final ranges. No ffmpeg, sockets, or Meta involved.
"""
import tempfile
import unittest
from unittest import mock

import server as server_module
from ffmpeg_render import build_render_args
from plan_schema import MediaInfo, validate_plan
from server import ApiError, route_plan

MEDIA13 = MediaInfo(
    duration=13.0, width=640, height=360, fps=30.0, has_audio=True
)


class SfxOutputTimelineTest(unittest.TestCase):
    def test_midcut_edited_timestamp_accepted(self):
        result = validate_plan({"operations": [
            {"op": "keep", "start": 0.0, "end": 4.0},
            {"op": "keep", "start": 7.0, "end": 13.0},
            {"op": "sfx", "asset": "pop", "at": 2.0},
        ]}, MEDIA13)
        self.assertTrue(result.ok, result.errors)

    def test_midcut_source_style_timestamp_rejected(self):
        # at=9.0 would be valid source footage, but sfx.at speaks
        # output seconds and the edit is only 10s long... at=9.0 is
        # inside; at=10.0 hits the output end.
        bad = validate_plan({"operations": [
            {"op": "keep", "start": 0.0, "end": 4.0},
            {"op": "keep", "start": 7.0, "end": 13.0},
            {"op": "sfx", "asset": "pop", "at": 10.0},
        ]}, MEDIA13)
        self.assertFalse(bad.ok)
        self.assertIn("output end", " ".join(bad.errors))
        good = validate_plan({"operations": [
            {"op": "keep", "start": 0.0, "end": 4.0},
            {"op": "keep", "start": 7.0, "end": 13.0},
            {"op": "sfx", "asset": "pop", "at": 9.9},
        ]}, MEDIA13)
        self.assertTrue(good.ok, good.errors)

    def test_full_source_endpoint_rejected(self):
        bad = validate_plan({"operations": [
            {"op": "sfx", "asset": "pop", "at": 13.0},
        ]}, MEDIA13)
        self.assertFalse(bad.ok)
        self.assertIn("output seconds", " ".join(bad.errors))

    def test_negative_rejected(self):
        bad = validate_plan({"operations": [
            {"op": "sfx", "asset": "pop", "at": -1.0},
        ]}, MEDIA13)
        self.assertFalse(bad.ok)


class SfxRenderOutputTest(unittest.TestCase):
    def test_mix_uses_output_moment_directly(self):
        argv, adjustments = build_render_args(
            {"operations": [
                {"op": "keep", "start": 7.0, "end": 13.0},
                {"op": "sfx", "asset": "pop", "at": 2.0},
            ]},
            {"duration": 13.0, "width": 640, "height": 360,
             "fps": 30.0, "has_audio": True},
            "in.mp4", "out.mp4", sfx_paths=["pop.wav"],
        )
        text = " ".join(argv)
        # Output second 2.0, NOT remapped to source second 9.0.
        self.assertIn("adelay=2000:all=1", text)
        self.assertNotIn("adelay=9000", text)
        self.assertEqual(adjustments, [])


class ExplicitCutsMergeTest(unittest.TestCase):
    MEDIA = {
        "duration": 13.0, "width": 640, "height": 360,
        "has_audio": True, "fps": 30.0,
    }

    def _plan_with(self, planner_ops, explicit_cuts):
        tmp = tempfile.mkdtemp(prefix="23editor-cuts-")
        old_storage = server_module.APP.storage
        server_module.APP.storage = server_module.Storage(tmp)
        try:
            with mock.patch.object(
                server_module.APP, "media_of",
                return_value=({"path": "src.mp4"}, dict(self.MEDIA)),
            ), mock.patch.object(
                server_module, "_spark_plan",
                return_value=(
                    {"operations": planner_ops, "notes": ""}, [], [],
                ),
            ):
                body: dict[str, object] = {
                    "file_id": "f1",
                    "instruction": "zoom the demo between 8 and 10 seconds",
                    "aspect_ratio": "original",
                    "captions": "off",
                }
                if explicit_cuts is not None:
                    body["explicit_cuts"] = explicit_cuts
                return route_plan(body)
        finally:
            server_module.APP.storage = old_storage

    def test_explicit_keeps_replace_planner_trim(self):
        planner = [
            {"op": "trim", "start": 0.0, "end": 13.0},
            {"op": "zoom", "start": 8.0, "end": 10.0, "to": 1.5},
        ]
        result = self._plan_with(planner, {
            "mode": "keep", "ranges": [[0.0, 4.0], [7.0, 13.0]],
        })
        ops = result["plan"]["operations"]
        kinds = sorted(o["op"] for o in ops)
        self.assertEqual(kinds, ["keep", "keep", "zoom"])
        self.assertTrue(
            any(o.get("origin") == "control"
                for o in ops if o["op"] == "keep"),
            ops,
        )
        self.assertIn(
            "replaced the planner's cut", " ".join(result["adjustments"])
        )

    def test_zoom_outside_explicit_keeps_rejected(self):
        planner = [
            {"op": "trim", "start": 0.0, "end": 13.0},
            {"op": "zoom", "start": 4.5, "end": 5.5, "to": 1.5},
        ]
        with self.assertRaises(ApiError) as ctx:
            self._plan_with(planner, {
                "mode": "keep", "ranges": [[0.0, 4.0], [7.0, 13.0]],
            })
        self.assertEqual(ctx.exception.status, 422)
        self.assertIn("cut from the render", ctx.exception.message)

    def test_explicit_remove_mode(self):
        planner = [{"op": "trim", "start": 0.0, "end": 13.0}]
        result = self._plan_with(planner, {
            "mode": "remove", "ranges": [[4.0, 7.0]],
        })
        kinds = sorted(
            o["op"] for o in result["plan"]["operations"]
        )
        self.assertEqual(kinds, ["remove"])

    def test_malformed_explicit_cuts_rejected(self):
        planner = [{"op": "trim", "start": 0.0, "end": 13.0}]
        for bad in (
            {"mode": "slice", "ranges": [[0.0, 1.0]]},
            {"mode": "keep", "ranges": []},
            {"mode": "keep", "ranges": [["soon", 1.0]]},
            "keep",
        ):
            with self.assertRaises(ApiError) as ctx:
                self._plan_with(planner, bad)
            self.assertEqual(ctx.exception.status, 400, bad)

    def test_no_explicit_cuts_unchanged(self):
        planner = [
            {"op": "trim", "start": 0.0, "end": 13.0},
            {"op": "zoom", "start": 8.0, "end": 10.0, "to": 1.5},
        ]
        result = self._plan_with(planner, None)
        kinds = sorted(o["op"] for o in result["plan"]["operations"])
        self.assertEqual(kinds, ["trim", "zoom"])
        self.assertNotIn(
            "replaced the planner's cut", " ".join(result["adjustments"])
        )


if __name__ == "__main__":
    unittest.main()
