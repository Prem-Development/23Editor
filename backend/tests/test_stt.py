"""STT request/parsing tests (schemas verified from official docs)."""
import unittest

from stt import build_request_part, canonical_languages, parse_transcript


class RequestPartTest(unittest.TestCase):
    def test_defaults_to_endpointing_wav(self):
        part = build_request_part()
        self.assertEqual(part["mode"], "ENDPOINTING")
        self.assertEqual(part["model"], "muse-voice-transcribe-1.0")
        self.assertEqual(part["audioEncoding"], "WAV")

    def test_accepts_tamil_and_english_bias(self):
        part = build_request_part(
            mode="PUSH_TO_TALK", languages=["tamil", "ENGLISH"]
        )
        self.assertEqual(part["languageBias"], ["Tamil", "English"])

    def test_rejects_sinhala_with_supported_list(self):
        with self.assertRaises(ValueError) as ctx:
            canonical_languages(["Sinhala"])
        self.assertIn("Tamil", str(ctx.exception))

    def test_rejects_unknown_mode(self):
        with self.assertRaises(ValueError):
            build_request_part(mode="NOPE")


class ParseTest(unittest.TestCase):
    def test_parses_transcript_and_turns(self):
        parsed = parse_transcript({
            "transcript": "Hello world",
            "turns": [
                {"turnId": 0, "transcript": "Hello world"},
                {"turnId": 1, "transcript": "Bye", "speaker": "B",
                 "startMs": 1000},
            ],
        })
        self.assertEqual(parsed["text"], "Hello world")
        turns = parsed["turns"]
        self.assertEqual(len(turns), 2)
        self.assertEqual(turns[0]["turn_id"], 0)
        self.assertEqual(turns[1]["speaker"], "B")
        self.assertEqual(turns[1]["raw"]["startMs"], 1000)

    def test_requires_transcript(self):
        with self.assertRaises(ValueError):
            parse_transcript({"turns": []})


if __name__ == "__main__":
    unittest.main()
