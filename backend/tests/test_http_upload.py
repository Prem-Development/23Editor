"""Upload-path regression tests.

Two layers:
- ChunkedDecoderTest exercises the chunked reader with BytesIO (no
  sockets, runs everywhere).
- LiveUploadTest drives the real HTTP server over 127.0.0.1 with real
  multipart/JSON/chunked/raw requests. It skips cleanly where listening
  sockets are blocked.
"""
import io
import json
import os
import shutil
import socket
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

import server as server_module
from server import ApiError, Handler, TimeoutHTTPServer, read_chunked_body


class ChunkedDecoderTest(unittest.TestCase):
    def test_decodes_chunks_extensions_and_trailers(self):
        raw = (
            b"4;ext=1\r\nWiki\r\n"
            b"5\r\npedia\r\n"
            b"0\r\nX-Trailer: yes\r\n\r\n"
        )
        self.assertEqual(read_chunked_body(io.BytesIO(raw), 1024), b"Wikipedia")

    def test_empty_body(self):
        self.assertEqual(read_chunked_body(io.BytesIO(b"0\r\n\r\n"), 1024), b"")

    def test_rejects_over_limit_before_buffering(self):
        with self.assertRaises(ApiError) as ctx:
            read_chunked_body(io.BytesIO(b"14\r\n" + b"x" * 20), 10)
        self.assertEqual(ctx.exception.status, 413)

    def test_rejects_bad_hex(self):
        with self.assertRaises(ApiError) as ctx:
            read_chunked_body(io.BytesIO(b"zz\r\n"), 1024)
        self.assertEqual(ctx.exception.status, 400)

    def test_rejects_truncated_data(self):
        with self.assertRaises(ApiError) as ctx:
            read_chunked_body(io.BytesIO(b"a\r\nabc"), 1024)
        self.assertEqual(ctx.exception.status, 400)

    def test_rejects_missing_chunk_crlf(self):
        with self.assertRaises(ApiError) as ctx:
            read_chunked_body(io.BytesIO(b"3\r\nabcXX"), 1024)
        self.assertEqual(ctx.exception.status, 400)


class LiveUploadTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            cls.httpd = TimeoutHTTPServer(("127.0.0.1", 0), Handler)
        except OSError:
            raise unittest.SkipTest(
                "listening sockets are blocked in this environment"
            )
        cls.tmp = tempfile.mkdtemp(prefix="23editor-test-")
        cls._old_storage = server_module.APP.storage
        server_module.APP.storage = server_module.Storage(
            os.path.join(cls.tmp, "storage")
        )
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(
            target=cls.httpd.serve_forever,
            kwargs={"poll_interval": 0.05},
            daemon=True,
        )
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        server_module.APP.storage = cls._old_storage
        cls.httpd.shutdown()
        cls.httpd.server_close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    # -- helpers ------------------------------------------------------
    def _url(self, path: str) -> str:
        return f"http://127.0.0.1:{self.port}{path}"

    def _post_urllib(
        self, path: str, body: bytes, content_type: str
    ) -> tuple[int, dict]:
        request = urllib.request.Request(
            self._url(path), data=body, method="POST",
            headers={"Content-Type": content_type},
        )
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, json.loads(response.read() or b"{}")
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read() or b"{}")

    def _post_raw(self, head_lines: list[str], body: bytes) -> tuple[int, dict]:
        """Send a hand-built request; tolerates an early server close."""
        sock = socket.create_connection(("127.0.0.1", self.port), timeout=10)
        try:
            head = "".join(head_lines).encode("latin-1")
            try:
                sock.sendall(head + body)
            except (BrokenPipeError, ConnectionResetError):
                pass
            try:
                sock.shutdown(socket.SHUT_WR)
            except OSError:
                pass
            data = b""
            while True:
                piece = sock.recv(65536)
                if not piece:
                    break
                data += piece
        finally:
            sock.close()
        status_line, _, rest = data.partition(b"\r\n")
        try:
            status = int(status_line.decode("latin-1").split()[1])
        except (IndexError, ValueError):
            self.fail(f"Unparseable response: {data[:80]!r}")
        _, _, payload = rest.partition(b"\r\n\r\n")
        return status, json.loads(payload or b"{}")

    # -- tests ----------------------------------------------------------
    def test_valid_audio_upload_roundtrip(self):
        payload = bytes(range(256)) * 8  # 2048 nonzero bytes
        boundary = "testboundary123"
        body = (
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="file"; '
            'filename="voice.m4a"\r\n'
            "Content-Type: application/octet-stream\r\n\r\n"
        ).encode("latin-1") + payload + f"\r\n--{boundary}--\r\n".encode()
        status, obj = self._post_urllib(
            "/api/upload", body, f"multipart/form-data; boundary={boundary}"
        )
        self.assertEqual(status, 200, obj)
        file_id = obj.get("file_id", "")
        self.assertRegex(file_id, r"^[0-9a-f]{32}$")
        self.assertGreaterEqual(obj["timings"]["probe_ms"], 0)
        stored = os.path.join(
            self.tmp, "storage", "uploads", file_id + ".m4a"
        )
        self.assertTrue(os.path.exists(stored))
        self.assertEqual(os.path.getsize(stored), len(payload))
        self.assertGreater(os.path.getsize(stored), 0)

    def test_empty_body_rejected(self):
        # Valid multipart headers isolate the length validation under test.
        status, obj = self._post_urllib(
            "/api/upload", b"", "multipart/form-data; boundary=x"
        )
        self.assertEqual(status, 400)
        self.assertIn("Bad body length", obj.get("error", ""))

    def test_invalid_length_rejected(self):
        status, obj = self._post_raw(
            ["POST /api/upload HTTP/1.1\r\n",
             "Host: x\r\n",
             "Content-Length: abc\r\n",
             "Content-Type: multipart/form-data; boundary=x\r\n\r\n"],
            b"{}",
        )
        self.assertEqual(status, 400)
        self.assertIn("Invalid Content-Length", obj.get("error", ""))

    def test_negative_length_rejected(self):
        status, obj = self._post_raw(
            ["POST /api/upload HTTP/1.1\r\n",
             "Host: x\r\n",
             "Content-Length: -5\r\n",
             "Content-Type: multipart/form-data; boundary=x\r\n\r\n"],
            b"",
        )
        self.assertEqual(status, 400)
        self.assertIn("Bad body length", obj.get("error", ""))

    def test_missing_length_without_chunked_rejected(self):
        status, obj = self._post_raw(
            ["POST /api/transcribe HTTP/1.1\r\n",
             "Host: x\r\n",
             "Content-Type: application/json\r\n\r\n"],
            b'{"file_id": "x"}',
        )
        self.assertEqual(status, 400)
        self.assertIn("Bad body length", obj.get("error", ""))

    def test_oversized_claim_rejected_without_reading_body(self):
        status, obj = self._post_raw(
            ["POST /api/upload HTTP/1.1\r\n",
             "Host: x\r\n",
             "Content-Length: 999999999999\r\n",
             "Content-Type: multipart/form-data; boundary=x\r\n\r\n"],
            b"",
        )
        self.assertEqual(status, 413)
        self.assertIn("exceeds", obj.get("error", ""))

    def test_wrong_content_type_rejected_before_body_read(self):
        # Fail-fast order is intentional: a mistyped request is rejected
        # without reading up to 2GB of body first.
        status, obj = self._post_raw(
            ["POST /api/upload HTTP/1.1\r\n",
             "Host: x\r\n",
             "Content-Length: 999999999999\r\n",
             "Content-Type: application/json\r\n\r\n"],
            b"",
        )
        self.assertEqual(status, 400)
        self.assertIn("Expected multipart/form-data", obj.get("error", ""))

    def test_chunked_json_is_parsed(self):
        # Reaches route validation: proves the chunked body was decoded.
        status, obj = self._post_raw(
            ["POST /api/transcribe HTTP/1.1\r\n",
             "Host: x\r\n",
             "Transfer-Encoding: chunked\r\n",
             "Content-Type: application/json\r\n\r\n"],
            b'7\r\n{"x":1}\r\n0\r\n\r\n',
        )
        self.assertEqual(status, 400)
        self.assertIn("file_id", obj.get("error", ""))

    def test_chunked_oversized_rejected(self):
        big = b"300000\r\n"  # 3MB declared, JSON limit is 2MB
        status, obj = self._post_raw(
            ["POST /api/plan HTTP/1.1\r\n",
             "Host: x\r\n",
             "Transfer-Encoding: chunked\r\n",
             "Content-Type: application/json\r\n\r\n"],
            big + b"x" * 1024,
        )
        self.assertEqual(status, 413)
        self.assertIn("exceeds", obj.get("error", ""))

    def test_upload_empty_file_rejected(self):
        boundary = "emptyfile9"
        body = (
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="file"; '
            'filename="voice.m4a"\r\n'
            "Content-Type: application/octet-stream\r\n\r\n"
            f"\r\n--{boundary}--\r\n"
        ).encode("latin-1")
        status, obj = self._post_urllib(
            "/api/upload", body, f"multipart/form-data; boundary={boundary}"
        )
        self.assertEqual(status, 400)
        self.assertIn("empty", obj.get("error", ""))

    def _multipart(self, boundary, file_bytes, filename,
                   label=None, label_first=True):
        head = (
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="label"\r\n\r\n'
            f"{label}\r\n"
            f"--{boundary}\r\n"
        ) if label_first and label is not None else f"--{boundary}\r\n"
        body = head.encode("latin-1")
        body += (
            'Content-Disposition: form-data; name="file"; '
            f'filename="{filename}"\r\n'
            "Content-Type: application/octet-stream\r\n\r\n"
        ).encode("latin-1") + file_bytes
        if not label_first and label is not None:
            body += (
                f"\r\n--{boundary}\r\n"
                'Content-Disposition: form-data; name="label"\r\n\r\n'
                f"{label}"
            ).encode("latin-1")
        body += f"\r\n--{boundary}--\r\n".encode("latin-1")
        return body

    def test_label_first_upload_matches_client(self):
        # The Flutter client sends label before file.
        payload = bytes(range(256)) * 4
        body = self._multipart(
            "label1st", payload, "clip.mp4", label="reference",
            label_first=True,
        )
        status, obj = self._post_urllib(
            "/api/upload", body, "multipart/form-data; boundary=label1st"
        )
        self.assertEqual(status, 200, obj)
        self.assertEqual(obj.get("label"), "reference")
        self.assertEqual(obj.get("bytes"), len(payload))
        self.assertGreaterEqual(obj["timings"]["receive_ms"], 0)
        stored = os.path.join(
            self.tmp, "storage", "uploads", obj["file_id"] + ".mp4"
        )
        with open(stored, "rb") as handle:
            self.assertEqual(handle.read(), payload)

    def test_file_first_upload_keeps_working(self):
        payload = bytes(range(256)) * 4
        body = self._multipart(
            "file1st", payload, "clip.mp4", label="asset",
            label_first=False,
        )
        status, obj = self._post_urllib(
            "/api/upload", body, "multipart/form-data; boundary=file1st"
        )
        self.assertEqual(status, 200, obj)
        self.assertEqual(obj.get("label"), "asset")
        stored = os.path.join(
            self.tmp, "storage", "uploads", obj["file_id"] + ".mp4"
        )
        with open(stored, "rb") as handle:
            self.assertEqual(handle.read(), payload)

    def test_multichunk_body_stays_intact(self):
        # 200KB crosses several 64KB read chunks, so part data is
        # reassembled from fragments; bytes must match exactly.
        payload = bytes((i * 7) % 251 for i in range(200 * 1024))
        body = self._multipart("bigbody7", payload, "clip.mp4")
        status, obj = self._post_urllib(
            "/api/upload", body, "multipart/form-data; boundary=bigbody7"
        )
        self.assertEqual(status, 200, obj)
        self.assertEqual(obj.get("bytes"), len(payload))
        stored = os.path.join(
            self.tmp, "storage", "uploads", obj["file_id"] + ".mp4"
        )
        with open(stored, "rb") as handle:
            self.assertEqual(handle.read(), payload)

    def test_chunked_upload_decodes(self):
        payload = bytes(range(256)) * 4
        body = self._multipart("chunkup1", payload, "clip.mp4")
        wire = b"%X\r\n%s\r\n0\r\n\r\n" % (len(body), body)
        status, obj = self._post_raw(
            ["POST /api/upload HTTP/1.1\r\n",
             "Host: x\r\n",
             "Transfer-Encoding: chunked\r\n",
             "Content-Type: multipart/form-data; boundary=chunkup1\r\n\r\n"],
            wire,
        )
        self.assertEqual(status, 200, obj)
        self.assertEqual(obj.get("bytes"), len(payload))

    def test_bad_label_removes_partial_file(self):
        before = set(os.listdir(os.path.join(self.tmp, "storage", "uploads")))
        body = self._multipart(
            "badlabel", b"data-bytes", "clip.mp4", label="nope",
            label_first=True,
        )
        status, obj = self._post_urllib(
            "/api/upload", body, "multipart/form-data; boundary=badlabel"
        )
        self.assertEqual(status, 400)
        self.assertIn("label must be", obj.get("error", ""))
        after = set(os.listdir(os.path.join(self.tmp, "storage", "uploads")))
        self.assertEqual(after, before)

    def test_bad_extension_stores_nothing(self):
        before = set(os.listdir(os.path.join(self.tmp, "storage", "uploads")))
        body = self._multipart("badext1", b"data-bytes", "clip.exe")
        status, obj = self._post_urllib(
            "/api/upload", body, "multipart/form-data; boundary=badext1"
        )
        self.assertEqual(status, 400)
        self.assertIn("not supported", obj.get("error", ""))
        after = set(os.listdir(os.path.join(self.tmp, "storage", "uploads")))
        self.assertEqual(after, before)


if __name__ == "__main__":
    unittest.main()
