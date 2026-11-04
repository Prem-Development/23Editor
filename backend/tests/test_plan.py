"""Editing-plan validator tests."""
import json
import unittest
from unittest import mock

import server as server_module
from meta_client import MetaAPIError
from plan_schema import (
    EDIT_PLAN_JSON_SCHEMA,
    MediaInfo,
    build_plan_prompt,
    canonical_aspect,
    normalize_plan_aspects,
    validate_plan,
)

MEDIA = MediaInfo(duration=60.0, width=1920, height=1080, fps=30.0,
                  has_audio=True)


class ValidatePlanTest(unittest.TestCase):
    def test_accepts_simple_trim_and_aspect(self):
        result = validate_plan(
            {"operations": [
                {"op": "trim", "start": 5, "end": 50},
                {"op": "resize_pad", "aspect": "9:16"},
            ]},
            MEDIA,
        )
        self.assertTrue(result.ok, result.errors)
        self.assertIsNotNone(result.plan)

    def test_rejects_unknown_operation(self):
        result = validate_plan({"operations": [{"op": "teleport"}]}, MEDIA)
        self.assertFalse(result.ok)
        self.assertTrue(any("teleport" in e for e in result.errors))

    def test_rejects_trim_beyond_duration(self):
        result = validate_plan(
            {"operations": [{"op": "trim", "start": 0, "end": 61}]}, MEDIA
        )
        self.assertFalse(result.ok)

    def test_rejects_crop_outside_frame(self):
        result = validate_plan(
            {"operations": [{"op": "crop", "x": 1800, "y": 0, "w": 200, "h": 200}]},
            MEDIA,
        )
        self.assertFalse(result.ok)

    def test_rejects_crop_plus_subject_framing(self):
        result = validate_plan(
            {"operations": [
                {"op": "crop", "x": 0, "y": 0, "w": 100, "h": 100},
                {"op": "frame_subject", "subject": "dog",
                 "start": 0, "end": 10},
            ]},
            MEDIA,
        )
        self.assertFalse(result.ok)

    def test_rejects_overlapping_segments(self):
        result = validate_plan(
            {"operations": [
                {"op": "frame_subject", "subject": "dog",
                 "start": 0, "end": 10},
                {"op": "frame_subject", "subject": "dog",
                 "start": 5, "end": 15},
            ]},
            MEDIA,
        )
        self.assertFalse(result.ok)

    def test_warns_when_segments_do_not_cover_range(self):
        result = validate_plan(
            {"operations": [
                {"op": "frame_subject", "subject": "dog",
                 "start": 10, "end": 20},
            ]},
            MEDIA,
        )
        self.assertTrue(result.ok, result.errors)
        self.assertTrue(any("cut" in w for w in result.warnings))

    def test_rejects_volume_without_audio(self):
        silent = MediaInfo(duration=10.0, width=640, height=480,
                           has_audio=False)
        result = validate_plan(
            {"operations": [{"op": "volume", "gain_db": 3}]}, silent
        )
        self.assertFalse(result.ok)

    def test_rejects_empty_operations(self):
        result = validate_plan({"operations": []}, MEDIA)
        self.assertFalse(result.ok)


FIRST_FIVE_SECONDS = (
    "keep only the first 5 seconds and preserve original audio"
)


class PlanPromptTest(unittest.TestCase):
    def test_first_five_seconds_prompt_names_required_fields(self):
        media = MediaInfo(duration=30.0, width=1920, height=1080,
                          fps=30.0, has_audio=True)
        messages = build_plan_prompt(
            FIRST_FIVE_SECONDS, media, style="", aspect_ratio="1:1"
        )
        system = str(messages[0]["content"])
        user = str(messages[1]["content"])
        self.assertIn("trim needs numeric start/end seconds", system)
        self.assertIn('"start": 0.0', system)
        self.assertIn('"end": 5.0', system)
        self.assertIn("Requested aspect: 1:1", user)
        self.assertIn(FIRST_FIVE_SECONDS, user)

    def test_strict_schema_requires_per_op_fields(self):
        items = EDIT_PLAN_JSON_SCHEMA["properties"]["operations"]["items"]
        self.assertIn("anyOf", items)
        branches = {
            branch["properties"]["op"]["enum"][0]: branch
            for branch in items["anyOf"]
        }
        self.assertEqual(
            set(branches),
            {"trim", "crop", "frame_subject", "resize_pad", "volume", "fade",
             "keep", "remove", "text", "zoom", "grade", "transition",
             "captions", "music", "sfx"},
        )
        self.assertIn("start", branches["trim"]["required"])
        self.assertIn("end", branches["trim"]["required"])
        self.assertIn("aspect", branches["resize_pad"]["required"])
        blob = json.dumps(EDIT_PLAN_JSON_SCHEMA)
        self.assertNotIn("oneOf", blob)
        self.assertNotIn("allOf", blob)
        self.assertNotIn("$ref", blob)
        # Root stays a plain object per the API docs.
        self.assertNotIn("anyOf", EDIT_PLAN_JSON_SCHEMA)


class AspectAliasTest(unittest.TestCase):
    def test_square_normalizes_to_1_1(self):
        plan = {"operations": [
            {"op": "trim", "start": 0.0, "end": 5.0},
            {"op": "resize_pad", "aspect": "square"},
        ]}
        normalized, applied = normalize_plan_aspects(plan)
        self.assertEqual(normalized["operations"][1]["aspect"], "1:1")
        self.assertEqual(len(applied), 1)
        self.assertIn("square", applied[0])
        self.assertIn("1:1", applied[0])

    def test_matching_is_case_insensitive(self):
        self.assertEqual(canonical_aspect("SQUARE"), "1:1")
        self.assertEqual(canonical_aspect("Vertical"), "9:16")

    def test_unknown_alias_left_for_strict_validation(self):
        plan = {"operations": [{"op": "resize_pad", "aspect": "circle"}]}
        normalized, applied = normalize_plan_aspects(plan)
        self.assertEqual(normalized["operations"][0]["aspect"], "circle")
        self.assertEqual(applied, [])
        result = validate_plan(normalized, MEDIA)
        self.assertFalse(result.ok)
        self.assertEqual(result.errors, [
            "operations[0].aspect must be one of original, 9:16, 16:9, 1:1."
        ])

    def test_canonical_aspect_rejects_garbage(self):
        with self.assertRaises(ValueError):
            canonical_aspect("bogus")
        with self.assertRaises(ValueError):
            canonical_aspect(None)


class SparkPlanWiringTest(unittest.TestCase):
    def _client_returning(self, payload):
        client = mock.Mock()
        client.chat_completions.return_value = {
            "choices": [{"message": {"content": json.dumps(payload)}}]
        }
        return client

    def _media(self):
        return MediaInfo(duration=30.0, width=1920, height=1080,
                         fps=30.0, has_audio=True)

    def _run_plan(self, client):
        with mock.patch.object(
            server_module.APP, "client", return_value=client
        ):
            return server_module._spark_plan(
                FIRST_FIVE_SECONDS, self._media(), "", "1:1",
                "muse-spark-1.3",
            )

    def test_first_five_seconds_with_1_1_validates(self):
        payload = {"operations": [
            {"op": "trim", "start": 0.0, "end": 5.0},
            {"op": "resize_pad", "aspect": "square"},
        ]}
        plan, warnings, errors = self._run_plan(
            self._client_returning(payload)
        )
        self.assertEqual(errors, [])
        self.assertEqual(plan["operations"][0]["start"], 0.0)
        self.assertEqual(plan["operations"][0]["end"], 5.0)
        self.assertEqual(plan["operations"][1]["aspect"], "1:1")
        self.assertTrue(any("square" in w for w in warnings))
        # No audio ops: original voice is preserved (stream copy at render).
        ops = [op["op"] for op in plan["operations"]]
        self.assertNotIn("volume", ops)

    def test_strict_schema_is_sent_first(self):
        payload = {"operations": [
            {"op": "trim", "start": 0.0, "end": 5.0}
        ]}
        client = self._client_returning(payload)
        self._run_plan(client)
        sent = client.chat_completions.call_args.kwargs["response_format"]
        self.assertIn("anyOf", json.dumps(sent))

    def test_malformed_trim_reports_exact_error(self):
        client = self._client_returning({"operations": [{"op": "trim"}]})
        plan, _, errors = self._run_plan(client)
        self.assertEqual(plan, {})
        self.assertEqual(
            errors, ["operations[0] needs numeric start and end."]
        )

    def test_string_times_rejected_not_coerced(self):
        payload = {"operations": [
            {"op": "trim", "start": "0", "end": "5"}
        ]}
        plan, _, errors = self._run_plan(self._client_returning(payload))
        self.assertEqual(plan, {})
        self.assertEqual(
            errors, ["operations[0] needs numeric start and end."]
        )

    def test_schema_rejection_falls_back_transparently(self):
        payload = {"operations": [
            {"op": "trim", "start": 0.0, "end": 5.0}
        ]}
        client = mock.Mock()
        client.chat_completions.side_effect = [
            MetaAPIError(400, "json_schema not supported here"),
            {"choices": [{"message": {"content": json.dumps(payload)}}]},
        ]
        plan, warnings, errors = self._run_plan(client)
        self.assertEqual(errors, [])
        self.assertEqual(plan["operations"][0]["end"], 5.0)
        self.assertTrue(any("compatibility schema" in w for w in warnings))
        self.assertEqual(client.chat_completions.call_count, 2)

    def test_mixed_keep_remove_merges_to_kept_intervals(self):
        # Live failure: "keep only seconds 0-3 and remove seconds
        # 3-12" made the model emit both modes and planning 422'd
        # with "Choose one cutting mode". The ranges merge instead.
        payload = {"operations": [
            {"op": "keep", "start": 0.0, "end": 3.0},
            {"op": "remove", "start": 3.0, "end": 12.0},
        ]}
        plan, warnings, errors = self._run_plan(
            self._client_returning(payload)
        )
        self.assertEqual(errors, [])
        ops = [(o["op"], o["start"], o["end"])
               for o in plan["operations"]]
        self.assertEqual(ops, [("keep", 0.0, 3.0)])
        self.assertTrue(
            any("Cutting modes combined" in w for w in warnings),
            warnings,
        )

    def test_non_schema_errors_are_not_retried(self):
        client = mock.Mock()
        client.chat_completions.side_effect = MetaAPIError(401, "bad key")
        with mock.patch.object(
            server_module.APP, "client", return_value=client
        ):
            with self.assertRaises(MetaAPIError):
                server_module._spark_plan(
                    FIRST_FIVE_SECONDS, self._media(), "", "1:1",
                    "muse-spark-1.3",
                )
        self.assertEqual(client.chat_completions.call_count, 1)


class FadeRulesTest(unittest.TestCase):
    def test_two_fades_merge_in_and_out(self):
        result = validate_plan({"operations": [
            {"op": "fade", "in_sec": 1, "out_sec": 0},
            {"op": "fade", "in_sec": 0, "out_sec": 1},
        ]}, MEDIA)
        self.assertTrue(result.ok, result.errors)

    def test_three_fades_rejected(self):
        result = validate_plan({"operations": [
            {"op": "fade", "in_sec": 1, "out_sec": 0},
            {"op": "fade", "in_sec": 0, "out_sec": 1},
            {"op": "fade", "in_sec": 1, "out_sec": 1},
        ]}, MEDIA)
        self.assertFalse(result.ok)
        self.assertEqual(
            result.errors, ["At most two fades are supported."]
        )

    def test_prompt_forbids_unrequested_effects(self):
        messages = build_plan_prompt("keep the first 5 seconds", MEDIA)
        system = str(messages[0]["content"])
        self.assertIn(
            "Never add captions, music, sfx, zoom, grade, fade, or volume "
            "unless the instruction explicitly asks",
            system,
        )
        self.assertIn("add only operations the instruction asks for", system)
        self.assertIn("Never add resize_pad", system)


if __name__ == "__main__":
    unittest.main()
