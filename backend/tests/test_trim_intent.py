"""First-N-seconds intent tests (pure: no binaries, no network)."""
import unittest

from trim_intent import (
    ensure_first_seconds_trim,
    minutes_clarification,
    needs_visual_analysis,
    parse_first_seconds,
    required_trim_end,
)


class ParseFirstSecondsTest(unittest.TestCase):
    def test_english_first_five_seconds(self):
        self.assertEqual(
            parse_first_seconds("keep only the first 5 seconds"), 5.0
        )

    def test_english_case_and_punctuation(self):
        self.assertEqual(
            parse_first_seconds("Keep the FIRST 5 Seconds!"), 5.0
        )

    def test_english_number_word(self):
        self.assertEqual(
            parse_first_seconds("keep only the first five seconds"), 5.0
        )

    def test_english_decimal(self):
        self.assertEqual(parse_first_seconds("first 2.5 secs"), 2.5)

    def test_tamil_first_five_seconds(self):
        self.assertEqual(
            parse_first_seconds("முதல் 5 வினாடிகளை மட்டும் வைத்துக்கொள்"),
            5.0,
        )

    def test_tamil_digits(self):
        self.assertEqual(parse_first_seconds("முதல் ௫ வினாடி"), 5.0)

    def test_tamil_number_word(self):
        self.assertEqual(parse_first_seconds("முதல் ஐந்து வினாடிகள்"), 5.0)

    def test_middle_cut_is_not_first_seconds(self):
        self.assertIsNone(
            parse_first_seconds("cut 5 seconds from the middle")
        )

    def test_minutes_are_not_seconds(self):
        self.assertIsNone(parse_first_seconds("keep the first 5 minutes"))

    def test_unit_is_required(self):
        self.assertIsNone(parse_first_seconds("keep the first 5"))

    def test_conflicting_values_are_ambiguous(self):
        self.assertIsNone(
            parse_first_seconds("first 5 seconds, or maybe first 10 seconds")
        )

    def test_zero_is_rejected(self):
        self.assertIsNone(parse_first_seconds("first 0 seconds"))

    def test_empty_is_rejected(self):
        self.assertIsNone(parse_first_seconds(""))
        self.assertIsNone(parse_first_seconds("make it cinematic"))


class MinutesClarificationTest(unittest.TestCase):
    def test_first_five_minutes_asks(self):
        question = minutes_clarification("keep only the first 5 minutes")
        self.assertIsNotNone(question)
        assert question is not None
        self.assertIn("5 seconds", question)
        self.assertIn("300 seconds", question)

    def test_tamil_minutes_asks(self):
        self.assertIsNotNone(
            minutes_clarification("முதல் 5 நிமிடங்கள்")
        )

    def test_seconds_do_not_ask(self):
        self.assertIsNone(
            minutes_clarification("keep only the first 5 seconds")
        )
        self.assertIsNone(minutes_clarification("make it punchier"))


class VisualAnalysisGateTest(unittest.TestCase):
    def test_visual_event_without_time_needs_vision(self):
        self.assertTrue(
            needs_visual_analysis("zoom in when the product appears")
        )

    def test_explicit_time_skips_vision(self):
        self.assertFalse(
            needs_visual_analysis("zoom in between seconds 3 and 5")
        )

    def test_plain_request_skips_vision(self):
        self.assertFalse(
            needs_visual_analysis("keep only the first 5 seconds")
        )
        self.assertFalse(needs_visual_analysis(""))


class RequiredTrimEndTest(unittest.TestCase):
    def test_first_five_of_eleven(self):
        self.assertEqual(
            required_trim_end("keep only the first 5 seconds", 11.0), 5.0
        )

    def test_full_length_request_needs_no_trim(self):
        self.assertIsNone(
            required_trim_end("keep only the first 30 seconds", 11.0)
        )

    def test_ambiguous_instruction_needs_no_trim(self):
        self.assertIsNone(required_trim_end("make it punchier", 11.0))


class EnsureTrimTest(unittest.TestCase):
    def test_omitted_trim_is_added_with_note(self):
        plan = {"operations": [{"op": "resize_pad", "aspect": "9:16"}],
                "notes": ""}
        plan, note = ensure_first_seconds_trim(plan, 5.0, 11.0)
        self.assertEqual(
            plan["operations"][0],
            {"op": "trim", "start": 0.0, "end": 5.0,
             "origin": "instruction"},
        )
        self.assertIsNotNone(note)
        assert note is not None
        self.assertIn("first-5s", note)
        self.assertIn("omitted", note)

    def test_full_length_trim_is_narrowed_with_note(self):
        plan = {"operations": [{"op": "trim", "start": 0.0, "end": 11.0}],
                "notes": ""}
        plan, note = ensure_first_seconds_trim(plan, 5.0, 11.0)
        self.assertEqual(plan["operations"][0]["end"], 5.0)
        self.assertIsNotNone(note)
        assert note is not None
        self.assertIn("full video", note)

    def test_conflicting_partial_trim_is_corrected(self):
        plan = {"operations": [{"op": "trim", "start": 2.0, "end": 7.0}],
                "notes": ""}
        plan, note = ensure_first_seconds_trim(plan, 5.0, 11.0)
        self.assertEqual(plan["operations"][0]["start"], 0.0)
        self.assertEqual(plan["operations"][0]["end"], 5.0)
        self.assertIsNotNone(note)

    def test_exact_trim_is_untouched(self):
        plan = {"operations": [{"op": "trim", "start": 0.0, "end": 5.0}],
                "notes": ""}
        _, note = ensure_first_seconds_trim(plan, 5.0, 11.0)
        self.assertIsNone(note)

    def test_model_keep_is_replaced_by_first_seconds(self):
        # Live failure: typed/voice "first N seconds" did not apply
        # when the model emitted keep/remove; the request was silently
        # ignored. The explicit request now overrides model cuts.
        plan = {"operations": [
            {"op": "keep", "start": 5.0, "end": 15.0},
            {"op": "remove", "start": 18.0, "end": 20.0},
        ], "notes": ""}
        plan, note = ensure_first_seconds_trim(plan, 10.0, 25.0)
        self.assertEqual(
            plan["operations"],
            [{"op": "trim", "start": 0.0, "end": 10.0,
              "origin": "instruction"}],
        )
        self.assertIsNotNone(note)
        assert note is not None
        self.assertIn("replaced", note)

    def test_exact_single_keep_is_untouched(self):
        plan = {"operations": [{"op": "keep", "start": 0.0, "end": 10.0}],
                "notes": ""}
        _, note = ensure_first_seconds_trim(plan, 10.0, 25.0)
        self.assertIsNone(note)


if __name__ == "__main__":
    unittest.main()
