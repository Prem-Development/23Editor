"""SAM output parsing and subject-window tests (uses the documented example)."""
import unittest

from sam_parse import parse_output_text, union_box, window_for_segment

DOC_LINE = (
    "<0f>0<|box;x1=211;y1=228;x2=270;y2=254;w=320;h=334|>"
    "<|mask;x=0;y=0;data=27,60,~payload|>,"
    "1<|box;x1=155;y1=228;x2=202;y2=254;w=320;h=334|>"
    "<|mask;x=0;y=0;data=27,48,~payload|>"
)


class ParseTest(unittest.TestCase):
    def test_parses_documented_example(self):
        result = parse_output_text(DOC_LINE)
        self.assertEqual(result.warnings, [])
        self.assertEqual(len(result.frames), 1)
        frame = result.frames[0]
        self.assertEqual(frame.frame_index, 0)
        self.assertEqual(set(frame.boxes), {0, 1})
        self.assertEqual(
            (frame.boxes[0].x1, frame.boxes[0].y1,
             frame.boxes[0].x2, frame.boxes[0].y2),
            (211, 228, 270, 254),
        )

    def test_sparse_ids_and_skipped_frames(self):
        result = parse_output_text(
            "<0f>0<|box;x1=1;y1=1;x2=9;y2=9;w=320;h=334|>"
            "<|mask;x=0;y=0;data=2,2,~x|>\n"
            "<2f>2<|box;x1=1;y1=1;x2=9;y2=9;w=320;h=334|>"
            "<|mask;x=0;y=0;data=2,2,~x|>\n"
        )
        self.assertEqual([f.frame_index for f in result.frames], [0, 2])
        self.assertEqual(set(result.frames[1].boxes), {2})

    def test_malformed_line_warns(self):
        result = parse_output_text("not a sam line\n")
        self.assertEqual(result.frames, [])
        self.assertEqual(len(result.warnings), 1)

    def test_union_covers_all_matches(self):
        result = parse_output_text(DOC_LINE)
        box = union_box(result.frames[0].boxes)
        self.assertIsNotNone(box)
        assert box is not None
        self.assertEqual((box.x1, box.y1, box.x2, box.y2),
                         (155, 228, 270, 254))


class WindowTest(unittest.TestCase):
    def _frames(self):
        lines = []
        for index in range(10):
            lines.append(
                f"<{index}f>0<|box;x1=100;y1=100;x2=200;y2=200;"
                f"w=640;h=480|><|mask;x=0;y=0;data=4,4,~x|>"
            )
        return parse_output_text("\n".join(lines)).frames

    def test_window_fits_aspect_and_stays_in_frame(self):
        window, reason = window_for_segment(
            self._frames(), fps=2.0, start=0.0, end=5.0, aspect="9:16"
        )
        self.assertIsNone(reason)
        self.assertIsNotNone(window)
        assert window is not None
        x, y, w, h = window
        self.assertEqual(x % 2, 0)
        self.assertEqual(y % 2, 0)
        self.assertTrue(0 <= x and x + w <= 640)
        self.assertTrue(0 <= y and y + h <= 480)
        self.assertAlmostEqual(w / h, 9 / 16, delta=0.05)

    def test_empty_segment_reports_reason(self):
        window, reason = window_for_segment(
            self._frames(), fps=2.0, start=100.0, end=101.0, aspect="1:1"
        )
        self.assertIsNone(window)
        self.assertIsNotNone(reason)


if __name__ == "__main__":
    unittest.main()
