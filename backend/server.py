"""23Editor backend: voice transcribe, Spark editing plans, FFmpeg renders.

Stdlib only (no pip packages). Run from this directory::

    python server.py

Reads backend/.env for META_API_KEY. The key is used for outbound Meta
Model API calls only and is never logged or returned by any endpoint.

Endpoints (all JSON unless noted):
  GET  /status              capability status with truthful labels
  GET  /api/capabilities    renderer-supported ops, assets, and fonts
  POST /api/upload          multipart video/audio upload -> file_id
  POST /api/transcribe      {file_id, mode?, languages?, keywords?}
  POST /api/plan            {file_id?, instruction, style?, aspect_ratio?,
                             media?, allow_wonder?} -> validated plan
  POST /api/render          {file_id, plan} -> rendered mp4 download URL
  POST /api/edit            full pipeline: audio? -> plan -> render
  POST /api/thumbnail       {prompt, size?} -> explicit-only artwork
  GET  /api/files/{id}      download a render or asset
"""
from __future__ import annotations

import base64
import hashlib
import json
import mimetypes
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import traceback
import uuid
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import BinaryIO
from urllib.parse import urlparse

import analyze
from assets import SFX_FILES, ensure_sfx_assets
from ass_subtitles import build_ass, find_tamil_font
from config import load_settings
from ffmpeg_render import (
    ASPECT_TARGETS,
    FFmpegError,
    NO_AUDIO_DURATION_MESSAGE,
    NO_AUDIO_MESSAGE,
    NO_IMAGE_MESSAGE,
    NO_VIDEO_MESSAGE,
    build_join_args,
    build_render_args,
    compute_expected_dims,
    convert_to_wav,
    escape_ass_path,
    expected_output_duration,
    probe_audio,
    probe_image,
    probe_media,
    run_render,
    verify_render_output,
)
from meta_client import (
    MODEL_IMAGE,
    MODEL_SAM,
    MODEL_SPARK,
    MODEL_TRANSCRIBE,
    MODEL_WONDER,
    MetaAPIError,
    MetaClient,
    classify_provider_error,
)
from plan_schema import (
    BANNER_POSITIONS,
    EDIT_PLAN_JSON_SCHEMA,
    EDIT_PLAN_JSON_SCHEMA_COMPAT,
    GRADE_PRESETS,
    INTRO_MAX_SEC,
    INTRO_MIN_SEC,
    MAX_BANNERS,
    OP_CATALOG,
    OUTRO_MAX_SEC,
    OUTRO_MIN_SEC,
    RESIZE_FITS,
    SFX_ASSETS,
    TEXT_ANIMATIONS,
    TEXT_POSITIONS,
    TEXT_SIZES,
    TRANSITION_TYPES,
    MediaInfo,
    build_plan_prompt,
    canonical_aspect,
    content_ranges,
    normalize_plan_aspects,
    output_segments,
    remap_interval_output,
    segments_output_len,
    validate_plan,
)
from router import decide
from sam_parse import parse_output_text, window_for_segment
from stt import (
    MAX_AUDIO_SECONDS,
    MAX_WAV_BYTES,
    build_request_part,
    parse_transcript,
)
from trim_intent import (
    ensure_first_seconds_trim,
    mentions_extra,
    minutes_clarification,
    needs_visual_analysis,
    parse_cut_ranges,
    required_trim_end,
)

# Uploads stream to disk in bounded chunks, so the cap guards disk use,
# not RAM: a genuine 10-minute 1080p phone recording (~1GB) fits.
MAX_UPLOAD_BYTES = 2 * 1024 * 1024 * 1024
MAX_JSON_BYTES = 2 * 1024 * 1024
_STREAM_CHUNK = 65536
_MAX_PART_HEAD = 65536
_MAX_FIELD_BYTES = 65536
INLINE_VIDEO_BYTES = 40 * 1024 * 1024
MAX_JOIN_CLIPS = 8
MAX_JOIN_DURATION_SEC = 1800.0
ALLOWED_EXTENSIONS = {
    ".mp4", ".mov", ".mkv", ".webm",
    ".m4a", ".mp3", ".wav", ".aac", ".ogg", ".opus",
    ".png", ".jpg", ".jpeg", ".webp",
}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}
HEX_ID = re.compile(r"^[0-9a-f]{32}$")

SERVER_BUILD = "1.1.0"

VERIFIED: set[str] = set()  # integrations with a live success this boot
VERIFIED_AT: dict[str, str] = {}  # integration -> ISO timestamp
FAILED: dict[str, dict[str, str]] = {}  # integration -> {at, message}
VERIFY_LOCK = threading.Lock()
FAILED_MESSAGE_LEN = 160


class _Stopwatch:
    """Elapsed-millisecond marks for one request's slow stages.

    Routes record one mark per stage they run (probing, model calls,
    ffmpeg) and return the marks as "timings" so the app can show
    where time went instead of a single total.
    """

    def __init__(self) -> None:
        self.marks: dict[str, int] = {}
        self._starts: dict[str, float] = {}

    def start(self, name: str) -> None:
        self._starts[name] = time.monotonic()

    def stop(self, name: str) -> int:
        elapsed = int(
            (time.monotonic() - self._starts.pop(name, time.monotonic()))
            * 1000
        )
        self.marks[name] = elapsed
        return elapsed

    def as_dict(self) -> dict[str, int]:
        return dict(self.marks)


def _verified_path() -> str:
    return os.path.join(APP.storage.root, "verified.json")


def _failed_path() -> str:
    return os.path.join(APP.storage.root, "failed.json")


def _load_verified() -> None:
    """Merge persisted live-success and live-failure markers.

    Keyed off the active storage dir, so tests with isolated storage
    never read or write the real markers — mocked tests cannot mark
    anything verified or failed.
    """
    try:
        with open(_verified_path(), encoding="utf-8") as handle:
            saved = json.load(handle)
    except (OSError, ValueError):
        saved = None
    if isinstance(saved, dict):
        with VERIFY_LOCK:
            for key, stamp in saved.items():
                if isinstance(key, str) and isinstance(stamp, str):
                    VERIFIED.add(key)
                    VERIFIED_AT.setdefault(key, stamp)
    try:
        with open(_failed_path(), encoding="utf-8") as handle:
            failed = json.load(handle)
    except (OSError, ValueError):
        return
    if not isinstance(failed, dict):
        return
    with VERIFY_LOCK:
        for key, entry in failed.items():
            if (
                isinstance(key, str) and isinstance(entry, dict)
                and isinstance(entry.get("at"), str)
                and isinstance(entry.get("message"), str)
                and key not in FAILED
            ):
                FAILED[key] = {
                    "at": str(entry["at"]),
                    "message": str(entry["message"])[:FAILED_MESSAGE_LEN],
                }


def mark_verified(integration: str) -> None:
    # Preload disk state first: a fresh process starts with empty memory,
    # and writing without loading would clobber other integrations'
    # markers. (Concurrent writers can still race; the next success
    # re-adds any dropped stamp.)
    _load_verified()
    stamp = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())
    with VERIFY_LOCK:
        VERIFIED.add(integration)
        VERIFIED_AT[integration] = stamp
        FAILED.pop(integration, None)
        snapshot = dict(VERIFIED_AT)
        failed_snapshot = {key: dict(entry) for key, entry in FAILED.items()}
    try:
        with open(_verified_path(), "w", encoding="utf-8") as handle:
            json.dump(snapshot, handle)
    except OSError:
        pass  # in-memory markers still hold for this boot
    try:
        with open(_failed_path(), "w", encoding="utf-8") as handle:
            json.dump(failed_snapshot, handle)
    except OSError:
        pass


def mark_failed(integration: str, message: str) -> None:
    """Record a live provider failure. Only real MetaAPIError outcomes
    call this; mocked tests use isolated storage and must save/restore
    the in-memory map around direct calls."""
    _load_verified()
    stamp = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())
    entry = {"at": stamp, "message": str(message)[:FAILED_MESSAGE_LEN]}
    with VERIFY_LOCK:
        FAILED[integration] = entry
        snapshot = {key: dict(val) for key, val in FAILED.items()}
    try:
        with open(_failed_path(), "w", encoding="utf-8") as handle:
            json.dump(snapshot, handle)
    except OSError:
        pass  # in-memory markers still hold for this boot


def is_verified(integration: str) -> bool:
    with VERIFY_LOCK:
        return integration in VERIFIED


def verified_at(integration: str) -> str | None:
    with VERIFY_LOCK:
        return VERIFIED_AT.get(integration)


def failure_of(integration: str) -> dict[str, str] | None:
    with VERIFY_LOCK:
        entry = FAILED.get(integration)
        return dict(entry) if entry is not None else None


class ApiError(Exception):
    def __init__(
        self, status: int, message: str,
        details: dict[str, object] | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.message = message
        self.details = details or {}


# --------------------------------------------------------------------------
# Multipart parsing (strict, stdlib-only)
# --------------------------------------------------------------------------

class Part:
    def __init__(
        self,
        name: str,
        filename: str | None,
        content_type: str,
        data: bytes,
    ) -> None:
        self.name = name
        self.filename = filename
        self.content_type = content_type
        self.data = data


def parse_multipart(body: bytes, boundary: bytes) -> list[Part]:
    """Parse multipart/form-data. Raises ApiError(400) when malformed."""
    if not boundary or len(boundary) > 200 or b"\r" in boundary or b"\n" in boundary:
        raise ApiError(400, "Invalid multipart boundary.")
    delimiter = b"--" + boundary
    raw_parts = body.split(delimiter)
    parts: list[Part] = []
    for chunk in raw_parts[1:]:
        if chunk.startswith(b"--"):
            break  # closing delimiter
        if not chunk.startswith(b"\r\n"):
            raise ApiError(400, "Malformed multipart body.")
        head, sep, data = chunk[2:].partition(b"\r\n\r\n")
        if not sep or not data.endswith(b"\r\n"):
            raise ApiError(400, "Malformed multipart part.")
        data = data[:-2]
        headers: dict[str, str] = {}
        for line in head.decode("latin-1").split("\r\n"):
            if ":" in line:
                key, _, value = line.partition(":")
                headers[key.strip().lower()] = value.strip()
        disposition = headers.get("content-disposition", "")
        if "form-data" not in disposition:
            raise ApiError(400, "Multipart part is not form-data.")
        name_match = re.search(r'name="([^"]*)"', disposition)
        if not name_match:
            raise ApiError(400, "Multipart part has no name.")
        file_match = re.search(r'filename="([^"]*)"', disposition)
        parts.append(
            Part(
                name=name_match.group(1),
                filename=file_match.group(1) if file_match else None,
                content_type=headers.get("content-type", "text/plain"),
                data=data,
            )
        )
        if len(parts) > 10:
            raise ApiError(400, "Too many multipart parts.")
    if not parts:
        raise ApiError(400, "Empty multipart body.")
    return parts


def _parse_part_headers(head: bytes) -> tuple[str, str | None]:
    """Split one part's header block into (name, filename|None).

    Same rules as parse_multipart's inline parsing, except an empty
    filename normalizes to None (the buffered path treats it as
    "no file" by truthiness too).
    """
    headers: dict[str, str] = {}
    for line in head.decode("latin-1").split("\r\n"):
        if ":" in line:
            key, _, value = line.partition(":")
            headers[key.strip().lower()] = value.strip()
    disposition = headers.get("content-disposition", "")
    if "form-data" not in disposition:
        raise ApiError(400, "Multipart part is not form-data.")
    name_match = re.search(r'name="([^"]*)"', disposition)
    if not name_match:
        raise ApiError(400, "Multipart part has no name.")
    file_match = re.search(r'filename="([^"]*)"', disposition)
    filename = file_match.group(1) if file_match else None
    return name_match.group(1), filename or None


def stream_multipart_upload(
    handler: Handler,
    boundary: bytes,
    open_sink: Callable[[str], BinaryIO],
) -> dict[str, object]:
    """Stream one multipart upload to disk with bounded memory.

    ``open_sink(filename)`` validates the name and returns an open
    binary handle for the FIRST file part's bytes; further file parts
    are drained. Returns {"filename", "file_bytes", "fields"}. Raises
    the same ApiError(400/408/413) failures as the buffered path.
    """
    if (not boundary or len(boundary) > 200 or b"\r" in boundary
            or b"\n" in boundary):
        raise ApiError(400, "Invalid multipart boundary.")
    delimiter = b"--" + boundary
    needle = b"\r\n" + delimiter
    buf = bytearray()
    chunks = _iter_request_body(handler, MAX_UPLOAD_BYTES)
    exhausted = False

    def fill(need: int) -> None:
        nonlocal exhausted
        while len(buf) < need and not exhausted:
            try:
                piece = next(chunks)
            except StopIteration:
                exhausted = True
            else:
                buf.extend(piece)

    def fill_until(marker: bytes, cap: int, missing: str) -> int:
        nonlocal exhausted
        while True:
            index = buf.find(marker)
            if index >= 0:
                return index
            if exhausted:
                raise ApiError(400, missing)
            if len(buf) > cap:
                raise ApiError(400, missing)
            try:
                buf.extend(next(chunks))
            except StopIteration:
                exhausted = True

    fields: dict[str, bytes] = {}
    filename: str | None = None
    file_bytes = 0
    sink: BinaryIO | None = None
    parts = 0
    try:
        # Leading preamble (if any) is skipped, as in parse_multipart.
        start = fill_until(
            delimiter, _MAX_PART_HEAD, "Empty multipart body."
        )
        del buf[:start]
        while True:
            fill(len(delimiter) + 2)
            if len(buf) < len(delimiter) + 2:
                raise ApiError(400, "Malformed multipart body.")
            after = bytes(buf[len(delimiter):len(delimiter) + 2])
            if after.startswith(b"--"):
                break  # closing delimiter; trailing bytes ignored
            if after != b"\r\n":
                raise ApiError(400, "Malformed multipart body.")
            del buf[:len(delimiter) + 2]
            head_end = fill_until(
                b"\r\n\r\n", _MAX_PART_HEAD, "Malformed multipart part."
            )
            name, part_filename = _parse_part_headers(bytes(buf[:head_end]))
            del buf[:head_end + 4]
            parts += 1
            if parts > 10:
                raise ApiError(400, "Too many multipart parts.")
            if part_filename and sink is None and filename is None:
                filename = part_filename
                sink = open_sink(part_filename)
            draining = part_filename is not None and (
                sink is None or filename != part_filename)
            if part_filename and not draining:
                assert sink is not None
                while True:
                    index = buf.find(needle)
                    if index >= 0:
                        sink.write(bytes(buf[:index]))
                        file_bytes += index
                        del buf[:index + 2]  # keep delimiter for the loop
                        break
                    if len(buf) > len(needle):
                        sink.write(bytes(buf[:-len(needle)]))
                        file_bytes += len(buf) - len(needle)
                        del buf[:-len(needle)]
                    try:
                        buf.extend(next(chunks))
                    except StopIteration:
                        raise ApiError(
                            400, "Malformed multipart part.") from None
                sink.close()
                sink = None
            else:
                field = bytearray()
                while True:
                    index = buf.find(needle)
                    if index >= 0:
                        field.extend(buf[:index])
                        del buf[:index + 2]
                        break
                    if len(buf) > len(needle):
                        field.extend(buf[:-len(needle)])
                        if len(field) > _MAX_FIELD_BYTES:
                            raise ApiError(
                                400, "Multipart field is too large.")
                        del buf[:-len(needle)]
                    try:
                        buf.extend(next(chunks))
                    except StopIteration:
                        raise ApiError(
                            400, "Malformed multipart part.") from None
                if not draining:
                    # Last part wins, matching the buffered path.
                    fields[name] = bytes(field)
    except socket.timeout:
        raise ApiError(
            408, "Request timed out while reading body.") from None
    finally:
        if sink is not None:
            try:
                sink.close()
            except OSError:
                pass
    if parts == 0:
        raise ApiError(400, "Empty multipart body.")
    return {"filename": filename, "file_bytes": file_bytes,
            "fields": fields}


# --------------------------------------------------------------------------
# Storage
# --------------------------------------------------------------------------

class Storage:
    def __init__(self, root: str) -> None:
        self.root = root
        self.uploads = os.path.join(root, "uploads")
        self.renders = os.path.join(root, "renders")
        self.assets = os.path.join(root, "assets")
        self.tmp = os.path.join(root, "tmp")
        for path in (self.uploads, self.renders, self.assets, self.tmp):
            os.makedirs(path, exist_ok=True)
        self._index_path = os.path.join(root, "index.json")
        self._lock = threading.Lock()
        self._index: dict[str, dict[str, str]] = {}
        try:
            with open(self._index_path, "r", encoding="utf-8") as handle:
                loaded = json.load(handle)
            if isinstance(loaded, dict):
                self._index = loaded
        except (FileNotFoundError, ValueError):
            pass

    def _save(self) -> None:
        tmp_path = self._index_path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as handle:
            json.dump(self._index, handle)
        os.replace(tmp_path, self._index_path)

    def register(self, file_id: str, entry: dict[str, str]) -> None:
        with self._lock:
            self._index[file_id] = entry
            self._save()

    def get(self, file_id: str) -> dict[str, str] | None:
        with self._lock:
            entry = self._index.get(file_id)
            return dict(entry) if entry else None

    def new_id(self) -> str:
        return uuid.uuid4().hex


def safe_extension(filename: str) -> str:
    ext = os.path.splitext(filename or "")[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise ApiError(
            400,
            f"Extension {ext or '(none)'} is not supported. "
            f"Allowed: {', '.join(sorted(ALLOWED_EXTENSIONS))}.",
        )
    return ext


# --------------------------------------------------------------------------
# App context
# --------------------------------------------------------------------------

class App:
    def __init__(self) -> None:
        self.settings = load_settings()
        self.storage = Storage(self.settings.storage_dir)
        self._client: MetaClient | None = None
        self._client_lock = threading.Lock()

    def client(self) -> MetaClient:
        if not self.settings.has_api_key:
            raise ApiError(
                503,
                "META_API_KEY is not set. Add it to backend/.env "
                "(see backend/.env.example).",
            )
        with self._client_lock:
            if self._client is None:
                self._client = MetaClient(
                    self.settings.api_key, self.settings.api_base
                )
            return self._client

    def media_of(self, file_id: str) -> tuple[dict[str, str], dict[str, object]]:
        entry = self.storage.get(file_id)
        if entry is None:
            raise ApiError(404, "Unknown file_id.")
        path = entry["path"]
        if not os.path.exists(path):
            raise ApiError(404, "Stored file is missing.")
        try:
            media = probe_media(self.settings.ffprobe_bin, path)
        except FFmpegError as exc:
            raise ApiError(503, str(exc)) from None
        return entry, media

    def audio_of(self, file_id: str) -> tuple[dict[str, str], dict[str, object]]:
        entry = self.storage.get(file_id)
        if entry is None:
            raise ApiError(404, "Unknown file_id.")
        path = entry["path"]
        if not os.path.exists(path):
            raise ApiError(404, "Stored file is missing.")
        try:
            audio = probe_audio(self.settings.ffprobe_bin, path)
        except FFmpegError as exc:
            # Missing/unreadable audio is the client's media problem (400);
            # a missing/broken tool is a server problem (503).
            if str(exc) in (NO_AUDIO_MESSAGE, NO_AUDIO_DURATION_MESSAGE):
                raise ApiError(400, str(exc)) from None
            raise ApiError(503, str(exc)) from None
        return entry, audio

    def video_of(self, file_id: str) -> tuple[dict[str, str], dict[str, object]]:
        entry = self.storage.get(file_id)
        if entry is None:
            raise ApiError(404, "Unknown file_id.")
        path = entry["path"]
        if not os.path.exists(path):
            raise ApiError(404, "Stored file is missing.")
        try:
            media = probe_media(self.settings.ffprobe_bin, path)
        except FFmpegError as exc:
            if str(exc) == NO_VIDEO_MESSAGE:
                raise ApiError(400, str(exc)) from None
            raise ApiError(503, str(exc)) from None
        if "width" not in media:
            raise ApiError(400, NO_VIDEO_MESSAGE)
        return entry, media

    def image_of(self, file_id: str) -> tuple[dict[str, str], dict[str, object]]:
        entry = self.storage.get(file_id)
        if entry is None:
            raise ApiError(404, "Unknown file_id.")
        path = entry["path"]
        if not os.path.exists(path):
            raise ApiError(404, "Stored file is missing.")
        try:
            dims = probe_image(self.settings.ffprobe_bin, path)
        except FFmpegError as exc:
            # A non-image file is the client's media problem (400);
            # a missing/broken tool is a server problem (503).
            if str(exc) == NO_IMAGE_MESSAGE:
                raise ApiError(400, str(exc)) from None
            raise ApiError(503, str(exc)) from None
        return entry, dims


APP = App()


# --------------------------------------------------------------------------
# Chunked request bodies
# --------------------------------------------------------------------------

def _iter_chunked(rfile, limit: int):
    """Yield decoded chunked-transfer bytes without buffering the body."""
    total = 0
    while True:
        line = rfile.readline(8192)
        if not line:
            raise ApiError(400, "Incomplete chunked body.")
        try:
            size = int(line.decode("ascii").split(";")[0].strip(), 16)
        except (ValueError, UnicodeDecodeError):
            raise ApiError(400, "Malformed chunked body.") from None
        if size < 0:
            raise ApiError(400, "Malformed chunked body.")
        if size == 0:
            while True:  # consume trailers + final CRLF
                trailer = rfile.readline(8192)
                if not trailer or trailer in (b"\r\n", b"\n"):
                    break
            return
        if total + size > limit:
            raise ApiError(413, f"Body exceeds {limit} bytes.")
        remaining = size
        while remaining > 0:
            piece = rfile.read(min(remaining, _STREAM_CHUNK))
            if not piece:
                raise ApiError(400, "Incomplete chunked body.")
            total += len(piece)
            yield piece
            remaining -= len(piece)
        if rfile.read(2) != b"\r\n":
            raise ApiError(400, "Malformed chunked body.")


def read_chunked_body(rfile, limit: int) -> bytes:
    """Decode a Transfer-Encoding: chunked body with a strict byte cap.

    Raises ApiError(400) on malformed input and ApiError(413) when the
    declared or actual bytes exceed ``limit``. Never buffers past the cap.
    """
    return b"".join(_iter_chunked(rfile, limit))


def _iter_request_body(handler: Handler, limit: int):
    """Yield request body bytes in bounded chunks.

    Raises the same ApiErrors as Handler._read_body for bad lengths,
    conflicting/unsupported encodings, and over-limit bodies.
    """
    transfer = handler.headers.get("Transfer-Encoding", "")
    codings = [
        coding.strip().lower()
        for coding in transfer.split(",")
        if coding.strip()
    ]
    has_length = handler.headers.get("Content-Length") is not None
    if "chunked" in codings:
        if has_length:
            raise ApiError(400, "Conflicting length headers.")
        yield from _iter_chunked(handler.rfile, limit)
        return
    if codings:
        raise ApiError(400, "Unsupported Transfer-Encoding.")
    try:
        length = int(handler.headers.get("Content-Length", "0"))
    except ValueError:
        raise ApiError(400, "Invalid Content-Length.") from None
    if length <= 0:
        raise ApiError(400, "Bad body length.")
    if length > limit:
        raise ApiError(413, f"Body exceeds {limit} bytes.")
    remaining = length
    while remaining > 0:
        piece = handler.rfile.read(min(remaining, _STREAM_CHUNK))
        if not piece:
            raise ApiError(400, "Incomplete request body.")
        remaining -= len(piece)
        yield piece


# --------------------------------------------------------------------------
# HTTP handler
# --------------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    server_version = "23EditorBackend/1.0"

    def log_message(self, fmt: str, *args: object) -> None:  # noqa: N802
        sys.stderr.write(f"{self.address_string()} {fmt % args}\n")

    def _send_json(self, status: int, obj: dict[str, object]) -> None:
        data = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _read_body(self, limit: int) -> bytes:
        try:
            return b"".join(_iter_request_body(self, limit))
        except socket.timeout:
            raise ApiError(
                408, "Request timed out while reading body."
            ) from None

    def _read_json(self) -> dict[str, object]:
        raw = self._read_body(MAX_JSON_BYTES)
        try:
            obj = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            raise ApiError(400, "Invalid JSON body.") from None
        if not isinstance(obj, dict):
            raise ApiError(400, "JSON body must be an object.")
        return obj

    def _handle(self, method: str) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        started = time.monotonic()
        try:
            if method == "GET" and path == "/status":
                self._send_json(200, status_payload())
            elif method == "GET" and path == "/api/capabilities":
                self._send_json(200, route_capabilities())
            elif method == "GET" and path.startswith("/api/files/"):
                self._route_download(path[len("/api/files/"):])
            elif method == "POST" and path == "/api/upload":
                self._send_json(200, route_upload(self))
            elif method == "POST" and path == "/api/transcribe":
                self._send_json(200, route_transcribe(self._read_json()))
            elif method == "POST" and path == "/api/plan":
                self._send_json(200, route_plan(self._read_json()))
            elif method == "POST" and path == "/api/join":
                self._send_json(200, route_join(self._read_json()))
            elif method == "POST" and path == "/api/render":
                self._send_json(200, route_render(self._read_json()))
            elif method == "POST" and path == "/api/edit":
                self._send_json(200, route_edit(self._read_json()))
            elif method == "POST" and path == "/api/thumbnail":
                self._send_json(200, route_thumbnail(self._read_json()))
            elif method == "POST" and path == "/api/analyze/start":
                self._send_json(202, route_analyze_start(self._read_json()))
            elif method == "GET" and path.startswith("/api/analyze/"):
                self._send_json(200, route_analyze_get(
                    path[len("/api/analyze/"):]))
            elif method == "DELETE" and path.startswith("/api/analyze/"):
                self._send_json(200, route_analyze_cancel(
                    path[len("/api/analyze/"):]))
            elif method == "POST" and path == "/api/brief":
                self._send_json(200, route_brief(self._read_json()))
            elif method == "POST" and path == "/api/reference/analyze":
                self._send_json(
                    200, route_reference_analyze(self._read_json()))
            elif method == "POST" and path == "/api/frame":
                self._send_json(200, route_frame(self._read_json()))
            elif method == "POST" and path == "/api/cover":
                self._send_json(200, route_cover(self._read_json()))
            else:
                self._send_json(404, {"error": f"Unknown route {path}."})
        except ApiError as exc:
            # Sanitized: messages carry lengths/counts only, never bodies,
            # headers, keys, or audio.
            sys.stderr.write(
                f"  ApiError {exc.status} {exc.message} [{method} {path}]\n"
            )
            self._send_json(
                exc.status, {"error": exc.message, **exc.details}
            )
        except MetaAPIError as exc:
            self._send_json(
                502, {"error": f"Meta API error: {exc.message}",
                      "provider": exc.as_dict()}
            )
        except FFmpegError as exc:
            self._send_json(503, {"error": str(exc)})
        except Exception as exc:  # never leak tracebacks to clients
            traceback.print_exc()
            self._send_json(
                500, {"error": f"{type(exc).__name__}: {exc}"}
            )
        finally:
            elapsed_ms = int((time.monotonic() - started) * 1000)
            sys.stderr.write(f"  {method} {path} {elapsed_ms}ms\n")

    def do_GET(self) -> None:  # noqa: N802
        self._handle("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._handle("POST")

    def do_DELETE(self) -> None:  # noqa: N802
        self._handle("DELETE")

    def _route_download(self, file_id: str) -> None:
        if not HEX_ID.match(file_id):
            self._send_json(404, {"error": "Unknown file."})
            return
        entry = APP.storage.get(file_id)
        if entry is None or not os.path.exists(entry["path"]):
            self._send_json(404, {"error": "Unknown file."})
            return
        mime, _ = mimetypes.guess_type(entry.get("name", "file"))
        size = os.path.getsize(entry["path"])
        self.send_response(200)
        self.send_header("Content-Type", mime or "application/octet-stream")
        self.send_header("Content-Length", str(size))
        self.end_headers()
        with open(entry["path"], "rb") as handle:
            shutil.copyfileobj(handle, self.wfile, length=1024 * 1024)


# --------------------------------------------------------------------------
# Routes
# --------------------------------------------------------------------------

def _tool_version(binary: str) -> tuple[bool, str]:
    import subprocess

    try:
        done = subprocess.run(
            [binary, "-version"], capture_output=True, text=True, timeout=15
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return False, ""
    if done.returncode != 0:
        return False, ""
    first = (done.stdout or "").splitlines()
    return True, first[0][:120] if first else binary


def status_payload() -> dict[str, object]:
    settings = APP.settings
    _load_verified()
    live_models: list[str] = []
    access = "no_key"
    if settings.has_api_key:
        try:
            live_models = APP.client().list_models()
            access = "verified"
        except MetaAPIError as exc:
            access = "auth_error" if exc.status in (401, 403) else "unreachable"
        except Exception:
            access = "unreachable"

    def ladder(integration: str) -> tuple[str, str]:
        """(status, failure_note) for a key-dependent integration.

        A provider failure newer than the last success reports
        provider_error with the failure detail; an older failure stays
        hidden once a later call succeeds. Ties (same second) report
        the failure: a success clears the failure record, so a tie
        means the failure came last.
        """
        if not settings.has_api_key:
            return "unavailable", ""
        failed = failure_of(integration)
        stamp = verified_at(integration)
        if (
            failed is not None
            and (stamp is None or failed["at"] >= stamp)
        ):
            return (
                "provider_error",
                f" Latest attempt failed at {failed['at']}: "
                f"{failed['message']}",
            )
        if is_verified(integration):
            return "verified", ""
        return "implemented_but_untested", ""

    ffmpeg_ok, ffmpeg_ver = _tool_version(settings.ffmpeg_bin)
    probe_ok, probe_ver = _tool_version(settings.ffprobe_bin)

    def row(
        integration: str, detail: str
    ) -> dict[str, object]:
        status, note = ladder(integration)
        return {
            "id": integration, "status": status,
            "detail": detail + note,
        }

    # Wonder stays "planned" until it is actually attempted: it is an
    # off-by-default escalation path, not an untested default.
    wonder_status, wonder_note = (
        ladder("wonder_plan")
        if is_verified("wonder_plan")
        or failure_of("wonder_plan") is not None
        else ("planned", "")
    )
    integrations = [
        row(
            "spark_plan",
            f"Muse Spark editing plans ({MODEL_SPARK}, "
            "JSON-schema validated).",
        ),
        row(
            "voice_transcribe",
            f"Tamil + Tamil-English supported ({MODEL_TRANSCRIBE}); "
            "Sinhala is not in Meta's 25-language list; turn-level "
            "timestamps only.",
        ),
        row(
            "sam_track",
            f"Subject tracking ({MODEL_SAM}) for crop/zoom; runs only "
            "when a plan keeps frame_subject.",
        ),
        row(
            "image_assets",
            f"Muse Image thumbnails ({MODEL_IMAGE}); explicit requests "
            "only, never auto-applied.",
        ),
        {
            "id": "wonder_plan",
            "status": wonder_status,
            "detail": f"Escalation path only ({MODEL_WONDER}), off by "
                      "default; enable per plan with the Workspace "
                      f"'Allow Muse Wonder retry' toggle.{wonder_note}",
        },
        {
            "id": "open_weight",
            "status": "unavailable",
            "detail": "No local GPU here; Spark Open (816B MoE) needs "
                      "datacenter/cloud GPUs and Glimmer 30B needs a GPU. "
                      "Nothing downloaded.",
        },
        {
            "id": "ffmpeg_render",
            "status": "verified" if ffmpeg_ok and probe_ok else "unavailable",
            "detail": ffmpeg_ver if ffmpeg_ok else "ffmpeg not installed.",
        },
        {
            "id": "social_publishing",
            "status": "planned",
            "detail": "Deferred this phase; roadmap kept in ROADMAP.md.",
        },
    ]
    for entry in integrations:
        stamp = verified_at(str(entry["id"]))
        # verified_at persists across restarts in storage/verified.json;
        # only a real successful call writes it, never a mocked test.
        entry["verified_at"] = stamp
    return {
        "service": "23editor-backend",
        "server_build": SERVER_BUILD,
        "api_key_present": settings.has_api_key,
        "api_access": access,
        "models_seen": live_models[:20],
        "ffmpeg": {"present": ffmpeg_ok, "version": ffmpeg_ver},
        "ffprobe": {"present": probe_ok, "version": probe_ver},
        "python": sys.version.split()[0],
        "integrations": integrations,
    }


def route_upload(handler: Handler) -> dict[str, object]:
    content_type = handler.headers.get("Content-Type", "")
    match = re.search(r"boundary=([^;]+)", content_type)
    if not match:
        raise ApiError(400, "Expected multipart/form-data.")
    try:
        boundary = match.group(1).strip().encode("ascii")
    except UnicodeEncodeError:
        raise ApiError(400, "Invalid multipart boundary.") from None
    clock = _Stopwatch()
    clock.start("receive_ms")
    state: dict[str, object] = {}

    def open_sink(filename: str) -> BinaryIO:
        ext = safe_extension(filename or "")
        file_id = APP.storage.new_id()
        path = os.path.join(APP.storage.uploads, file_id + ext)
        handle = open(path, "wb")
        state["file_id"] = file_id
        state["path"] = path
        state["ext"] = ext
        state["filename"] = filename
        return handle

    try:
        streamed = stream_multipart_upload(handler, boundary, open_sink)
    except ApiError:
        partial = state.get("path")
        if isinstance(partial, str) and os.path.exists(partial):
            try:
                os.remove(partial)
            except OSError:
                pass
        raise
    clock.stop("receive_ms")
    filename = streamed["filename"]
    if not isinstance(filename, str) or not filename:
        raise ApiError(400, "Multipart field 'file' is required.")
    if int(streamed["file_bytes"] or 0) <= 0:
        partial = state.get("path")
        if isinstance(partial, str) and os.path.exists(partial):
            try:
                os.remove(partial)
            except OSError:
                pass
        raise ApiError(400, "Uploaded file is empty.")
    file_id = state["file_id"]
    path = state["path"]
    ext = state["ext"]
    assert isinstance(file_id, str) and isinstance(path, str)
    assert isinstance(ext, str)
    fields = streamed["fields"]
    assert isinstance(fields, dict)
    label = "source"
    raw_label = fields.get("label")
    if isinstance(raw_label, bytes):
        label = raw_label.decode("utf-8", "replace").strip().lower()
    if label not in ("source", "reference", "asset"):
        try:
            os.remove(path)
        except OSError:
            pass
        raise ApiError(400, "label must be source, reference, or asset.")
    APP.storage.register(file_id, {
        "path": path,
        "name": os.path.basename(filename or f"upload{ext}"),
        "kind": "upload",
        "label": label,
    })
    payload: dict[str, object] = {
        "file_id": file_id,
        "bytes": int(streamed["file_bytes"] or 0),
        "label": label,
        "media": None,
    }
    clock.start("probe_ms")
    if ext in IMAGE_EXTENSIONS:
        # Still images carry no duration: describe dimensions only.
        try:
            dims = probe_image(APP.settings.ffprobe_bin, path)
        except FFmpegError as exc:
            payload["media_warning"] = str(exc)
        else:
            payload["media"] = {
                "duration": 0.0,
                "width": int(dims["width"]),
                "height": int(dims["height"]),
                "fps": 0.0,
                "has_audio": False,
            }
        clock.stop("probe_ms")
        payload["timings"] = clock.as_dict()
        return payload
    try:
        payload["media"] = probe_media(APP.settings.ffprobe_bin, path)
    except FFmpegError as exc:
        if str(exc) == NO_VIDEO_MESSAGE:
            # Audio-only uploads (voice clips) are legitimate: describe audio.
            try:
                audio = probe_audio(APP.settings.ffprobe_bin, path)
            except FFmpegError as exc2:
                payload["media_warning"] = str(exc2)
            else:
                payload["media"] = {
                    "duration": audio["duration"],
                    "width": 0,
                    "height": 0,
                    "fps": 0.0,
                    "has_audio": True,
                }
        else:
            payload["media_warning"] = str(exc)
    clock.stop("probe_ms")
    payload["timings"] = clock.as_dict()
    return payload


def _resolve_media(body: dict[str, object]) -> tuple[str, dict[str, object]]:
    file_id = body.get("file_id")
    if isinstance(file_id, str) and file_id:
        _, media = APP.media_of(file_id)
        return file_id, media
    raw = body.get("media")
    if not isinstance(raw, dict):
        raise ApiError(400, "Provide file_id or an explicit media object.")
    try:
        duration = float(raw["duration"])
        width = int(raw["width"])
        height = int(raw["height"])
    except (KeyError, TypeError, ValueError):
        raise ApiError(
            400, "media needs numeric duration, width, height."
        ) from None
    if duration <= 0 or width <= 0 or height <= 0:
        raise ApiError(400, "media dimensions must be positive.")
    media = {
        "duration": duration,
        "width": width,
        "height": height,
        "fps": float(raw.get("fps", 30.0) or 30.0),
        "has_audio": bool(raw.get("has_audio", False)),
    }
    return "", media


def route_transcribe(body: dict[str, object]) -> dict[str, object]:
    file_id = body.get("file_id")
    if not isinstance(file_id, str) or not file_id:
        raise ApiError(400, "file_id is required.")
    entry, audio = APP.audio_of(file_id)
    if float(audio["duration"]) > MAX_AUDIO_SECONDS:
        raise ApiError(
            400,
            f"Audio is longer than {MAX_AUDIO_SECONDS // 60} minutes; "
            "split it into shorter clips.",
        )
    mode = str(body.get("mode", "ENDPOINTING"))
    languages = body.get("languages") or []
    keywords = body.get("keywords") or []
    if not isinstance(languages, list) or not isinstance(keywords, list):
        raise ApiError(400, "languages and keywords must be lists.")
    try:
        request_part = build_request_part(
            mode, [str(v) for v in languages], [str(v) for v in keywords]
        )
    except ValueError as exc:
        raise ApiError(400, str(exc)) from None
    wav_path = os.path.join(APP.storage.tmp, f"{uuid.uuid4().hex}.wav")
    clock = _Stopwatch()
    try:
        clock.start("convert_ms")
        info = convert_to_wav(APP.settings.ffmpeg_bin, entry["path"], wav_path)
        clock.stop("convert_ms")
        if int(info["bytes"]) > MAX_WAV_BYTES:
            raise ApiError(
                400, "Converted audio exceeds 32 MB; use a shorter clip."
            )
        clock.start("classify_ms")
        audio_class, audio_reason = analyze.classify_audio(
            analyze.audio_levels(APP.settings.ffmpeg_bin, wav_path)
        )
        clock.stop("classify_ms")
        if audio_class == "silence":
            raise ApiError(
                422, "This audio is silent, so there is nothing to "
                "transcribe. Add a written title instead of captions.",
                details={"diagnosis": {
                    "class": "silence", "detail": audio_reason,
                    "suggest_titles": True,
                }},
            )
        clock.start("stt_ms")
        try:
            parsed = _transcribe_wav_file(wav_path, request_part)
        except MetaAPIError as exc:
            mark_failed("voice_transcribe", exc.message)
            raise _provider_transcribe_error(exc) from None
        finally:
            clock.stop("stt_ms")
        text = str(parsed.get("text", "") or "").strip()
        turns = parsed.get("turns", [])
        heard = bool(text) or any(
            isinstance(t, dict) and str(t.get("text", "") or "").strip()
            for t in turns
        ) if isinstance(turns, list) else False
        if not heard:
            raise ApiError(
                422, "No speech was detected in this audio — it may be "
                "music or background sound. Add a written title instead "
                "of captions.",
                details={"diagnosis": {
                    "class": "no_speech_detected",
                    "detail": ("The provider returned no usable speech; "
                               "loudness alone never counts as speech."),
                    "suggest_titles": True,
                }},
            )
    finally:
        if os.path.exists(wav_path):
            os.unlink(wav_path)
    mark_verified("voice_transcribe")
    return {
        "text": parsed["text"], "turns": parsed["turns"], "mode": mode,
        "approximate": not _turns_have_timestamps(parsed),
        "duration": float(audio["duration"]),
        "diagnosis": {"class": "recognized", "detail": (
            "Speech recognized; timing is "
            + ("approximate (no turn timestamps)."
               if not _turns_have_timestamps(parsed) else
               "turn-level from the provider.")
        )},
        "timings": clock.as_dict(),
    }


def _provider_transcribe_error(exc: MetaAPIError) -> ApiError:
    """Map a provider failure to a diagnosis, never to 'no speech'."""
    blob = f"{exc.message} {exc.type} {exc.code} {exc.param}".lower()
    if "language" in blob or "locale" in blob:
        return ApiError(
            422, "The speech in this audio uses a language the "
            "transcription service did not accept.",
            details={"diagnosis": {
                "class": "unsupported_language",
                "detail": exc.message,
            }},
        )
    return ApiError(
        502, f"Transcription failed at the provider: {exc.message}",
        details={"diagnosis": {
            "class": "provider_failure", "detail": exc.message,
        }},
    )


def _looks_like_schema_rejection(exc: MetaAPIError) -> bool:
    blob = f"{exc.type} {exc.code} {exc.param} {exc.message}".lower()
    return exc.status == 400 and (
        "schema" in blob or "response_format" in blob or "json_schema" in blob
    )


def _spark_plan(
    instruction: str, media: MediaInfo, style: str, aspect: str, model: str,
    footage_notes: str = "",
) -> tuple[dict[str, object], list[str], list[str]]:
    from meta_client import MetaClient as _Client  # local alias, no cycle

    messages = build_plan_prompt(
        instruction, media, style, aspect, footage_notes
    )
    warnings: list[str] = []

    def _format(schema: dict[str, object]) -> dict[str, object]:
        return {
            "type": "json_schema",
            "json_schema": {"name": "EditingPlan", "schema": schema},
        }

    try:
        response = APP.client().chat_completions(
            model,
            messages,
            response_format=_format(EDIT_PLAN_JSON_SCHEMA),
            timeout=180.0,
        )
    except MetaAPIError as exc:
        if not _looks_like_schema_rejection(exc):
            raise
        warnings.append(
            "Strict plan schema was rejected; retried with the "
            "compatibility schema."
        )
        response = APP.client().chat_completions(
            model,
            messages,
            response_format=_format(EDIT_PLAN_JSON_SCHEMA_COMPAT),
            timeout=180.0,
        )
    text = _Client.chat_text(response)
    try:
        obj = json.loads(text)
    except ValueError:
        return {}, warnings, ["The model returned invalid JSON."]
    obj, applied = normalize_plan_aspects(obj)
    warnings.extend(applied)
    cut_note = _normalize_cutting_modes(obj)
    if cut_note is not None:
        warnings.append(cut_note)
    checked = validate_plan(obj, media)
    if not checked.ok:
        return {}, warnings, checked.errors
    assert checked.plan is not None
    return checked.plan, warnings, []


def _tag_instruction_source(plan: dict[str, object]) -> None:
    """Mark every model operation as instruction-sourced for the UI."""
    ops = plan.get("operations")
    if isinstance(ops, list):
        for raw in ops:
            if isinstance(raw, dict):
                raw["origin"] = "instruction"


def _enforce_intent(
    plan: dict[str, object],
    instruction: str,
    captions_flag: str,
    media: MediaInfo,
) -> tuple[list[str], list[str]]:
    """Drop model-suggested extras the user never asked for.

    Precedence: explicit instruction wording and explicit control flags
    override model suggestions. Every removal is reported in notes so
    the app can show what was left out and why. Returns (notes, pending)
    where pending names requests that stay valid but cannot run yet
    (captions with no audio track).
    """
    ops = plan.get("operations")
    assert isinstance(ops, list)
    notes: list[str] = []
    pending: list[str] = []
    kept: list[dict[str, object]] = []
    for raw in ops:
        if not isinstance(raw, dict):
            kept.append(raw)
            continue
        name = raw.get("op")
        if name == "captions":
            if captions_flag == "off":
                notes.append(
                    "Captions are Off, so the planner's caption "
                    "suggestion was left out."
                )
                continue
            if captions_flag != "on" and not mentions_extra(
                instruction, "captions"
            ):
                notes.append(
                    "Captions were not requested, so the planner's "
                    "caption suggestion was left out."
                )
                continue
            if raw.get("segments") is not None:
                raw.pop("segments", None)
                raw["source"] = "auto"
                notes.append(
                    "Caption lines need real speech timing, so the "
                    "render transcribes the kept audio instead of "
                    "using the planner's guessed lines."
                )
            if not media.has_audio:
                pending.append(
                    "Captions need speech, but this video has no audio "
                    "track. The other edits proceed; add captions only "
                    "after choosing footage with sound."
                )
                continue
            kept.append(raw)
        elif name == "music":
            notes.append(
                "Music needs an audio file you attach under 'Captions, "
                "music & effects'; the planner's music suggestion was "
                "left out."
            )
        elif name in ("intro", "outro", "banner"):
            notes.append(
                "Attached images come from the app's cover and artwork "
                "controls; the planner's image suggestion was left out."
            )
        elif name == "sfx" and raw.get("file_id") is not None:
            notes.append(
                "A sound effect needs a bundled asset or an uploaded "
                "file; the planner's file reference was left out."
            )
        elif name == "sfx" and not mentions_extra(instruction, "sfx"):
            notes.append(
                "Sound effects were not requested, so the planner's "
                "sound suggestion was left out."
            )
        elif name == "zoom" and not mentions_extra(instruction, "zoom"):
            notes.append(
                "Zoom was not requested, so the planner's zoom "
                "suggestion was left out."
            )
        elif name == "fade" and not mentions_extra(instruction, "fade"):
            notes.append(
                "Fades were not requested, so the planner's fade "
                "suggestion was left out."
            )
        elif name == "grade" and not mentions_extra(instruction, "grade"):
            notes.append(
                "Color grading was not requested, so the planner's "
                "grade suggestion was left out."
            )
        elif name == "volume" and not mentions_extra(instruction, "volume"):
            notes.append(
                "A volume change was not requested, so the planner's "
                "volume suggestion was left out."
            )
        else:
            kept.append(raw)
    plan["operations"] = kept
    return notes, pending


def _enforce_aspect(
    plan: dict[str, object], aspect: str, fit: str
) -> list[str]:
    """Apply the explicitly chosen aspect ratio and fit deterministically.

    Only model-sourced frames are replaced; preset frames (Social Reel's
    9:16 when Original is selected) are deliberate and stay.
    """
    ops = plan.get("operations")
    assert isinstance(ops, list)
    notes: list[str] = []
    survivors: list[dict[str, object]] = []
    for raw in ops:
        if (
            isinstance(raw, dict)
            and raw.get("op") == "resize_pad"
            and raw.get("origin") == "instruction"
        ):
            claimed = raw.get("aspect")
            if aspect == "original":
                if claimed != "original":
                    notes.append(
                        f"Removed the planner's unrequested {claimed} "
                        "frame; Original keeps the source shape."
                    )
            elif claimed != aspect or raw.get("fit", "pad") != fit:
                notes.append(
                    f"Requested {aspect} ({fit}) replaced the planner's "
                    f"{claimed} frame."
                )
            continue
        survivors.append(raw)
    if aspect != "original":
        survivors.append({
            "op": "resize_pad", "aspect": aspect, "fit": fit,
            "origin": "control",
        })
        notes.append(f"Framed {aspect} ({fit}).")
    plan["operations"] = survivors
    return notes


def _explicit_cut_ops(
    body: dict[str, object],
) -> list[dict[str, object]] | None:
    """Parse user-chosen kept/removed intervals from a plan request.

    Shape: {"mode": "keep"|"remove", "ranges": [[start, end], ...]} in
    source-footage seconds. Bounds/overlap/order are enforced by the
    normal plan validation after the merge; this only rejects
    malformed shapes with a clear 400.
    """
    raw = body.get("explicit_cuts")
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ApiError(400, "explicit_cuts must be an object.")
    mode = raw.get("mode")
    if mode not in ("keep", "remove"):
        raise ApiError(400, "explicit_cuts.mode must be keep or remove.")
    ranges = raw.get("ranges")
    if not isinstance(ranges, list) or not ranges:
        raise ApiError(400, "explicit_cuts.ranges must be a non-empty list.")
    ops: list[dict[str, object]] = []
    for pair in ranges:
        if (
            not isinstance(pair, (list, tuple)) or len(pair) != 2
            or not isinstance(pair[0], (int, float))
            or not isinstance(pair[1], (int, float))
            or isinstance(pair[0], bool) or isinstance(pair[1], bool)
        ):
            raise ApiError(
                400, "explicit_cuts.ranges must be [start, end] numbers."
            )
        ops.append({
            "op": mode, "origin": "control",
            "start": float(pair[0]), "end": float(pair[1]),
        })
    return ops


def route_plan(body: dict[str, object]) -> dict[str, object]:
    instruction = body.get("instruction", "")
    if not isinstance(instruction, str) or not instruction.strip():
        raise ApiError(400, "instruction is required.")
    _, media = _resolve_media(body)
    info = MediaInfo(
        duration=float(media["duration"]),
        width=int(media["width"]),
        height=int(media["height"]),
        fps=float(media.get("fps", 30.0) or 30.0),
        has_audio=bool(media.get("has_audio", False)),
    )
    style = str(body.get("style", "") or "")
    try:
        aspect = canonical_aspect(body.get("aspect_ratio", "original"))
    except ValueError as exc:
        raise ApiError(400, str(exc)) from None
    fit = str(body.get("fit", "pad") or "pad")
    if fit not in RESIZE_FITS:
        raise ApiError(
            400, f"fit must be one of {', '.join(RESIZE_FITS)}."
        )
    captions_flag = str(body.get("captions", "auto") or "auto")
    if captions_flag not in ("auto", "on", "off"):
        raise ApiError(400, 'captions must be one of auto, on, off.')
    minutes_question = minutes_clarification(instruction)
    if minutes_question is not None:
        raise ApiError(422, minutes_question)
    footage_notes = ""
    analyze_warnings: list[str] = []
    clock = _Stopwatch()
    cached = _analysis_for_request(body)
    if cached:
        footage_notes = (
            "Footage observations (measured in-app, use instead of "
            f"guessing): {cached}"
        )
    if needs_visual_analysis(instruction):
        clock.start("visual_analysis_ms")
        try:
            extra_notes, analyze_warnings = _maybe_analyze_footage(
                body, info, instruction
            )
        finally:
            clock.stop("visual_analysis_ms")
        footage_notes = f"{footage_notes}\n{extra_notes}".strip()
    else:
        clock.marks["visual_analysis_ms"] = 0
    clock.start("model_ms")
    try:
        plan, spark_notes, errors = _spark_plan(
            instruction, info, style, aspect, MODEL_SPARK,
            footage_notes=footage_notes,
        )
    except MetaAPIError as exc:
        mark_failed("spark_plan", exc.message)
        raise
    model_used = MODEL_SPARK
    roundtrips = 1 + _schema_fallback_count(spark_notes)
    if errors and body.get("allow_wonder") is True:
        try:
            plan, spark_notes, errors = _spark_plan(
                instruction, info, style, aspect, MODEL_WONDER,
                footage_notes=footage_notes,
            )
        except MetaAPIError as exc:
            mark_failed("wonder_plan", exc.message)
            raise
        model_used = MODEL_WONDER
        roundtrips += 1 + _schema_fallback_count(spark_notes)
    clock.stop("model_ms")
    clock.marks["model_roundtrips"] = roundtrips
    if errors:
        raise ApiError(422, "Plan failed validation: " + " ".join(errors))
    # Warnings are for items the creator should review; routine
    # enforcement (stripped extras, framing, style presets) is reported
    # as adjustments so the summary reads as information, not alarms.
    warnings = analyze_warnings + [
        note for note in spark_notes
        if not _is_aspect_alias_note(note)
    ]
    adjustments = [
        note for note in spark_notes if _is_aspect_alias_note(note)
    ]
    _tag_instruction_source(plan)
    intent_notes, pending = _enforce_intent(
        plan, instruction, captions_flag, info
    )
    adjustments.extend(intent_notes)
    adjustments.extend(_enforce_aspect(plan, aspect, fit))
    # User-chosen intervals override any planner cutting ops; timed ops
    # are then validated against the final explicit ranges below.
    explicit = _explicit_cut_ops(body)
    if explicit:
        plan["operations"] = [
            o for o in plan["operations"]
            if not (isinstance(o, dict)
                    and o.get("op") in ("trim", "keep", "remove"))
        ] + explicit
        adjustments.append(
            "Your kept/removed intervals replaced the planner's cut."
        )
    # An unambiguous first-N-seconds request is explicit intent: honor it
    # before deciding the plan says nothing. The plan is still valid
    # here (validated upstream; enforcement only removes or replaces
    # framing), so reconciling first is safe.
    requested = required_trim_end(instruction, info.duration)
    # UI explicit cuts (Keep/Remove fields, reviewed keeps) outrank the
    # prompt: first-N reconciliation must not clobber them.
    if requested is not None and not explicit:
        plan, note = ensure_first_seconds_trim(plan, requested, info.duration)
        if note is not None:
            adjustments.append(note)
    # Explicit keep/remove second-ranges ("keep only seconds 0-3")
    # apply when neither the cut controls nor a first-N request
    # already set the cut. The model returns nothing usable for this
    # phrasing, so the ranges reconcile deterministically.
    if not explicit and requested is None:
        parsed = parse_cut_ranges(instruction)
        if parsed is not None:
            plan, ranges_note = ensure_requested_ranges(
                plan, parsed[0], parsed[1], info.duration)
            if ranges_note is not None:
                adjustments.append(ranges_note)
    # The model can emit two caption tracks for one captions request;
    # merge them into one (all timed lines kept) before validation.
    adjustments.extend(_dedupe_control_captions(plan))
    if not [o for o in plan["operations"] if isinstance(o, dict)]:
        raise ApiError(
            422,
            "I could not tell what to change in this video. For example: "
            "'keep the first 5 seconds', 'add captions for the speech', "
            "or 'zoom in between seconds 3 and 5'. Tell me which change "
            "to apply.",
        )
    rechecked = validate_plan(plan, info)
    if not rechecked.ok:
        if requested is not None:
            raise ApiError(
                422,
                "Plan conflicts with the requested first-"
                f"{requested:g}s interval: " + " ".join(rechecked.errors),
            )
        raise ApiError(
            422,
            "Plan failed validation after applying your settings: "
            + " ".join(rechecked.errors),
        )
    assert rechecked.plan is not None
    plan = rechecked.plan
    warnings.extend(
        note for note in rechecked.warnings if note not in warnings
    )
    styled = _apply_style_effects(plan, adjustments, style, aspect, info)
    if styled:
        rechecked = validate_plan(plan, info)
        if not rechecked.ok:
            raise ApiError(
                422,
                "Plan failed validation after style effects: "
                + " ".join(rechecked.errors),
            )
        assert rechecked.plan is not None
        plan = rechecked.plan
        warnings.extend(
            note for note in rechecked.warnings if note not in warnings
        )
    if _is_noop_plan(plan):
        raise ApiError(
            422,
            "I could not tell what to change in this video. For example: "
            "'keep the first 5 seconds', 'add captions for the speech', "
            "or 'zoom in between seconds 3 and 5'. Tell me which change "
            "to apply.",
        )
    mark_verified(
        "wonder_plan" if model_used == MODEL_WONDER else "spark_plan"
    )
    return {
        "plan": plan, "warnings": warnings, "model": model_used,
        "pending": pending, "adjustments": adjustments,
        "timings": clock.as_dict(),
        "suggestions": _review_suggestions(
            _analysis_dict_for_request(body), info.duration
        ),
    }


def _schema_fallback_count(notes: list[str]) -> int:
    """Schema-retry round trips hidden in one _spark_plan result."""
    return sum(1 for note in notes if "compatibility schema" in note)


def _is_aspect_alias_note(note: str) -> bool:
    """Routine model-output normalizations are info, not review items.

    Aspect-alias notes are owned by plan_schema.normalize_plan_aspects
    ("operations[i].aspect 'alias' → 'canonical'."); cutting-mode
    notes are owned by _normalize_cutting_modes ("Cutting modes
    combined ...").
    """
    return note.startswith("operations[") or note.startswith(
        "Cutting modes combined")


# Strip-note markers superseded when a preset applies the same effect.
# Each op lists substrings that must ALL appear; the resize pair keeps
# the explicit-cut note ("planner's cut", no "frame") and the preset's
# own "framed 9:16" note (no "planner's") intact.
_SUPERSEDED_STRIP_MARKERS = {
    "fade": ("planner's fade",),
    "grade": ("planner's grade",),
    "resize_pad": ("planner's", "frame"),
}


def _apply_style_effects(
    plan: dict[str, object],
    adjustments: list[str],
    style: str,
    aspect: str,
    media: MediaInfo,
) -> bool:
    """Append a style preset's real operations. Returns True when added.

    Clearing the preset (empty style) adds nothing, removing its effect.
    A preset op supersedes the matching "planner's suggestion was left
    out" note, so the summary names each applied effect once, with its
    actual source.
    """
    ops = plan["operations"]
    assert isinstance(ops, list)
    have = {o.get("op") for o in ops if isinstance(o, dict)}
    key = style.strip().lower()
    added = False

    def add(op: dict[str, object], note: str) -> None:
        op["origin"] = "preset"
        ops.append(op)
        markers = _SUPERSEDED_STRIP_MARKERS[op["op"]]
        adjustments[:] = [
            item for item in adjustments
            if not all(marker in item for marker in markers)
        ]
        adjustments.append(note)

    if key == "product ad":
        if "grade" not in have:
            add({"op": "grade", "preset": "vivid", "intensity": 0.5},
                "Style 'Product Ad' applied a vivid grade (0.5).")
            added = True
    elif key == "cinematic":
        if "grade" not in have:
            add({"op": "grade", "preset": "warm", "intensity": 0.5},
                "Style 'Cinematic' applied a warm grade (0.5).")
            added = True
        fades = sum(1 for o in ops if o.get("op") == "fade")
        if fades < 2:
            add({"op": "fade", "in_sec": 0.5, "out_sec": 0.5},
                "Style 'Cinematic' added a 0.5s fade in/out.")
            added = True
    elif key == "social reel":
        if "resize_pad" not in have and aspect == "original":
            add({"op": "resize_pad", "aspect": "9:16", "fit": "crop"},
                "Style 'Social Reel' framed 9:16 to fill the screen.")
            added = True
    elif key == "tutorial":
        # Tutorial adds no automatic operations: captions are never enabled
        # by a preset. The style still guides the planner's wording and
        # pacing choices through the "Requested style" prompt line.
        pass
    return added


def _op_is_noop(op: object) -> bool:
    if not isinstance(op, dict):
        return False
    name = op.get("op")
    if name == "resize_pad":
        return op.get("aspect", "original") == "original"
    if name == "fade":
        return (float(op.get("in_sec", 0) or 0) == 0
                and float(op.get("out_sec", 0) or 0) == 0)
    if name == "grade":
        return float(op.get("intensity", 0) or 0) == 0
    return False


def _is_noop_plan(plan: dict[str, object]) -> bool:
    ops = plan.get("operations", [])
    return (isinstance(ops, list) and bool(ops)
            and all(_op_is_noop(o) for o in ops))


def _extract_frame(
    ffmpeg_bin: str, video_path: str, at: float, dest: str
) -> None:
    done = subprocess.run(
        [ffmpeg_bin, "-y", "-v", "error", "-ss", f"{at:.2f}",
         "-i", video_path, "-frames:v", "1",
         "-vf", "scale=480:-1", dest],
        capture_output=True, text=True, timeout=120, check=False,
    )
    if done.returncode != 0 or not os.path.exists(dest):
        raise RuntimeError(f"Frame extraction failed: {done.stderr[-200:]}")


def _maybe_analyze_footage(
    body: dict[str, object], info: MediaInfo, instruction: str
) -> tuple[str, list[str]]:
    """Sample frames and ask Spark what is visible when.

    Used only for visual-event requests without timestamps. Returns
    (notes, warnings); failures degrade to warnings, never errors.
    """
    file_id = body.get("file_id")
    entry = APP.storage.get(file_id) if isinstance(file_id, str) else None
    if entry is None or not os.path.exists(entry["path"]):
        return "", []
    stamps = [round(info.duration * frac, 1)
              for frac in (0.08, 0.25, 0.42, 0.58, 0.75, 0.92)]
    images: list[tuple[float, str]] = []
    for at in stamps:
        dest = os.path.join(APP.storage.tmp, f"{uuid.uuid4().hex}.jpg")
        try:
            _extract_frame(APP.settings.ffmpeg_bin, entry["path"], at, dest)
            with open(dest, "rb") as handle:
                encoded = base64.b64encode(handle.read()).decode("ascii")
            images.append((at, encoded))
        except (RuntimeError, OSError):
            continue
        finally:
            try:
                os.remove(dest)
            except OSError:
                pass
    if not images:
        return "", ["Visual analysis found no frames; continuing without it."]
    parts: list[dict[str, object]] = [{
        "type": "text",
        "text": (
            f"Edit request: {instruction.strip()} "
            f"(video is {info.duration:.1f}s). Sampled frames follow, "
            "each labeled with its timestamp. Describe only what is "
            "visibly happening at each timestamp so the planner can "
            "time the edit. Never invent events or timestamps; say "
            "'not visible' when the request names nothing in the frames."
        ),
    }]
    for at, encoded in images:
        parts.append({"type": "text", "text": f"Frame at {at:.1f}s:"})
        parts.append({"type": "image_url", "image_url": {
            "url": f"data:image/jpeg;base64,{encoded}",
        }})
    messages = [
        {"role": "system",
         "content": ("You are a precise video analyst. Report only visible "
                     "events with their frame timestamps.")},
        {"role": "user", "content": parts},
    ]
    try:
        response = APP.client().chat_completions(
            MODEL_SPARK, messages, timeout=180.0
        )
        notes = MetaClient.chat_text(response)
    except (MetaAPIError, ApiError) as exc:
        message = exc.message if isinstance(exc, MetaAPIError) else str(exc)
        return "", [f"Visual analysis is unavailable ({message}); "
                    "continuing without it."]
    if not notes.strip():
        return "", ["Visual analysis returned no notes; "
                    "continuing without it."]
    return notes.strip()[:1500], ["Analyzed sampled frames for visual events."]


def _sam_track_video(
    video_path: str, subject: str
) -> tuple[str, list[str]]:
    """Run one SAM concept request; return concatenated output_text."""
    with open(video_path, "rb") as handle:
        video_bytes = handle.read()
    client = APP.client()
    file_id: str | None = None
    try:
        if len(video_bytes) <= INLINE_VIDEO_BYTES:
            encoded = base64.b64encode(video_bytes).decode("ascii")
            media_part: dict[str, object] = {
                "type": "input_video",
                "video_url": f"data:video/mp4;base64,{encoded}",
            }
        else:
            file_id = client.upload_file("clip.mp4", video_bytes)
            media_part = {"type": "input_video", "file_id": file_id}
        events = client.responses_stream(
            "sam-3.1",
            [{
                "type": "message",
                "role": "user",
                "content": [
                    {"type": "input_text", "text": subject},
                    media_part,
                ],
            }],
        )
    finally:
        if file_id is not None:
            try:
                client.delete_file(file_id)
            except MetaAPIError:
                pass
    chunks: list[str] = []
    completed = False
    for event in events:
        etype = event.get("type")
        if etype == "response.output_text.delta":
            delta = event.get("delta", "")
            if isinstance(delta, str):
                chunks.append(delta)
        elif etype == "response.completed":
            completed = True
    if not completed:
        raise ApiError(502, "SAM stream ended without completion.")
    mark_verified("sam_track")
    return "".join(chunks), []


def _turn_time_ms(raw: dict[str, object], keys: tuple[str, ...]) -> float | None:
    for key in keys:
        value = raw.get(key)
        if isinstance(value, bool):
            continue
        if isinstance(value, (int, float)):
            number = float(value)
            if "ms" in key.lower():
                return number / 1000.0
            return number
    return None


def _turns_to_caption_segments(
    transcript: dict[str, object], audio_duration: float
) -> tuple[list[dict[str, object]], bool]:
    """Turns -> caption segments. Uses turn timestamps when every turn
    carries them; otherwise spreads turns evenly and reports approximate
    timing. Never invents word-level timing."""
    turns = [
        t for t in transcript.get("turns", [])
        if isinstance(t, dict) and isinstance(t.get("text"), str)
        and str(t["text"]).strip()
    ]
    if not turns:
        return [], False
    timed: list[tuple[str, float | None, float | None]] = []
    for turn in turns:
        raw = turn.get("raw")
        blob = raw if isinstance(raw, dict) else {}
        start = _turn_time_ms(
            blob, ("startMs", "start_ms", "startTimeMs", "start")
        )
        end = _turn_time_ms(blob, ("endMs", "end_ms", "endTimeMs", "end"))
        timed.append((str(turn["text"]).strip(), start, end))
    segments: list[dict[str, object]] = []
    if all(start is not None for _, start, _ in timed):
        for index, (text, start, end) in enumerate(timed):
            assert start is not None
            stop = end
            if stop is None or stop <= start:
                stop = (
                    timed[index + 1][1] if index + 1 < len(timed)
                    else audio_duration
                )
            stop = min(max(float(stop), start + 0.4), audio_duration)
            segments.append({"start": start, "end": stop, "text": text})
        return segments[:120], False
    # No turn timestamps: split coarse turns into sentences and spread
    # them evenly so one 13s blob never burns whole-transcript text into
    # a 5s output. Still approximate — never word timing.
    chunks: list[str] = []
    for text, _, _ in timed:
        chunks.extend(_split_sentences(text))
    chunks = chunks or [text for text, _, _ in timed]
    slot = audio_duration / max(len(chunks), 1)
    for index, text in enumerate(chunks[:120]):
        segments.append({
            "start": round(index * slot, 3),
            "end": round(min((index + 1) * slot, audio_duration), 3),
            "text": text,
        })
    return segments[:120], True


_SENTENCE_END_RE = re.compile(r"(.+?[.!?…]+)(?=\s+|$)")


def _split_sentences(text: str) -> list[str]:
    """Split a coarse transcript into sentence chunks for even spreading.

    Tamil and English both end sentences with . ? !, so one rule covers
    mixed text. Very long sentences split further at commas and then at
    word count so no caption line carries a paragraph.
    """
    parts = [
        m.group(1).strip()
        for m in _SENTENCE_END_RE.finditer(text.strip())
    ] or [text.strip()]
    chunks: list[str] = []
    for part in parts:
        if len(part) <= 90:
            chunks.append(part)
            continue
        comma_bits = [b.strip() for b in part.split(",") if b.strip()]
        if len(comma_bits) > 1 and all(len(b) <= 90 for b in comma_bits):
            chunks.extend(comma_bits)
            continue
        words = part.split()
        for at in range(0, len(words), 12):
            chunks.append(" ".join(words[at:at + 12]))
    return [c for c in chunks if c]


def _turns_have_timestamps(transcript: dict[str, object]) -> bool:
    """True when every non-empty turn carries a start timestamp."""
    turns = transcript.get("turns", [])
    if not isinstance(turns, list):
        return False
    checked = 0
    for turn in turns:
        if not isinstance(turn, dict):
            continue
        if not isinstance(turn.get("text"), str) or not str(
            turn["text"]
        ).strip():
            continue
        checked += 1
        blob = turn.get("raw")
        blob = blob if isinstance(blob, dict) else {}
        if _turn_time_ms(
            blob, ("startMs", "start_ms", "startTimeMs", "start")
        ) is None:
            return False
    return checked > 0


def _transcribe_wav_file(
    wav_path: str, request_part: dict[str, object]
) -> dict[str, object]:
    """Send one WAV file to Meta Voice Transcribe and parse the reply."""
    with open(wav_path, "rb") as handle:
        wav_bytes = handle.read()
    response = APP.client().transcribe(wav_bytes, request_part)
    try:
        return parse_transcript(response)
    except ValueError as exc:
        raise ApiError(502, f"Bad transcription payload: {exc}") from None


def _extract_kept_wav(
    ffmpeg_bin: str, src_path: str,
    ranges: list[tuple[float, float]], dest: str,
) -> None:
    """Concatenate the kept audio ranges into one WAV for transcription.

    Caption segments from this audio are already on the edited timeline,
    so coarse transcripts spread over kept seconds instead of source
    seconds.
    """
    if len(ranges) == 1:
        start, end = ranges[0]
        filt = (
            f"[0:a]atrim=start={start:.3f}:end={end:.3f},"
            "asetpts=PTS-STARTPTS[aout]"
        )
    else:
        parts = []
        labels = []
        for index, (start, end) in enumerate(ranges):
            parts.append(
                f"[0:a]atrim=start={start:.3f}:end={end:.3f},"
                f"asetpts=PTS-STARTPTS[s{index}]"
            )
            labels.append(f"[s{index}]")
        parts.append(
            "".join(labels) + f"concat=n={len(labels)}:v=0:a=1[aout]"
        )
        filt = ";".join(parts)
    done = subprocess.run(
        [ffmpeg_bin, "-y", "-v", "error", "-i", src_path,
         "-filter_complex", filt, "-map", "[aout]",
         "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", dest],
        capture_output=True, text=True, timeout=300, check=False,
    )
    if done.returncode != 0 or not os.path.exists(dest):
        raise ApiError(
            503, f"Could not extract the kept audio: {done.stderr[-200:]}"
        )


def route_capabilities() -> dict[str, object]:
    font_name, _ = find_tamil_font()
    return {
        "operations": [dict(entry) for entry in OP_CATALOG],
        "sfx_assets": list(SFX_ASSETS),
        "grade_presets": list(GRADE_PRESETS),
        "transition_types": list(TRANSITION_TYPES),
        "text_positions": list(TEXT_POSITIONS),
        "text_animations": list(TEXT_ANIMATIONS),
        "text_sizes": list(TEXT_SIZES),
        "resize_fits": list(RESIZE_FITS),
        "banner_positions": list(BANNER_POSITIONS),
        "max_banners": MAX_BANNERS,
        "join_limits": {
            "max_clips": MAX_JOIN_CLIPS,
            "max_duration_sec": MAX_JOIN_DURATION_SEC,
        },
        "intro_duration": [INTRO_MIN_SEC, INTRO_MAX_SEC],
        "outro_duration": [OUTRO_MIN_SEC, OUTRO_MAX_SEC],
        "tamil_font": {
            "available": font_name is not None,
            "name": font_name or "",
        },
        "bundled_sfx": list(SFX_FILES),
        "server_build": SERVER_BUILD,
    }


def _dedupe_control_captions(plan: object) -> list[str]:
    """Merge duplicate caption tracks into one before validation.

    The model can emit two caption tracks for one request (seen live:
    a Tamil captions brief produced an auto track plus a second
    track), and the app attaches the user's edited lines at render
    time on top of a planned auto track. All timed segments union
    into one track (sorted, exact duplicates removed) so no Tamil
    line is lost; a pure auto/auto duplicate collapses to one auto
    track. The merged op carries exactly one of segments/source so
    the one-track validation rule passes.
    """
    if not isinstance(plan, dict):
        return []
    ops = plan.get("operations")
    if not isinstance(ops, list):
        return []
    indexes = [
        i for i, o in enumerate(ops)
        if isinstance(o, dict) and o.get("op") == "captions"
    ]
    if len(indexes) < 2:
        return []
    tracks = [ops[i] for i in indexes]
    assert all(isinstance(o, dict) for o in tracks)
    seen: set[tuple[str, str, str]] = set()
    merged: list[dict[str, object]] = []
    for track in tracks:
        assert isinstance(track, dict)
        segments = track.get("segments")
        if not isinstance(segments, list):
            continue
        for entry in segments:
            if not isinstance(entry, dict):
                continue
            if ("start" not in entry or "end" not in entry
                    or "text" not in entry):
                continue
            # Entries pass through verbatim (validation still judges
            # types and bounds); only exact duplicates collapse.
            key = (repr(entry["start"]), repr(entry["end"]),
                   repr(entry["text"]))
            if key in seen:
                continue
            seen.add(key)
            merged.append({
                "start": entry["start"], "end": entry["end"],
                "text": entry["text"],
            })
    try:
        merged.sort(key=lambda e: (float(e["start"]),  # type: ignore[arg-type]
                                     float(e["end"])))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        pass  # non-numeric times stay in order; validation reports them
    if merged:
        kept: dict[str, object] = {
            "op": "captions", "segments": merged,
            "origin": _merged_captions_origin(tracks),
        }
        note = (
            f"Merged {len(indexes)} caption tracks into one "
            f"({len(merged)} lines kept)."
        )
    else:
        kept = {
            "op": "captions", "source": "auto",
            "origin": _merged_captions_origin(tracks),
        }
        note = (
            f"Merged {len(indexes)} automatic caption tracks into one."
        )
    plan["operations"] = [
        o for i, o in enumerate(ops) if i not in indexes
    ]
    plan["operations"].insert(indexes[0], kept)
    return [note]


def _union_ranges(spans: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Merge overlapping/adjacent spans into sorted disjoint spans."""
    merged: list[list[float]] = []
    for start, end in sorted(spans):
        if end - start <= 1e-9:
            continue
        if merged and start <= merged[-1][1] + 1e-9:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return [(s, e) for s, e in merged]


def _subtract_ranges(
    base: list[tuple[float, float]], cuts: list[tuple[float, float]]
) -> list[tuple[float, float]]:
    """Keep base spans minus cut spans, sorted and disjoint."""
    kept = _union_ranges(base)
    for cut_start, cut_end in sorted(cuts):
        remaining: list[tuple[float, float]] = []
        for keep_start, keep_end in kept:
            if cut_end <= keep_start or cut_start >= keep_end:
                remaining.append((keep_start, keep_end))
                continue
            if cut_start > keep_start:
                remaining.append((keep_start, min(cut_start, keep_end)))
            if cut_end < keep_end:
                remaining.append((max(cut_end, keep_start), keep_end))
        kept = [(s, e) for s, e in remaining if e - s > 1e-9]
    return kept


def _normalize_cutting_modes(plan: dict[str, object]) -> str | None:
    """Merge mixed trim/keep/remove ops into one keep list.

    The model mirrors "keep A and remove B" phrasing into two cutting
    modes, which validation rejects ("Choose one cutting mode"). Kept
    spans are (trim/keep ranges) minus (remove ranges), emitted as
    keep ops at the first cut's position. Single-mode plans are
    untouched. Returns an adjustments note, or None when nothing was
    merged (validation then reports the original problem).
    """
    ops = plan.get("operations")
    if not isinstance(ops, list):
        return None
    modes = {
        o.get("op") for o in ops if isinstance(o, dict)
    } & {"trim", "keep", "remove"}
    if len(modes) < 2:
        return None
    base: list[tuple[float, float]] = []
    cuts: list[tuple[float, float]] = []
    first_index: int | None = None
    for index, raw in enumerate(ops):
        if not isinstance(raw, dict):
            continue
        name = raw.get("op")
        if name not in ("trim", "keep", "remove"):
            continue
        try:
            span = (float(raw["start"]), float(raw["end"]))  # type: ignore[arg-type]
        except (KeyError, TypeError, ValueError):
            return None  # malformed times; validation reports them
        if first_index is None:
            first_index = index
        if name == "remove":
            cuts.append(span)
        else:
            base.append(span)
    kept = _subtract_ranges(base, cuts)
    if not kept:
        return None  # contradictory cuts; validation reports the mix
    assert first_index is not None
    merged = [o for o in ops if not (
        isinstance(o, dict) and o.get("op") in ("trim", "keep", "remove"))]
    keeps = [
        {"op": "keep", "start": round(s, 3), "end": round(e, 3)}
        for s, e in kept
    ]
    plan["operations"] = (
        merged[:first_index] + keeps + merged[first_index:]
    )
    describe = ", ".join(f"{s:g}-{e:g}s" for s, e in kept)
    return (
        f"Cutting modes combined into {len(kept)} kept interval(s) "
        f"({describe})."
    )


def ensure_requested_ranges(
    plan: dict[str, object],
    keeps: list[tuple[float, float]],
    removes: list[tuple[float, float]],
    duration: float,
) -> tuple[dict[str, object], str | None]:
    """Reconcile a validated plan with explicit keep/remove ranges.

    Returns (plan, note). The note is None when the plan already
    expresses exactly the requested kept spans. Ends past the clip
    clamp to its duration; ranges starting past the end are dropped,
    and an emptied request leaves the plan untouched for validation
    to judge.
    """
    ops = plan.get("operations")
    assert isinstance(ops, list)
    kept = [
        (a, min(b, duration)) for a, b in keeps if a < duration - 1e-9
    ]
    cut = [
        (a, min(b, duration)) for a, b in removes if a < duration - 1e-9
    ]
    kept = [(a, b) for a, b in kept if b - a > 1e-9]
    cut = [(a, b) for a, b in cut if b - a > 1e-9]
    clamped = (
        any(b > duration + 1e-9 for _, b in keeps + removes)
    )
    if not kept and not cut:
        return plan, None
    if kept:
        want: list[tuple[str, float, float]] = [
            ("keep", s, e) for s, e in _subtract_ranges(kept, cut)
        ]
    else:
        want = [("remove", s, e) for s, e in sorted(cut)]
    if not want:
        return plan, None
    current: list[tuple[float, float]] = []
    current_cuts: list[tuple[float, float]] = []
    for raw in ops:
        if not isinstance(raw, dict):
            continue
        name = raw.get("op")
        if name not in ("trim", "keep", "remove"):
            continue
        span = (float(raw["start"]), float(raw["end"]))  # validated
        if name == "remove":
            current_cuts.append(span)
        else:
            current.append(span)
    have = _subtract_ranges(
        current or [(0.0, duration)], current_cuts)
    if kept:
        want_spans = [(s, e) for _, s, e in want]
    else:
        want_spans = _subtract_ranges(
            [(0.0, duration)], [(s, e) for _, s, e in want])
    if len(have) == len(want_spans) and all(
        abs(a - c) < 1e-6 and abs(b - d) < 1e-6
        for (a, b), (c, d) in zip(have, want_spans)
    ):
        return plan, None
    first_index = next(
        (i for i, o in enumerate(ops)
         if isinstance(o, dict) and o.get("op") in ("trim", "keep",
                                                   "remove")),
        len(ops),
    )
    rest = [o for o in ops if not (
        isinstance(o, dict) and o.get("op") in ("trim", "keep", "remove"))]
    fresh = [
        {"op": name, "start": round(s, 3), "end": round(e, 3),
         "origin": "instruction"}
        for name, s, e in want
    ]
    plan["operations"] = rest[:first_index] + fresh + rest[first_index:]
    parts = (
        [f"keep {a:g}-{b:g}s" for a, b in kept]
        + [f"remove {a:g}-{b:g}s" for a, b in cut]
    )
    note = f"Applied the requested ranges ({'; '.join(parts)})."
    if clamped:
        note += f" Ends past the {duration:g}s clip were clamped."
    return plan, note


def _merged_captions_origin(tracks: list[object]) -> str:
    """Origin for a merged track: control wins, else the first seen."""
    origins = [
        o.get("origin") for o in tracks if isinstance(o, dict)
    ]
    if "control" in origins:
        return "control"
    for origin in origins:
        if isinstance(origin, str) and origin:
            return origin
    return "instruction"


def route_join(body: dict[str, object]) -> dict[str, object]:
    """Join main clips into one normalized timeline for planning.

    Multi-clip projects call this once per clip set: the returned
    join_id behaves like a single source (plan/render/analyze take
    it as file_id) while clips[] keeps each clip's stable id, name,
    and offset into the joined timeline. Geometry normalizes to the
    first clip's frame (aspect "original") or a standard target;
    frame rate becomes the fastest input; silent clips contribute
    silence. Results cache by inputs, so reopening a project is a
    cheap cache hit and a missing join file rebuilds automatically.
    """
    raw_ids = body.get("file_ids")
    if not isinstance(raw_ids, list) or not raw_ids:
        raise ApiError(400, "file_ids must be a non-empty list.")
    if len(raw_ids) > MAX_JOIN_CLIPS:
        raise ApiError(
            400,
            f"At most {MAX_JOIN_CLIPS} clips per project; "
            f"got {len(raw_ids)}.",
        )
    file_ids = []
    for file_id in raw_ids:
        if not isinstance(file_id, str) or not HEX_ID.match(file_id):
            raise ApiError(400, "Each file_id must be a 32-hex id.")
        file_ids.append(file_id)
    aspect = str(body.get("aspect_ratio", "original") or "original")
    if aspect not in ("original", "9:16", "16:9", "1:1"):
        raise ApiError(400, "aspect_ratio must be original, 9:16, 16:9, or 1:1.")
    entries: list[dict[str, str]] = []
    medias: list[dict[str, object]] = []
    clips: list[dict[str, object]] = []
    offset = 0.0
    clock = _Stopwatch()
    clock.start("probe_ms")
    for index, file_id in enumerate(file_ids):
        entry = APP.storage.get(file_id)
        if entry is None or not os.path.exists(entry["path"]):
            raise ApiError(404, "Unknown file_id.", {"missing": file_id})
        try:
            media = probe_media(APP.settings.ffprobe_bin, entry["path"])
        except FFmpegError as exc:
            # A non-video clip is the client's media problem (400), like
            # video_of; only a broken tool is a server problem (503).
            if str(exc) == NO_VIDEO_MESSAGE:
                raise ApiError(
                    400, f"Clip {index + 1} ({entry.get('name', '?')}) "
                         "has no video stream.",
                ) from None
            raise ApiError(503, str(exc)) from None
        duration = float(media["duration"])
        clips.append({
            "index": index,
            "file_id": file_id,
            "name": entry.get("name", f"clip {index + 1}"),
            "duration": duration,
            "width": int(media["width"]),
            "height": int(media["height"]),
            "fps": float(media.get("fps", 30.0) or 30.0),
            "has_audio": bool(media.get("has_audio")),
            "offset": offset,
        })
        offset += duration
        entries.append(entry)
        medias.append(media)
    clock.stop("probe_ms")
    total = offset
    if total > MAX_JOIN_DURATION_SEC:
        raise ApiError(
            400,
            f"Joined clips total {total:.0f}s; "
            f"at most {MAX_JOIN_DURATION_SEC:.0f}s per project.",
        )
    first = medias[0]
    if aspect == "original":
        width = int(first["width"]) // 2 * 2
        height = int(first["height"]) // 2 * 2
    else:
        width, height = ASPECT_TARGETS[aspect]
    fps = min(60.0, max(
        float(m.get("fps", 30.0) or 30.0) for m in medias
    ))
    has_audio = any(bool(m.get("has_audio")) for m in medias)
    if len(file_ids) == 1:
        # One clip is already a timeline: no transcode, no new file.
        return {
            "join_id": file_ids[0],
            "url": f"/api/files/{file_ids[0]}",
            "duration": total,
            "width": int(first["width"]),
            "height": int(first["height"]),
            "fps": float(first.get("fps", 30.0) or 30.0),
            "has_audio": bool(first.get("has_audio")),
            "cache": "single",
            "clips": clips,
            "timings": clock.as_dict(),
            "limits": {
                "max_clips": MAX_JOIN_CLIPS,
                "max_duration_sec": MAX_JOIN_DURATION_SEC,
            },
        }
    key_src = json.dumps(
        {"ids": file_ids, "aspect": aspect,
         "sizes": [os.path.getsize(e["path"]) for e in entries]},
        sort_keys=True,
    )
    key = hashlib.sha1(key_src.encode("utf-8")).hexdigest()
    join_id = hashlib.sha1(f"join:{key}".encode("utf-8")).hexdigest()[:32]
    joins_dir = os.path.join(APP.storage.renders, "joins")
    os.makedirs(joins_dir, exist_ok=True)
    out_path = os.path.join(joins_dir, f"{key}.mp4")
    cache = "hit"
    clock.start("build_ms")
    if not os.path.exists(out_path):
        cache = "built"
        argv = build_join_args(
            [{"path": e["path"],
              "duration": float(m["duration"]),
              "has_audio": bool(m.get("has_audio"))}
             for e, m in zip(entries, medias)],
            width, height, fps, out_path,
        )
        run_render(APP.settings.ffmpeg_bin, argv)
    probed = probe_media(APP.settings.ffprobe_bin, out_path)
    clock.stop("build_ms")
    APP.storage.register(join_id, {
        "path": out_path,
        "name": f"join of {len(file_ids)} clips",
        "kind": "video",
    })
    return {
        "join_id": join_id,
        "url": f"/api/files/{join_id}",
        "duration": float(probed["duration"]),
        "width": int(probed["width"]),
        "height": int(probed["height"]),
        "fps": float(probed.get("fps", fps) or fps),
        "has_audio": bool(probed.get("has_audio", has_audio)),
        "cache": cache,
        "clips": clips,
        "timings": clock.as_dict(),
        "limits": {
            "max_clips": MAX_JOIN_CLIPS,
            "max_duration_sec": MAX_JOIN_DURATION_SEC,
        },
    }


def route_render(body: dict[str, object]) -> dict[str, object]:
    file_id = body.get("file_id")
    if not isinstance(file_id, str) or not file_id:
        raise ApiError(400, "file_id is required.")
    entry, media = APP.media_of(file_id)
    info = MediaInfo(
        duration=float(media["duration"]),
        width=int(media["width"]),
        height=int(media["height"]),
        fps=float(media.get("fps", 30.0) or 30.0),
        has_audio=bool(media.get("has_audio", False)),
    )
    normalized, applied = normalize_plan_aspects(body.get("plan"))
    dedup_notes = _dedupe_control_captions(normalized)
    checked = validate_plan(normalized, info)
    if not checked.ok:
        raise ApiError(422, "Plan failed validation: " + " ".join(checked.errors))
    assert checked.plan is not None
    plan = checked.plan
    warnings = applied + dedup_notes + list(checked.warnings)
    steps_used = ["plan_validated"]
    clock = _Stopwatch()
    clock.marks["sam_ms"] = 0
    clock.marks["transcribe_ms"] = 0
    requested = required_trim_end(
        str(body.get("instruction", "") or ""), info.duration
    )
    reconcile_note: str | None = None
    if requested is not None:
        plan, reconcile_note = ensure_first_seconds_trim(
            plan, requested, info.duration
        )
        rechecked = validate_plan(plan, info)
        if not rechecked.ok:
            raise ApiError(
                422,
                "Plan conflicts with the requested first-"
                f"{requested:g}s interval: " + " ".join(rechecked.errors),
            )
        assert rechecked.plan is not None
        plan = rechecked.plan

    segments = [o for o in plan["operations"] if o.get("op") == "frame_subject"]
    tracked: dict[int, tuple[int, int, int, int]] = {}
    if segments:
        clock.start("sam_ms")
        steps_used.append("sam_track")
        subjects: dict[str, list[int]] = {}
        for index, seg in enumerate(segments):
            subjects.setdefault(str(seg["subject"]).strip(), []).append(index)
        for subject, indexes in subjects.items():
            try:
                output_text, _ = _sam_track_video(entry["path"], subject)
            except MetaAPIError as exc:
                mark_failed("sam_track", exc.message)
                raise
            parsed = parse_output_text(output_text)
            warnings.extend(parsed.warnings)
            for index in indexes:
                seg = segments[index]
                window, reason = window_for_segment(
                    parsed.frames, info.fps,
                    float(seg["start"]), float(seg["end"]),
                    str(seg.get("aspect", "original")),
                )
                if window is None:
                    warnings.append(
                        f"Segment {index}: {reason}; center crop used."
                    )
                    window = _center_window(info)
                tracked[index] = window
        clock.stop("sam_ms")

    render_id = APP.storage.new_id()
    out_path = os.path.join(APP.storage.renders, render_id + ".mp4")
    ops = plan["operations"]
    transition = next((o for o in ops if o.get("op") == "transition"), None)
    trans_dur = float(transition["duration"]) if transition else 0.0
    seg_map = output_segments(ops, info.duration, trans_dur)

    music_path = None
    music_op = next((o for o in ops if o.get("op") == "music"), None)
    if music_op is not None:
        music_entry, _ = APP.audio_of(str(music_op["file_id"]))
        music_path = music_entry["path"]

    sfx_ops = [o for o in ops if o.get("op") == "sfx"]
    sfx_paths: list[str] = []
    if sfx_ops:
        try:
            bundled = ensure_sfx_assets(
                APP.settings.ffmpeg_bin, APP.storage.assets
            )
        except RuntimeError as exc:
            raise ApiError(503, str(exc)) from None
        for op in sfx_ops:
            if op.get("asset") is not None:
                sfx_paths.append(bundled[str(op["asset"])])
            else:
                sfx_entry, _ = APP.audio_of(str(op["file_id"]))
                sfx_paths.append(sfx_entry["path"])

    intro_op = next((o for o in ops if o.get("op") == "intro"), None)
    outro_op = next((o for o in ops if o.get("op") == "outro"), None)
    banner_ops = [o for o in ops if o.get("op") == "banner"]
    intro_path = outro_path = None
    if intro_op is not None:
        intro_entry, _ = APP.image_of(str(intro_op["file_id"]))
        intro_path = intro_entry["path"]
    if outro_op is not None:
        outro_entry, _ = APP.image_of(str(outro_op["file_id"]))
        outro_path = outro_entry["path"]
    banner_paths: list[str] = []
    for op in banner_ops:
        banner_entry, _ = APP.image_of(str(op["file_id"]))
        banner_paths.append(banner_entry["path"])
    support_paths: dict[str, str] = {}
    for op in ops:
        if not isinstance(op, dict) or op.get("op") not in (
            "cutaway", "pip", "insert"
        ):
            continue
        # Distinct names: entry/media/file_id still hold the MAIN clip
        # below (captions, kept-audio extraction, the render inputs).
        support_id = str(op["file_id"])
        if support_id not in support_paths:
            support_entry, support_media = APP.video_of(support_id)
            support_paths[support_id] = support_entry["path"]
            op["_support_duration"] = float(support_media["duration"])
            op["_support_audio"] = bool(support_media.get("has_audio"))
        need = float(op.get("src_start", 0.0)) + float(op["duration"])
        if need > float(op["_support_duration"]) + 1e-6:
            raise ApiError(
                400,
                f"{op['op']} needs {need:.1f}s of supporting video but "
                f"the file has {float(op['_support_duration']):.1f}s; "
                "shorten it or pick a longer clip.",
            )
        if op.get("audio", "mute") != "mute" and not op["_support_audio"]:
            op["audio"] = "mute"
            warnings.append(
                f"{op['op']} at {float(op['at']):.1f}s is muted: the "
                "supporting video has no audio track."
            )
    intro_dur = float(intro_op["duration"]) if intro_op else 0.0
    outro_dur = float(outro_op["duration"]) if outro_op else 0.0

    captions_op = next((o for o in ops if o.get("op") == "captions"), None)
    caption_segments: list[dict[str, object]] = []
    captions_output_relative = False
    captions_silent = False
    captions_left_out = False
    if captions_op is not None and captions_op.get("source") == "auto":
        clock.start("transcribe_ms")
        kept = content_ranges(ops, info.duration)
        full_source = len(kept) == 1 and abs(kept[0][0]) < 1e-6 and abs(
            kept[0][1] - info.duration
        ) < 1e-6
        if full_source:
            try:
                spoken = route_transcribe({"file_id": file_id})
            except ApiError as exc:
                # Silence / no-speech / provider hiccups must not fail
                # the whole render: report precisely and continue.
                warnings.append(
                    f"Captions were left out: {exc.message}"
                )
                captions_op = None
                ops[:] = [
                    o for o in ops
                    if not isinstance(o, dict) or o.get("op") != "captions"
                ]
                caption_segments = []
                approximate = False
                captions_left_out = True
            else:
                caption_segments, approximate = _turns_to_caption_segments(
                    spoken, info.duration
                )
        else:
            # Transcribe the RETAINED audio so coarse transcripts spread
            # over kept seconds; whole-source text must never burn into a
            # trimmed output as if all those words occur there.
            kept_wav = os.path.join(APP.storage.tmp, f"{uuid.uuid4().hex}.wav")
            try:
                _extract_kept_wav(
                    APP.settings.ffmpeg_bin, entry["path"], kept, kept_wav
                )
                kept_class, _ = analyze.classify_audio(
                    analyze.audio_levels(APP.settings.ffmpeg_bin, kept_wav)
                )
                captions_silent = kept_class == "silence"
                if captions_silent:
                    # Silent audio carries no speech: skip the provider
                    # call entirely instead of transcribing nothing.
                    spoken = {"text": "", "turns": []}
                else:
                    try:
                        spoken = _transcribe_wav_file(
                            kept_wav,
                            build_request_part("ENDPOINTING", [], []),
                        )
                    except MetaAPIError as exc:
                        mark_failed("voice_transcribe", exc.message)
                        raise
                    mark_verified("voice_transcribe")
            finally:
                if os.path.exists(kept_wav):
                    os.unlink(kept_wav)
            caption_segments, approximate = _turns_to_caption_segments(
                spoken, segments_output_len(seg_map)
            )
            captions_output_relative = True
        if approximate:
            warnings.append(
                "Caption timing is approximate: the transcription "
                "carries no turn timestamps."
            )
        if not caption_segments and not captions_left_out:
            if captions_silent:
                warnings.append(
                    "The kept audio is silent, so captions were left "
                    "out; the other edits are rendered normally. Add a "
                    "written title if the video needs words."
                )
            else:
                warnings.append(
                    "No speech was found in the kept audio — it may be "
                    "music or background sound — so captions were left "
                    "out; the other edits are rendered normally."
                )
            captions_op = None
            # Drop the op too: the renderer must not expect overlays.
            ops[:] = [
                o for o in ops
                if not isinstance(o, dict) or o.get("op") != "captions"
            ]
        else:
            steps_used.append("transcribe")
        clock.stop("transcribe_ms")
    elif captions_op is not None:
        caption_segments = list(captions_op.get("segments", []))  # type: ignore[arg-type]

    texts = [o for o in ops if o.get("op") == "text"]
    ass_path: str | None = None
    fonts_dir: str | None = None
    insert_marks = sorted(
        (float(o["at"]), float(o["duration"]))  # type: ignore[arg-type]
        for o in ops
        if isinstance(o, dict) and o.get("op") == "insert"
    )

    def _shifted(span_start: float, span_end: float):
        # Content-relative spans move past inserted footage, then past
        # the silent intro (zero when no intro is attached).
        shift_start = sum(
            dur for at, dur in insert_marks if at <= span_start + 1e-6
        )
        shift_end = sum(
            dur for at, dur in insert_marks if at < span_end - 1e-6
        )
        return (
            span_start + shift_start + intro_dur,
            span_end + shift_end + intro_dur,
        )

    if texts or caption_segments:
        events: list[dict[str, object]] = []
        for op in texts:
            for start, end in remap_interval_output(
                float(op["start"]), float(op["end"]), seg_map
            ):
                # Subtitles burn onto the final timeline: shift past
                # inserts and the silent intro.
                final_start, final_end = _shifted(start, end)
                events.append({
                    "start": final_start, "end": final_end,
                    "text": op["text"],
                    "position": op["position"], "size": op["size"],
                    "animation": op["animation"],
                })
        out_span = segments_output_len(seg_map)
        for entry_seg in caption_segments:
            if captions_output_relative:
                # Kept-audio segments already speak output seconds.
                base = (
                    max(0.0, float(entry_seg["start"])),  # type: ignore[arg-type]
                    min(out_span, float(entry_seg["end"])),  # type: ignore[arg-type]
                )
                spans = [_shifted(*base)]
            else:
                spans = [
                    _shifted(start, end)
                    for start, end in remap_interval_output(
                        float(entry_seg["start"]),  # type: ignore[arg-type]
                        float(entry_seg["end"]),  # type: ignore[arg-type]
                        seg_map,
                    )
                ]
            for start, end in spans:
                if end <= start:
                    continue
                events.append({
                    "start": start, "end": end, "text": entry_seg["text"],
                    "position": "bottom", "size": "medium",
                    "animation": "none",
                })
        if not events:
            warnings.append(
                "All text overlays fall outside the kept footage; "
                "nothing is drawn."
            )
        font_name, fonts_dir = find_tamil_font()
        if font_name is None or fonts_dir is None:
            raise ApiError(
                503,
                "No Tamil-capable system font found; text overlays "
                "need Nirmala UI (ships with Windows).",
            )
        dims = compute_expected_dims(plan, {
            "duration": info.duration, "width": info.width,
            "height": info.height, "has_audio": info.has_audio,
        })
        assert dims is not None  # text rejects frame_subject plans
        ass_path = os.path.join(APP.storage.tmp, render_id + ".ass")
        with open(ass_path, "w", encoding="utf-8") as handle:
            handle.write(build_ass(events, dims[0], dims[1], font_name))

    argv, adjustments = build_render_args(
        plan,
        {"duration": info.duration, "width": info.width,
         "height": info.height, "has_audio": info.has_audio,
         "fps": info.fps},
        entry["path"],
        out_path,
        tracked or None,
        music_path=music_path,
        sfx_paths=sfx_paths or None,
        ass_path=ass_path,
        fonts_dir=fonts_dir,
        intro_path=intro_path,
        outro_path=outro_path,
        banner_paths=banner_paths or None,
        support_paths=support_paths or None,
    )
    clock.start("ffmpeg_ms")
    run_render(APP.settings.ffmpeg_bin, argv)
    clock.stop("ffmpeg_ms")
    if reconcile_note is not None:
        adjustments.append(reconcile_note)
    # Validate against the requested interval when the instruction states
    # one explicitly — never against a trim-less plan alone. Inserted
    # segments and attached stills extend the footage on both paths.
    insert_dur = sum(
        float(op["duration"]) for op in ops  # type: ignore[arg-type]
        if isinstance(op, dict) and op.get("op") == "insert"
    )
    expected = (
        requested + insert_dur + intro_dur + outro_dur
        if requested is not None
        else expected_output_duration(plan, info.duration)
    )
    dims = compute_expected_dims(plan, {
        "duration": info.duration, "width": info.width,
        "height": info.height, "has_audio": info.has_audio,
    })
    # Music and audible effects ADD an audio track to silent footage,
    # so the output must carry audio even when the source has none.
    # sfx.at is edited-output seconds: compare against the final span,
    # which includes inserted segments as well as cuts and stills.
    out_span = (
        segments_output_len(seg_map) + insert_dur + intro_dur + outro_dur
    )
    sfx_audible = False
    for op in sfx_ops:
        if float(op["at"]) < out_span - 1e-3:  # type: ignore[arg-type]
            sfx_audible = True
            break
    support_audible = any(
        isinstance(op, dict)
        and op.get("op") in ("cutaway", "pip", "insert")
        and op.get("audio", "mute") != "mute"
        for op in ops
    )
    clock.start("verify_ms")
    try:
        output_media = verify_render_output(
            APP.settings.ffprobe_bin, out_path,
            expected_duration=expected,
            expect_audio=(
                info.has_audio or music_op is not None or sfx_audible
                or support_audible
            ),
            expected_dims=dims,
        )
    except FFmpegError:
        try:
            os.remove(out_path)
        except OSError:
            pass
        raise
    finally:
        if ass_path is not None:
            try:
                os.remove(ass_path)
            except OSError:
                pass
    clock.stop("verify_ms")
    APP.storage.register(render_id, {
        "path": out_path, "name": "render.mp4", "kind": "render",
    })
    output_summary = {
        "duration": float(output_media["duration"]),
        "has_audio": bool(output_media["has_audio"]),
        "width": int(output_media["width"]),
        "height": int(output_media["height"]),
        "sar": str(output_media.get("sar", "1:1")),
    }
    try:
        with open(
            os.path.join(APP.storage.renders, render_id + ".plan.json"),
            "w", encoding="utf-8",
        ) as handle:
            json.dump({
                "instruction": str(body.get("instruction", "") or ""),
                "plan": plan,
                "output": output_summary,
                "warnings": warnings,
                "adjustments": adjustments,
                "timings": clock.as_dict(),
            }, handle)
    except OSError:
        warnings.append(
            "The render plan could not be saved for debugging."
        )
    steps_used.append("ffmpeg_render")
    return {
        "render_id": render_id,
        "url": f"/api/files/{render_id}",
        "steps_used": steps_used,
        "warnings": warnings,
        "adjustments": adjustments,
        "timings": clock.as_dict(),
        "plan": plan,
        "output": output_summary,
    }


def _center_window(info: MediaInfo) -> tuple[int, int, int, int]:
    side = min(info.width, info.height) // 2 * 2
    return ((info.width - side) // 2, (info.height - side) // 2, side, side)


def route_edit(body: dict[str, object]) -> dict[str, object]:
    """Full pipeline in one call: audio? -> plan -> render (+thumbnail?)."""
    file_id = body.get("file_id")
    if not isinstance(file_id, str) or not file_id:
        raise ApiError(400, "file_id is required.")
    audio_id = body.get("audio_file_id")
    instruction = str(body.get("instruction", "") or "")
    transcript_text = ""
    if isinstance(audio_id, str) and audio_id:
        spoken = route_transcribe({
            "file_id": audio_id,
            "mode": str(body.get("transcribe_mode", "ENDPOINTING")),
            "languages": body.get("languages", []),
            "keywords": body.get("keywords", []),
        })
        transcript_text = str(spoken["text"])
        if not instruction.strip():
            instruction = transcript_text
    if not instruction.strip():
        raise ApiError(
            400, "instruction is required (or provide audio_file_id)."
        )
    planned = route_plan({
        "file_id": file_id,
        "instruction": instruction,
        "style": body.get("style", ""),
        "aspect_ratio": body.get("aspect_ratio", "original"),
        "fit": body.get("fit", "pad"),
        "captions": body.get("captions", "auto"),
        "allow_wonder": body.get("allow_wonder", False),
    })
    rendered = route_render({
        "file_id": file_id,
        "plan": planned["plan"],
        "instruction": instruction,
    })
    decision = decide(
        has_audio=bool(audio_id),
        plan_operations=list(planned["plan"]["operations"]),
        thumbnail_requested=False,
    )
    payload: dict[str, object] = {
        "transcript": transcript_text,
        "plan": planned["plan"],
        "plan_warnings": planned["warnings"],
        "plan_model": planned["model"],
        "plan_pending": planned.get("pending", []),
        "render_id": rendered["render_id"],
        "url": rendered["url"],
        "steps_used": list(decision.steps),
        "render_steps": rendered["steps_used"],
        "warnings": rendered["warnings"],
        "adjustments": rendered["adjustments"],
    }
    thumb_prompt = body.get("thumbnail_prompt")
    if isinstance(thumb_prompt, str) and thumb_prompt.strip():
        thumb = route_thumbnail({"prompt": thumb_prompt})
        payload["thumbnail_url"] = thumb["url"]
        payload["steps_used"] = list(decision.steps) + ["thumbnail"]
    return payload


_ANALYZE_JOBS = analyze.JobManager()


def _analysis_cache_path(file_id: str) -> str:
    directory = os.path.join(APP.storage.root, "analysis")
    os.makedirs(directory, exist_ok=True)
    return os.path.join(directory, file_id + ".json")


def _load_cached_analysis(file_id: str) -> dict[str, object] | None:
    try:
        with open(_analysis_cache_path(file_id), encoding="utf-8") as handle:
            cached = json.load(handle)
    except (OSError, ValueError):
        return None
    return cached if isinstance(cached, dict) else None


def _analysis_dict_for_request(body: dict[str, object]) -> dict | None:
    """Analysis object for the planner, from cache or request echo."""
    raw = body.get("analysis")
    if isinstance(raw, dict) and raw.get("duration"):
        return raw
    file_id = body.get("file_id")
    if isinstance(file_id, str) and file_id:
        cached = _load_cached_analysis(file_id)
        if isinstance(cached, dict):
            return cached
    return None


def _analysis_for_request(body: dict[str, object]) -> str:
    """Summarized observations for the planner, from cache or request.

    Accepts an `analysis` object echoed by the app or a `file_id`
    whose cached analysis is reused, so restyling never re-analyzes.
    Returns "" when nothing usable is available.
    """
    raw = _analysis_dict_for_request(body)
    if raw is not None:
        try:
            return analyze.summarize_for_prompt(raw)
        except (TypeError, ValueError):
            pass
    return ""


def _review_suggestions(
    analysis: dict | None, duration: float
) -> list[dict[str, object]]:
    """Reviewable cut candidates from measured pauses only.

    Long silences are offered as suggestions the creator accepts or
    rejects; nothing here cuts automatically, because a pause may be
    a meaningful beat.
    """
    suggestions: list[dict[str, object]] = []
    if not isinstance(analysis, dict):
        return suggestions
    pauses = analysis.get("pauses", [])
    if not isinstance(pauses, list):
        return suggestions
    for pause in pauses:
        if not isinstance(pause, dict):
            continue
        start = pause.get("start")
        end = pause.get("end")
        if not isinstance(start, (int, float)) or not isinstance(
            end, (int, float)
        ):
            continue
        if end - start < 2.0 or start < 0 or end > duration:
            continue
        suggestions.append({
            "start": float(start),
            "end": float(end),
            "reason": (
                f"{end - start:.1f}s of silence — possible dead air. "
                "Cut only if no beat or breath belongs here."
            ),
        })
        if len(suggestions) >= 8:
            break
    return suggestions


def _run_analysis_job(job: analyze.Job, file_id: str) -> dict[str, object]:
    settings = APP.settings
    entry, media = APP.media_of(file_id)
    src = entry["path"]
    duration = float(media["duration"])
    width = int(media["width"])
    height = int(media["height"])

    job.stage = "Inspecting streams."
    job.check()
    clock = _Stopwatch()
    has_audio = bool(media.get("has_audio", False))
    levels: dict[str, float] | None = None
    audio_class = "no_stream"
    audio_reason = "The file has no audio stream."
    if has_audio:
        job.stage = "Measuring audio levels."
        job.check()
        clock.start("audio_ms")
        levels = analyze.audio_levels(settings.ffmpeg_bin, src)
        audio_class, audio_reason = analyze.classify_audio(levels)
        clock.stop("audio_ms")

    speech: dict[str, object] = {
        "present": False, "words": 0, "approximate": True,
        "transcript_excerpt": "", "reason": "No audio to transcribe.",
        "turns": [], "turn_count": 0,
    }
    pauses: list[dict[str, float]] = []
    if has_audio:
        job.stage = "Finding silent pauses."
        job.check()
        clock.start("pauses_ms")
        pauses = analyze.silence_spans(settings.ffmpeg_bin, src)
        clock.stop("pauses_ms")
        job.check()
    if audio_class == "non_silent":
        job.stage = "Transcribing speech."
        job.check()
        wav_path = os.path.join(
            APP.storage.tmp, f"{uuid.uuid4().hex}.wav"
        )
        clock.start("transcribe_ms")
        try:
            convert_to_wav(settings.ffmpeg_bin, src, wav_path)
            job.check()
            spoken = _transcribe_wav_file(
                wav_path, build_request_part("ENDPOINTING", [], [])
            )
        finally:
            clock.stop("transcribe_ms")
            if os.path.exists(wav_path):
                os.unlink(wav_path)
        job.check()
        words = len(str(spoken.get("text", "") or "").split())
        excerpt = str(spoken.get("text", "") or "")[:280]
        raw_turns = spoken.get("turns", [])
        kept_turns: list[dict[str, object]] = []
        if isinstance(raw_turns, list):
            for turn in raw_turns[:200]:
                if not isinstance(turn, dict):
                    continue
                item: dict[str, object] = {
                    "text": str(turn.get("text", "") or "")[:280],
                }
                for key in ("start", "end"):
                    value = turn.get(key)
                    if isinstance(value, (int, float)):
                        item[key] = float(value)
                kept_turns.append(item)
        speech = {
            "present": words > 0,
            "words": words,
            "approximate": not _turns_have_timestamps(spoken),
            "transcript_excerpt": excerpt,
            "reason": (
                "Speech transcribed from the audio track." if words else
                "Audible audio with no detected speech "
                "(music or background sound)."
            ),
            "turns": kept_turns,
            "turn_count": len(raw_turns) if isinstance(raw_turns, list) else 0,
        }
        mark_verified("voice_transcribe")
    elif audio_class == "silence":
        speech["reason"] = "Audio is silent; nothing to transcribe."

    job.stage = "Detecting scene changes."
    job.check()
    clock.start("cuts_ms")
    cuts = analyze.scene_cuts(settings.ffmpeg_bin, src)
    clock.stop("cuts_ms")
    job.check()

    job.stage = "Sampling representative frames."
    frames: list[dict[str, object]] = []
    clock.start("frames_ms")
    for at in analyze.frame_times(duration):
        job.check()
        frame_id = APP.storage.new_id()
        dest = os.path.join(APP.storage.assets, frame_id + ".jpg")
        analyze.extract_frame(settings.ffmpeg_bin, src, at, dest)
        APP.storage.register(frame_id, {
            "path": dest, "name": f"frame at {at}s.jpg", "kind": "asset",
        })
        frames.append({"asset_id": frame_id, "t": at,
                       "url": f"/api/files/{frame_id}"})
    clock.stop("frames_ms")

    words = int(speech.get("words", 0) or 0)
    result: dict[str, object] = {
        "file_id": file_id,
        "duration": duration,
        "width": width, "height": height,
        "fps": float(media.get("fps", 30.0) or 30.0),
        "orientation": analyze.orientation_of(width, height),
        "has_audio": has_audio,
        "audio": {"class": audio_class, "detail": audio_reason,
                  "levels": levels or {}},
        "speech": speech,
        "cuts": cuts,
        "pauses": pauses,
        "frames": frames,
        "frame_budget_note": (
            f"{len(frames)} sampled frames cannot show every moment; "
            "treat them as spot checks, not full understanding."
        ),
        "content_hint": analyze.content_hint(
            duration, has_audio, audio_class, words, len(cuts)
        ),
        "timings": clock.as_dict(),
    }
    try:
        with open(_analysis_cache_path(file_id), "w",
                  encoding="utf-8") as handle:
            json.dump(result, handle)
    except OSError:
        pass
    return result


def route_analyze_start(body: dict[str, object]) -> dict[str, object]:
    file_id = body.get("file_id")
    if not isinstance(file_id, str) or not file_id:
        raise ApiError(400, "file_id is required.")
    if not body.get("refresh"):
        cached = _load_cached_analysis(file_id)
        if cached is not None:
            job = analyze.Job(uuid.uuid4().hex, file_id)
            job.state = "done"
            job.stage = "Reused the cached analysis for this footage."
            job.result = cached
            return job.to_dict()
    # Validate the file before spending a thread on it.
    APP.media_of(file_id)
    job = _ANALYZE_JOBS.start(
        file_id, lambda j: _run_analysis_job(j, file_id)
    )
    # The worker thread may already be running; the start response
    # deterministically reports the accepted state. Live progress
    # comes from GET /api/analyze/<job>.
    return {"job_id": job.id, "state": "queued",
            "stage": "Waiting to start."}


def route_analyze_get(job_id: str) -> dict[str, object]:
    job = _ANALYZE_JOBS.get(job_id)
    if job is None:
        raise ApiError(404, "Unknown analysis job.")
    return job.to_dict()


def route_analyze_cancel(job_id: str) -> dict[str, object]:
    job = _ANALYZE_JOBS.get(job_id)
    if job is None:
        raise ApiError(404, "Unknown analysis job.")
    stopped = job.cancel()
    payload = job.to_dict()
    payload["cancelled"] = stopped
    return payload


_BRIEF_LANGUAGES = ("en", "ta", "mixed")
_BRIEF_GOALS = ("captions", "product", "reel", "tutorial", "travel",
                "custom", "reference")


def _brief_clarification(
    goal: str, detail: str, obs: str
) -> str | None:
    """One focused question for genuinely ambiguous brief requests."""
    text = f"{goal} {detail}".strip()
    if re.search(r"\d+\s*(min|mins|minute|minutes)\b", text, re.I):
        return ("Do you mean minutes or seconds? Videos here are short, "
                "so tell me the unit — for example 'first 5 seconds'.")
    quoted = re.findall(r"[\"']([^\"']{2,60})[\"']", detail)
    if "name" in detail.lower() or "called" in detail.lower():
        called = re.search(r"(?:called|named)\s+([A-Z][A-Za-z]{1,30})",
                           detail)
        target = quoted[0] if quoted else (
            called.group(1) if called else "that name")
        return (f"What is '{target}' — a person, a product, or something "
                "else in the footage?")
    if goal == "custom" and len(detail.split()) < 3:
        return ("Tell me a little more: what should the edit keep, add, "
                "or change?")
    if goal == "captions" and "speech: none detected" in obs:
        return ("This footage has no detected speech, so speech captions "
                "are not possible. Want written titles instead? Tell me "
                "the title text.")
    if goal == "reference" and not obs:
        return ("Which video should the style come from? Upload a "
                "reference video first, or pick another goal.")
    return None


def route_brief(body: dict[str, object]) -> dict[str, object]:
    goal = str(body.get("goal", "") or "").strip().lower()
    if goal not in _BRIEF_GOALS:
        raise ApiError(
            400, f"goal must be one of {', '.join(_BRIEF_GOALS)}."
        )
    language = str(body.get("language", "en") or "en").strip().lower()
    if language not in _BRIEF_LANGUAGES:
        raise ApiError(
            400, f"language must be one of {', '.join(_BRIEF_LANGUAGES)}."
        )
    detail = str(body.get("detail", "") or "").strip()
    if len(detail) > 2000:
        raise ApiError(400, "detail must be under 2000 characters.")
    obs = _analysis_for_request(body)
    reference = body.get("reference_style")
    reference_text = ""
    if isinstance(reference, dict) and reference:
        reference_text = (
            "Reference style to match (adapt timing to the user's "
            f"footage): {json.dumps(reference, ensure_ascii=False)}"
        )
    question = _brief_clarification(goal, detail, obs if goal != "reference"
                                    else reference_text)
    if question is not None:
        return {"question": question}
    catalog = "\n".join(
        f"- {entry['op']}: {entry['desc']} ({entry['fields']})"
        for entry in OP_CATALOG
    )
    lang_name = {"en": "English", "ta": "Tamil",
                 "mixed": "mixed Tamil-English"}[language]
    system = (
        "Write an editing brief for a phone video editor. The brief is "
        "plain prose the app turns into an editing plan; it must use "
        "ONLY observations given here and the user's goal. Never invent "
        "products, dialogue, scene events, claims, or timestamps. "
        "Mention only these operations, in words: "
        f"{catalog}. "
        "Reply in " + lang_name + ". "
        "If anything is still ambiguous (a name, time unit, or goal), "
        "set question instead of guessing."
    )
    user_lines = [f"Goal: {goal}."]
    if detail:
        user_lines.append(f"User's words: {detail}.")
    if obs:
        user_lines.append(f"Footage observations: {obs}")
    if reference_text:
        user_lines.append(reference_text + ".")
    try:
        response = APP.client().chat_completions(
            MODEL_SPARK,
            [{"role": "system", "content": system},
             {"role": "user", "content": "\n".join(user_lines)}],
            {"type": "json_schema", "json_schema": {
                "name": "EditingBrief",
                "schema": {
                    "type": "object",
                    "properties": {
                        "brief": {"type": "string"},
                        "explains": {"type": "array",
                                     "items": {"type": "string"}},
                        "question": {"type": ["string", "null"]},
                    },
                    "required": ["brief", "explains", "question"],
                    "additionalProperties": False,
                },
            }},
            timeout=120.0,
        )
    except MetaAPIError as exc:
        mark_failed("spark_plan", exc.message)
        raise
    try:
        content = MetaClient.chat_text(response)
        data = json.loads(content or "{}")
    except (ValueError, MetaAPIError) as exc:
        raise ApiError(502, f"Bad brief payload: {exc}") from None
    if not isinstance(data, dict):
        raise ApiError(502, "Bad brief payload: not an object.")
    brief = str(data.get("brief", "") or "").strip()
    explains = data.get("explains", [])
    model_question = str(data.get("question", "") or "").strip()
    if model_question:
        return {"question": model_question}
    if not brief:
        raise ApiError(502, "The brief came back empty; try again.")
    mark_verified("spark_plan")
    return {
        "brief": brief,
        "explains": [str(item) for item in explains
                     if isinstance(item, str)][:12],
        "language": language,
        "goal": goal,
    }


def _transferable_traits(
    aspect: str, shots: int, avg_shot_len: float, fades: int,
    has_colors: bool,
) -> list[str]:
    """Style traits with enough measured evidence to apply.

    Only traits listed here transfer to the user's footage (framing
    via the aspect switch, pacing/fades/grade via the plan). An
    empty list means the reference carries no usable style signal.
    """
    traits: list[str] = []
    if aspect != "original":
        traits.append(f"Framing: match the {aspect} aspect (optional).")
    if shots >= 2:
        traits.append(
            f"Pacing: {shots} shots, about {avg_shot_len}s each.")
    if fades > 0:
        traits.append(f"Fades: {fades} fade(s) to black.")
    if has_colors:
        traits.append("Grade: overall brightness and saturation.")
    return traits


def route_reference_analyze(body: dict[str, object]) -> dict[str, object]:
    file_id = body.get("file_id")
    if not isinstance(file_id, str) or not file_id:
        raise ApiError(400, "file_id is required.")
    entry, media = APP.media_of(file_id)
    src = entry["path"]
    duration = float(media["duration"])
    width = int(media["width"])
    height = int(media["height"])
    clock = _Stopwatch()
    clock.start("cuts_ms")
    cuts = analyze.scene_cuts(APP.settings.ffmpeg_bin, src)
    clock.stop("cuts_ms")
    shots = len(cuts) + 1
    avg_shot = round(duration / max(shots, 1), 2)
    colors: list[dict[str, float]] = []
    clock.start("color_ms")
    for at in (round(duration * 0.25, 2), round(duration * 0.5, 2),
               round(duration * 0.75, 2)):
        tmp = os.path.join(APP.storage.tmp, f"{uuid.uuid4().hex}.png")
        try:
            analyze.extract_frame(
                APP.settings.ffmpeg_bin, src, at, tmp, width=320
            )
            sample = analyze.frame_color(APP.settings.ffmpeg_bin, tmp)
            if sample:
                colors.append(sample)
        except RuntimeError:
            pass
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
    clock.stop("color_ms")
    brightness = (
        round(sum(c["brightness"] for c in colors) / len(colors), 1)
        if colors else 0.0
    )
    saturation = (
        round(sum(c["saturation"] for c in colors) / len(colors), 1)
        if colors else 0.0
    )
    aspect = (
        "9:16" if height > width * 1.2 else
        "16:9" if width > height * 1.2 else "1:1"
        if abs(width - height) < max(width, height) * 0.05 else
        "original"
    )
    clock.start("fades_ms")
    fades = analyze.black_fades(APP.settings.ffmpeg_bin, src)
    clock.stop("fades_ms")
    profile: dict[str, object] = {
        "file_id": file_id,
        "aspect": aspect,
        "width": width, "height": height,
        "duration": duration,
        "shots": shots,
        "avg_shot_len": avg_shot,
        "cuts": cuts[:analyze.MAX_CUTS_REPORTED],
        "fades_to_black": fades,
        "has_audio": bool(media.get("has_audio", False)),
        "brightness": brightness,
        "saturation": saturation,
        "transferable": _transferable_traits(
            aspect, shots, avg_shot, fades, bool(colors)),
        "unsupported": [
            "Caption layout and text animations cannot be read from "
            "pixels without a vision model.",
            "Dissolve/wipe types are not detected; only fades to black.",
            "The reference's dialogue, music, logos, and footage are "
            "never imported.",
        ],
    }
    return {"style": profile, "timings": clock.as_dict()}


def route_frame(body: dict[str, object]) -> dict[str, object]:
    file_id = body.get("file_id")
    if not isinstance(file_id, str) or not file_id:
        raise ApiError(400, "file_id is required.")
    try:
        at = float(body.get("t", 0.0) or 0.0)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        raise ApiError(400, "t must be seconds.") from None
    entry, media = APP.media_of(file_id)
    if at < 0 or at > float(media["duration"]):
        raise ApiError(
            400, f"t must sit inside 0–{float(media['duration']):.1f}s."
        )
    frame_id = APP.storage.new_id()
    dest = os.path.join(APP.storage.assets, frame_id + ".jpg")
    try:
        analyze.extract_frame(
            APP.settings.ffmpeg_bin, entry["path"], at, dest, width=960
        )
    except RuntimeError as exc:
        raise ApiError(503, str(exc)) from None
    APP.storage.register(frame_id, {
        "path": dest, "name": f"frame at {at}s.jpg", "kind": "asset",
    })
    return {"image_id": frame_id, "url": f"/api/files/{frame_id}", "t": at}


_COVER_POSITIONS = ("top", "bottom")


def route_cover(body: dict[str, object]) -> dict[str, object]:
    """Video cover: a real frame plus a title (Tamil-safe via libass)."""
    file_id = body.get("file_id")
    if not isinstance(file_id, str) or not file_id:
        raise ApiError(400, "file_id is required.")
    try:
        at = float(body.get("t", 0.0) or 0.0)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        raise ApiError(400, "t must be seconds.") from None
    title = str(body.get("title", "") or "").strip()
    if not title or len(title) > 120:
        raise ApiError(400, "title must be 1-120 characters.")
    position = str(body.get("position", "bottom") or "bottom")
    if position not in _COVER_POSITIONS:
        raise ApiError(400, "position must be top or bottom.")
    entry, media = APP.media_of(file_id)
    duration = float(media["duration"])
    if at < 0 or at > duration:
        raise ApiError(400, f"t must sit inside 0–{duration:.1f}s.")
    frame = route_frame({"file_id": file_id, "t": at})
    frame_entry = APP.storage.get(str(frame["image_id"]))
    assert frame_entry is not None
    ass_text = build_ass(
        [{"start": 0.0, "end": 60.0, "text": title,
          "position": "top" if position == "top" else "bottom",
          "size": "large", "animation": "none"}],
        int(media["width"]), int(media["height"]),
    )
    ass_path = os.path.join(APP.storage.tmp, f"{uuid.uuid4().hex}.ass")
    cover_id = APP.storage.new_id()
    out_path = os.path.join(APP.storage.assets, cover_id + ".png")
    try:
        with open(ass_path, "w", encoding="utf-8") as handle:
            handle.write(ass_text)
        escaped = escape_ass_path(ass_path)
        done = subprocess.run(
            [APP.settings.ffmpeg_bin, "-y", "-v", "error",
             "-i", frame_entry["path"], "-vf", f"ass={escaped}",
             "-frames:v", "1", out_path],
            capture_output=True, text=True, timeout=120, check=False,
        )
    finally:
        if os.path.exists(ass_path):
            os.unlink(ass_path)
    if done.returncode != 0 or not os.path.exists(out_path):
        raise ApiError(503, f"Cover render failed: {done.stderr[-200:]}")
    APP.storage.register(cover_id, {
        "path": out_path, "name": "cover.png", "kind": "asset",
    })
    return {"image_id": cover_id, "url": f"/api/files/{cover_id}"}


_NAME_LIKE_RE = re.compile(
    r"\b([A-Z][a-z]{2,}(?:\s+[A-Za-z][a-z]{2,})+)\b"
)
# Generic words that never make a phrase name-like on their own.
_NAME_STOPLIST = frozenset({
    "video", "cover", "poster", "day", "vlog", "trip", "song", "movie",
    "match", "celebration", "festival", "tamil", "nadu", "india",
})
# Words that anchor a phrase as software/product context: "Video creator
# app 23editor" names the product itself, so there is nothing to ask.
_PRODUCT_CONTEXT = frozenset({
    "app", "application", "software", "website", "webpage", "logo",
    "icon", "product", "tool", "editor", "program", "platform",
    "startup", "brand", "company", "23editor",
})


def _artwork_clarification(
    prompt: str, analysis: dict[str, object] | None
) -> str | None:
    """Ask before generating when the prompt names an unknown subject.

    A transliterated name such as 'Air rakumaan' must never become an
    unrelated product ad: when name-like words appear that the footage
    analysis never observed, ask what the user means.
    """
    candidates = [
        c for c in _NAME_LIKE_RE.findall(prompt)
        if any(word.lower() not in _NAME_STOPLIST
               for word in c.split())
    ]
    if not candidates:
        return None
    known = ""
    if isinstance(analysis, dict):
        speech = analysis.get("speech", {})
        if isinstance(speech, dict):
            known = str(speech.get("transcript_excerpt", "") or "").lower()
    def _observed(phrase: str) -> bool:
        # Trailing lowercase words ("celebrating") describe the scene;
        # the name itself is the leading capitalized part.
        parts = phrase.split()
        for end in range(len(parts), 1, -1):
            if " ".join(parts[:end]).lower() in known:
                return True
        return False

    unknown = [c for c in candidates if not _observed(c)]
    # A candidate carrying its own product context ("Video creator app")
    # is understood; only genuinely unanchored names get the question.
    unknown = [
        c for c in unknown
        if not any(w.lower() in _PRODUCT_CONTEXT for w in c.split())
    ]
    if not unknown:
        return None
    name = unknown[0].split()[0] + (
        f" {unknown[0].split()[1]}" if len(unknown[0].split()) > 1 else ""
    )
    return (f"What is '{name}' — a person, a product, or something else? "
            "I will not guess: describe it in a few words, or pick a "
            "video-frame cover instead.")


_SIZE_RE = re.compile(r"^(\d{3,4})x(\d{3,4})$")


def route_thumbnail(body: dict[str, object]) -> dict[str, object]:
    prompt = body.get("prompt", "")
    if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 1000:
        raise ApiError(400, "prompt must be 1-1000 characters.")
    size = str(body.get("size", "1280x720"))
    if not _SIZE_RE.match(size):
        raise ApiError(400, "size must look like 1280x720.")
    analysis = body.get("analysis")
    question = _artwork_clarification(
        prompt.strip(), analysis if isinstance(analysis, dict) else None
    )
    if question is not None:
        raise ApiError(422, question, details={"question": question})
    try:
        image_bytes = APP.client().generate_image(prompt.strip(), size)
    except MetaAPIError as exc:
        mark_failed("image_assets", exc.message)
        if classify_provider_error(exc) == "policy":
            raise ApiError(
                422, "The image service refused this request "
                f"({exc.message}). Filters are never bypassed or "
                "reworded here — use a video-frame cover instead.",
                details={"suggest_cover": True,
                         "provider_message": exc.message},
            ) from None
        raise
    image_id = APP.storage.new_id()
    out_path = os.path.join(APP.storage.assets, image_id + ".png")
    with open(out_path, "wb") as handle:
        handle.write(image_bytes)
    APP.storage.register(image_id, {
        "path": out_path, "name": "thumbnail.png", "kind": "asset",
    })
    mark_verified("image_assets")
    return {"image_id": image_id, "url": f"/api/files/{image_id}"}


class TimeoutHTTPServer(ThreadingHTTPServer):
    """Threaded server with stalled-connection timeouts.

    Each accepted socket gets a read/write timeout so a stalled phone
    cannot hold a handler thread forever. Slow-but-moving transfers are
    unaffected: the timeout only fires after 120s with zero bytes.
    """

    daemon_threads = True
    request_timeout = 120.0

    def get_request(self):  # noqa: N802
        sock, addr = super().get_request()
        sock.settimeout(self.request_timeout)
        return sock, addr


def main() -> None:
    settings = APP.settings
    # Loopback by default; a physical phone on the same Wi-Fi needs
    # BACKEND_HOST=0.0.0.0 so the app can reach this backend.
    host = os.environ.get("BACKEND_HOST", "127.0.0.1")
    server = TimeoutHTTPServer((host, settings.port), Handler)
    sys.stderr.write(
        f"23Editor backend on http://{host}:{settings.port}\n"
        f"API key present: {settings.has_api_key}\n"
        f"Storage: {settings.storage_dir}\n"
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
