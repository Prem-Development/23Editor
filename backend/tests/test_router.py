"""Router tests: models are called only when actually needed."""
import unittest

from router import decide


class DecideTest(unittest.TestCase):
    def test_text_only_edit_calls_spark_and_render(self):
        decision = decide(
            has_audio=False,
            plan_operations=[{"op": "trim", "start": 0, "end": 5}],
            thumbnail_requested=False,
        )
        self.assertEqual(decision.steps, ("spark_plan", "render"))

    def test_voice_adds_transcription_first(self):
        decision = decide(
            has_audio=True,
            plan_operations=[{"op": "trim", "start": 0, "end": 5}],
            thumbnail_requested=False,
        )
        self.assertEqual(
            decision.steps, ("transcribe", "spark_plan", "render")
        )

    def test_subject_framing_adds_tracking(self):
        decision = decide(
            has_audio=False,
            plan_operations=[{
                "op": "frame_subject", "subject": "dog",
                "start": 0, "end": 5,
            }],
            thumbnail_requested=False,
        )
        self.assertEqual(
            decision.steps, ("spark_plan", "sam_track", "render")
        )

    def test_thumbnail_only_when_explicitly_requested(self):
        plain = decide(
            has_audio=False, plan_operations=[{"op": "trim"}],
            thumbnail_requested=False,
        )
        self.assertNotIn("thumbnail", plain.steps)
        asked = decide(
            has_audio=False, plan_operations=[{"op": "trim"}],
            thumbnail_requested=True,
        )
        self.assertIn("thumbnail", asked.steps)

    def test_wonder_never_routes_by_default(self):
        decision = decide(
            has_audio=False,
            plan_operations=[{"op": "trim"}],
            thumbnail_requested=False,
            plan_failures=5,
        )
        self.assertNotIn("wonder_plan", decision.steps)


if __name__ == "__main__":
    unittest.main()
