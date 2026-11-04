"""Strict multipart parser tests."""
import io
import unittest

import server as server_module
from server import ApiError, parse_multipart


def body(boundary: bytes) -> bytes:
    return (
        b"--" + boundary + b"\r\n"
        b'Content-Disposition: form-data; name="note"\r\n'
        b"\r\n"
        b"hello\r\n"
        b"--" + boundary + b"\r\n"
        b'Content-Disposition: form-data; name="file"; filename="a.mp4"\r\n'
        b"Content-Type: video/mp4\r\n"
        b"\r\n"
        b"\x00\x01raw-bytes\r\n"
        b"--" + boundary + b"--\r\n"
    )


class MultipartTest(unittest.TestCase):
    def test_parses_fields_and_file(self):
        parts = parse_multipart(body(b"abc123"), b"abc123")
        self.assertEqual([p.name for p in parts], ["note", "file"])
        self.assertEqual(parts[0].data, b"hello")
        self.assertEqual(parts[1].filename, "a.mp4")
        self.assertEqual(parts[1].data, b"\x00\x01raw-bytes")

    def test_rejects_wrong_boundary(self):
        with self.assertRaises(ApiError):
            parse_multipart(body(b"abc123"), b"nope")

    def test_rejects_empty(self):
        with self.assertRaises(ApiError):
            parse_multipart(b"--zz--\r\n", b"zz")


class _StubHandler:
    """Minimal handler surface for stream_multipart_upload."""

    def __init__(self, body: bytes):
        self.headers: dict[str, str] = {
            "Content-Length": str(len(body)),
        }
        self.rfile = io.BytesIO(body)


class _Sink(io.BytesIO):
    """Test sink that keeps its bytes (and close state) readable."""

    def __init__(self):
        super().__init__()
        self.data = b""
        self.closed_ok = False

    def close(self):
        if not self.closed:
            self.data = self.getvalue()
        super().close()
        self.closed_ok = True


class StreamUploadTest(unittest.TestCase):
    def _stream(self, body_bytes, boundary, chunk_mock=None):
        sinks: dict[str, _Sink] = {}

        def open_sink(filename):
            sinks[filename] = _Sink()
            return sinks[filename]

        handler = _StubHandler(body_bytes)
        if chunk_mock is not None:
            handler.rfile = chunk_mock
        result = server_module.stream_multipart_upload(
            handler, boundary, open_sink)
        return result, sinks

    def test_matches_buffered_parser(self):
        payload = body(b"abc123")
        result, sinks = self._stream(payload, b"abc123")
        self.assertEqual(result["filename"], "a.mp4")
        self.assertEqual(sinks["a.mp4"].data, b"\x00\x01raw-bytes")
        self.assertTrue(sinks["a.mp4"].closed_ok)
        self.assertEqual(result["file_bytes"], 11)
        fields = result["fields"]
        assert isinstance(fields, dict)
        self.assertEqual(fields["note"], b"hello")

    def test_one_byte_chunks_keep_boundaries(self):
        # Pathological chunking: every boundary marker is split, so
        # the overlap logic does all the work.
        payload = body(b"abc123")

        class Drip(io.BytesIO):
            def read(self, size=-1):
                return super().read(1)

        result, sinks = self._stream(payload, b"abc123", Drip(payload))
        self.assertEqual(sinks["a.mp4"].data, b"\x00\x01raw-bytes")
        fields = result["fields"]
        assert isinstance(fields, dict)
        self.assertEqual(fields["note"], b"hello")

    def test_label_first_order(self):
        # The Flutter client sends label before file.
        boundary = b"dart9f"
        payload = (
            b"--" + boundary + b"\r\n"
            b'Content-Disposition: form-data; name="label"\r\n'
            b"\r\n"
            b"reference\r\n"
            b"--" + boundary + b"\r\n"
            b'Content-Disposition: form-data; name="file"; '
            b'filename="r.mp4"\r\n'
            b"Content-Type: application/octet-stream\r\n"
            b"\r\n"
            b"videobytes\r\n"
            b"--" + boundary + b"--\r\n"
        )
        result, sinks = self._stream(payload, boundary)
        self.assertEqual(result["filename"], "r.mp4")
        self.assertEqual(sinks["r.mp4"].data, b"videobytes")
        fields = result["fields"]
        assert isinstance(fields, dict)
        self.assertEqual(fields["label"], b"reference")

    def test_last_label_wins(self):
        boundary = b"dup1"
        payload = (
            b"--" + boundary + b"\r\n"
            b'Content-Disposition: form-data; name="label"\r\n'
            b"\r\n"
            b"source\r\n"
            b"--" + boundary + b"\r\n"
            b'Content-Disposition: form-data; name="label"\r\n'
            b"\r\n"
            b"asset\r\n"
            b"--" + boundary + b"\r\n"
            b'Content-Disposition: form-data; name="file"; '
            b'filename="a.mp4"\r\n'
            b"\r\n"
            b"data\r\n"
            b"--" + boundary + b"--\r\n"
        )
        result, _ = self._stream(payload, boundary)
        fields = result["fields"]
        assert isinstance(fields, dict)
        self.assertEqual(fields["label"], b"asset")

    def test_first_file_wins(self):
        boundary = b"two2"
        payload = (
            b"--" + boundary + b"\r\n"
            b'Content-Disposition: form-data; name="file"; '
            b'filename="a.mp4"\r\n'
            b"\r\n"
            b"first\r\n"
            b"--" + boundary + b"\r\n"
            b'Content-Disposition: form-data; name="file"; '
            b'filename="b.mp4"\r\n'
            b"\r\n"
            b"second\r\n"
            b"--" + boundary + b"--\r\n"
        )
        opened = []

        def open_sink(filename):
            opened.append(filename)
            return io.BytesIO()

        result = server_module.stream_multipart_upload(
            _StubHandler(payload), boundary, open_sink)
        self.assertEqual(opened, ["a.mp4"])
        self.assertEqual(result["filename"], "a.mp4")
        self.assertEqual(result["file_bytes"], 5)

    def test_truncated_file_is_malformed(self):
        boundary = b"cut9"
        payload = (
            b"--" + boundary + b"\r\n"
            b'Content-Disposition: form-data; name="file"; '
            b'filename="a.mp4"\r\n'
            b"\r\n"
            b"partial-data-with-no-closing-delimiter"
        )
        with self.assertRaises(ApiError) as ctx:
            self._stream(payload, boundary)
        self.assertIn("Malformed multipart part", str(ctx.exception))

    def test_empty_body_matches_buffered_message(self):
        with self.assertRaises(ApiError) as ctx:
            self._stream(b"no delimiters here", b"zz")
        self.assertIn("Empty multipart body", str(ctx.exception))

    def test_too_many_parts_rejected(self):
        boundary = b"many"
        payload = b"".join(
            b"--" + boundary + b"\r\n"
            b'Content-Disposition: form-data; name="n%d"\r\n'
            b"\r\n"
            b"x\r\n" % i for i in range(11)
        ) + b"--" + boundary + b"--\r\n"
        with self.assertRaises(ApiError) as ctx:
            self._stream(payload, boundary)
        self.assertIn("Too many multipart parts", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
