"""FFmpeg command-builder tests (pure: nothing is executed)."""
import json
import subprocess
import unittest
from unittest import mock

from ffmpeg_render import (
    FFmpegError,
    build_render_args,
    compute_expected_dims,
    expected_output_duration,
    verify_render_output,
)

MEDIA = {"duration": 60.0, "width": 1920, "height": 1080, "has_audio": True}


class BuilderTest(unittest.TestCase):
    def test_simple_trim_volume_fade(self):
        argv, adjustments = build_render_args(
            {"operations": [
                {"op": "trim", "start": 5, "end": 50},
                {"op": "volume", "gain_db": 3},
                {"op": "fade", "in_sec": 1, "out_sec": 2},
            ]},
            MEDIA, "in.mp4", "out.mp4",
        )
        text = " ".join(argv)
        self.assertIn("trim=start=5.000:end=50.000", text)
        self.assertIn("atrim=start=5.000:end=50.000", text)
        self.assertIn("volume=3.00dB", text)
        self.assertIn("fade=t=in", text)
        self.assertIn("libx264", text)
        self.assertIn("aac", text)  # audio filters force re-encode
        self.assertIn("+faststart", text)
        self.assertEqual(adjustments, [])

    def test_audio_copied_when_untouched(self):
        argv, _ = build_render_args(
            {"operations": [{"op": "resize_pad", "aspect": "original"}]},
            MEDIA, "in.mp4", "out.mp4",
        )
        joined = " ".join(argv)
        self.assertIn("-c:a copy", joined)

    def test_trim_cuts_audio_to_same_interval(self):
        argv, _ = build_render_args(
            {"operations": [{"op": "trim", "start": 0.0, "end": 5.0}]},
            MEDIA, "in.mp4", "out.mp4",
        )
        joined = " ".join(argv)
        # A trim re-encodes the audio with an exact sample cut so it
        # matches the video interval; stream copy cuts at packet
        # boundaries and can drift out of sync.
        self.assertIn("atrim=start=0.000:end=5.000", joined)
        self.assertIn("-c:a aac", joined)
        self.assertNotIn("-c:a copy", joined)

    def test_full_span_trim_is_flagged(self):
        _, adjustments = build_render_args(
            {"operations": [{"op": "trim", "start": 0.0, "end": 60.0}]},
            MEDIA, "in.mp4", "out.mp4",
        )
        self.assertEqual(len(adjustments), 1)
        self.assertIn("full source duration", adjustments[0])

    def test_silent_video_gets_no_audio(self):
        media = dict(MEDIA, has_audio=False)
        argv, _ = build_render_args(
            {"operations": [{"op": "trim", "start": 0, "end": 10}]},
            media, "in.mp4", "out.mp4",
        )
        self.assertIn("-an", argv)

    def test_segmented_subject_framing(self):
        argv, _ = build_render_args(
            {"operations": [
                {"op": "frame_subject", "subject": "dog",
                 "start": 0, "end": 10},
                {"op": "frame_subject", "subject": "dog",
                 "start": 20, "end": 30},
            ]},
            MEDIA, "in.mp4", "out.mp4",
            tracked={0: (100, 100, 400, 400), 1: (200, 100, 400, 400)},
        )
        text = " ".join(argv)
        self.assertIn("crop=400:400:100:100", text)
        self.assertIn("concat=n=2", text)

    def test_missing_window_refuses_to_render(self):
        with self.assertRaises(FFmpegError):
            build_render_args(
                {"operations": [
                    {"op": "frame_subject", "subject": "dog",
                     "start": 0, "end": 10},
                ]},
                MEDIA, "in.mp4", "out.mp4", tracked={},
            )

    def test_refuses_to_overwrite_original(self):
        with self.assertRaises(FFmpegError):
            build_render_args(
                {"operations": [{"op": "trim", "start": 0, "end": 1}]},
                MEDIA, "same.mp4", "same.mp4",
            )

    def test_odd_crop_is_rounded_with_note(self):
        argv, adjustments = build_render_args(
            {"operations": [
                {"op": "frame_subject", "subject": "dog",
                 "start": 0, "end": 10},
            ]},
            MEDIA, "in.mp4", "out.mp4", tracked={0: (1, 1, 101, 101)},
        )
        self.assertIn("crop=100:100:0:0", " ".join(argv))
        self.assertEqual(len(adjustments), 1)


class FirstFiveSecondsTest(unittest.TestCase):
    def test_exact_five_second_trim_argv(self):
        argv, _ = build_render_args(
            {"operations": [{"op": "trim", "start": 0.0, "end": 5.0}]},
            MEDIA, "in.mp4", "out.mp4",
        )
        text = " ".join(argv)
        self.assertIn("trim=start=0.000:end=5.000", text)
        self.assertIn("atrim=start=0.000:end=5.000", text)

    def test_square_pad_chain(self):
        argv, _ = build_render_args(
            {"operations": [{"op": "resize_pad", "aspect": "1:1"}]},
            MEDIA, "in.mp4", "out.mp4",
        )
        text = " ".join(argv)
        self.assertIn("force_original_aspect_ratio=decrease", text)
        self.assertIn("pad=1080:1080", text)

    def test_trim_pad_preserves_original_audio_content(self):
        argv, _ = build_render_args(
            {"operations": [
                {"op": "trim", "start": 0.0, "end": 5.0},
                {"op": "resize_pad", "aspect": "1:1"},
            ]},
            MEDIA, "in.mp4", "out.mp4",
        )
        text = " ".join(argv)
        # Original audio content is preserved via re-encode (no volume /
        # fade / mute ops), cut to the exact trim interval.
        self.assertIn("atrim=start=0.000:end=5.000", text)
        self.assertIn("-c:a aac", text)
        self.assertNotIn("-c:a copy", text)
        self.assertNotIn("volume=", text)
        self.assertNotIn("afade", text)


class NewOpsArgvTest(unittest.TestCase):
    def test_keep_joins_with_concat(self):
        argv, _ = build_render_args(
            {"operations": [
                {"op": "keep", "start": 0.0, "end": 5.0},
                {"op": "keep", "start": 10.0, "end": 12.0},
            ]},
            MEDIA, "in.mp4", "out.mp4",
        )
        text = " ".join(argv)
        self.assertIn("trim=start=0.000:end=5.000", text)
        self.assertIn("trim=start=10.000:end=12.000", text)
        self.assertIn("concat=n=2:v=1:a=0", text)
        self.assertIn("concat=n=2:v=0:a=1", text)

    def test_remove_keeps_complement(self):
        argv, _ = build_render_args(
            {"operations": [{"op": "remove", "start": 2.0, "end": 5.0}]},
            MEDIA, "in.mp4", "out.mp4",
        )
        text = " ".join(argv)
        self.assertIn("trim=start=0.000:end=2.000", text)
        self.assertIn("trim=start=5.000:end=60.000", text)

    def test_zoom_builds_smooth_scale_crop(self):
        argv, _ = build_render_args(
            {"operations": [
                {"op": "trim", "start": 0.0, "end": 10.0},
                {"op": "zoom", "start": 3.0, "end": 5.0, "to": 1.5,
                 "from": 1.0, "x": 0.5, "y": 0.5},
            ]},
            MEDIA, "in.mp4", "out.mp4",
        )
        text = " ".join(argv)
        self.assertIn("eval=frame", text)
        self.assertIn("crop=1920:1080", text)
        self.assertIn("[vzoom]", text)

    def test_grade_vivid_adds_eq(self):
        argv, _ = build_render_args(
            {"operations": [
                {"op": "grade", "preset": "vivid", "intensity": 0.5},
            ]},
            MEDIA, "in.mp4", "out.mp4",
        )
        text = " ".join(argv)
        self.assertIn("eq=saturation=1.450:contrast=1.060", text)

    def test_grade_warm_adds_colorbalance(self):
        argv, _ = build_render_args(
            {"operations": [
                {"op": "grade", "preset": "warm", "intensity": 1.0},
            ]},
            MEDIA, "in.mp4", "out.mp4",
        )
        self.assertIn("colorbalance=", " ".join(argv))

    def test_transition_builds_xfade_chain(self):
        argv, _ = build_render_args(
            {"operations": [
                {"op": "keep", "start": 0.0, "end": 5.0},
                {"op": "keep", "start": 10.0, "end": 15.0},
                {"op": "transition", "type": "fade", "duration": 0.5},
            ]},
            MEDIA, "in.mp4", "out.mp4",
        )
        text = " ".join(argv)
        self.assertIn("xfade=transition=fade:duration=0.500:offset=4.500",
                      text)
        self.assertIn("acrossfade=d=0.500", text)

    def test_text_needs_subtitles_file(self):
        with self.assertRaises(FFmpegError):
            build_render_args(
                {"operations": [dict(
                    op="text", text="Hi", start=0.0, end=1.0,
                    position="bottom", animation="none", size="medium",
                )]},
                MEDIA, "in.mp4", "out.mp4",
            )
        argv, _ = build_render_args(
            {"operations": [dict(
                op="text", text="Hi", start=0.0, end=1.0,
                position="bottom", animation="none", size="medium",
            )]},
            MEDIA, "in.mp4", "out.mp4",
            ass_path="C:/tmp/x.ass", fonts_dir="C:/Windows/Fonts",
        )
        self.assertIn("ass=", " ".join(argv))

    def test_music_mixes_with_ducking(self):
        argv, _ = build_render_args(
            {"operations": [dict(
                op="music", file_id="9dbab62e7bac4965ad44b92aefabd05a",
                volume_db=-12.0, duck=True,
            )]},
            MEDIA, "in.mp4", "out.mp4", music_path="song.m4a",
        )
        text = " ".join(argv)
        self.assertIn("sidechaincompress=", text)
        self.assertIn("amix=inputs=2", text)
        self.assertIn("volume=-12.00dB", text)

    def test_music_without_duck_skips_sidechain(self):
        argv, _ = build_render_args(
            {"operations": [dict(
                op="music", file_id="9dbab62e7bac4965ad44b92aefabd05a",
                volume_db=-12.0, duck=False,
            )]},
            MEDIA, "in.mp4", "out.mp4", music_path="song.m4a",
        )
        text = " ".join(argv)
        self.assertIn("amix=inputs=2", text)
        self.assertNotIn("sidechaincompress", text)

    def test_sfx_delayed_and_mixed(self):
        argv, adjustments = build_render_args(
            {"operations": [
                {"op": "trim", "start": 0.0, "end": 10.0},
                {"op": "sfx", "asset": "pop", "at": 2.0},
            ]},
            MEDIA, "in.mp4", "out.mp4", sfx_paths=["pop.wav"],
        )
        text = " ".join(argv)
        self.assertIn("adelay=2000:all=1", text)
        self.assertIn("amix=inputs=2", text)
        self.assertEqual(adjustments, [])

    def test_cut_sfx_dropped_with_note(self):
        argv, adjustments = build_render_args(
            {"operations": [
                {"op": "trim", "start": 0.0, "end": 5.0},
                {"op": "sfx", "asset": "pop", "at": 9.0},
            ]},
            MEDIA, "in.mp4", "out.mp4", sfx_paths=["pop.wav"],
        )
        self.assertNotIn("adelay", " ".join(argv))
        self.assertEqual(len(adjustments), 1)
        self.assertIn("output end", adjustments[0])
        self.assertIn("output seconds", adjustments[0])

    def test_two_fades_merge(self):
        argv, _ = build_render_args(
            {"operations": [
                {"op": "trim", "start": 0.0, "end": 10.0},
                {"op": "fade", "in_sec": 1.0, "out_sec": 0.0},
                {"op": "fade", "in_sec": 0.0, "out_sec": 2.0},
            ]},
            MEDIA, "in.mp4", "out.mp4",
        )
        text = " ".join(argv)
        self.assertIn("fade=t=in:st=0:d=1.000", text)
        self.assertIn("fade=t=out:st=8.000:d=2.000", text)
        self.assertIn("afade=t=in:st=0:d=1.000", text)

    def test_crop_fit_covers_frame(self):
        argv, _ = build_render_args(
            {"operations": [
                {"op": "resize_pad", "aspect": "1:1", "fit": "crop"},
            ]},
            MEDIA, "in.mp4", "out.mp4",
        )
        text = " ".join(argv)
        self.assertIn("force_original_aspect_ratio=increase", text)
        self.assertIn("crop=1080:1080", text)

    def test_expected_duration_with_transition(self):
        self.assertEqual(
            expected_output_duration(
                {"operations": [
                    {"op": "keep", "start": 0.0, "end": 5.0},
                    {"op": "keep", "start": 10.0, "end": 15.0},
                    {"op": "transition", "type": "fade", "duration": 0.5},
                ]},
                60.0,
            ),
            9.5,
        )

    def test_expected_dims(self):
        self.assertEqual(
            compute_expected_dims(
                {"operations": [
                    {"op": "resize_pad", "aspect": "1:1", "fit": "pad"},
                ]},
                MEDIA,
            ),
            (1080, 1080),
        )
        self.assertEqual(
            compute_expected_dims({"operations": []}, MEDIA),
            (1920, 1080),
        )


def _completed(info):
    return subprocess.CompletedProcess(
        args=["ffprobe"], returncode=0,
        stdout=json.dumps(info), stderr="",
    )


def _probe_json(duration, has_audio):
    streams = [
        {"codec_type": "video", "codec_name": "h264", "width": 320,
         "height": 240, "r_frame_rate": "10/1", "duration": str(duration)},
    ]
    if has_audio:
        streams.append(
            {"codec_type": "audio", "codec_name": "aac",
             "duration": str(duration), "sample_rate": "44100",
             "channels": 2}
        )
    return {"streams": streams, "format": {"duration": str(duration)}}


class ExpectedDurationTest(unittest.TestCase):
    def test_trim_sets_expected_length(self):
        self.assertEqual(
            expected_output_duration(
                {"operations": [{"op": "trim", "start": 0.0, "end": 5.0}]},
                13.0,
            ),
            5.0,
        )

    def test_segments_sum_expected_length(self):
        self.assertEqual(
            expected_output_duration(
                {"operations": [
                    {"op": "frame_subject", "subject": "dog",
                     "start": 0.0, "end": 2.0},
                    {"op": "frame_subject", "subject": "dog",
                     "start": 5.0, "end": 8.0},
                ]},
                13.0,
            ),
            5.0,
        )

    def test_no_trim_keeps_full_length(self):
        self.assertEqual(
            expected_output_duration(
                {"operations": [{"op": "resize_pad", "aspect": "1:1"}]},
                13.0,
            ),
            13.0,
        )


class VerifyRenderOutputTest(unittest.TestCase):
    def test_matching_output_passes(self):
        with mock.patch(
            "ffmpeg_render._run",
            return_value=_completed(_probe_json(5.02, True)),
        ):
            media = verify_render_output(
                "ffprobe", "out.mp4",
                expected_duration=5.0, expect_audio=True,
            )
        self.assertAlmostEqual(media["duration"], 5.02, places=2)
        self.assertTrue(media["has_audio"])

    def test_uncut_output_fails_clearly(self):
        with mock.patch(
            "ffmpeg_render._run",
            return_value=_completed(_probe_json(13.0, True)),
        ):
            with self.assertRaisesRegex(
                FFmpegError, "expected about 5.0s but the output is 13.0s"
            ):
                verify_render_output(
                    "ffprobe", "out.mp4",
                    expected_duration=5.0, expect_audio=True,
                )

    def test_missing_audio_fails_clearly(self):
        with mock.patch(
            "ffmpeg_render._run",
            return_value=_completed(_probe_json(5.0, False)),
        ):
            with self.assertRaisesRegex(FFmpegError, "no audio stream"):
                verify_render_output(
                    "ffprobe", "out.mp4",
                    expected_duration=5.0, expect_audio=True,
                )


if __name__ == "__main__":
    unittest.main()
