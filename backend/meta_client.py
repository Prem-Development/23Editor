"""Minimal Meta Model API client using only the Python standard library.

Verified against https://dev.meta.ai/docs (Nov 2026):
- Base URL https://api.meta.ai/v1, ``Authorization: Bearer <key>``.
- Chat Completions: POST /v1/chat/completions (OpenAI-compatible).
- Responses (streaming, used by SAM 3.1): POST /v1/responses, SSE events.
- Files: POST /v1/files (purpose=user_data), DELETE /v1/files/{id}.
- Transcribe: POST /v1/asr/transcribe, multipart parts ``request`` + ``audio``.
- Images: POST /v1/images/generations (OpenAI Images compatible).
- Errors: OpenAI-style envelope {error: {message, type, param, code}}.
- Rate limits: honor Retry-After, exponential backoff with jitter.

The API key is never logged. Debug output redacts the Authorization header.
"""
from __future__ import annotations

import io
import json
import random
import time
import urllib.error
import urllib.parse
import urllib.request

MODEL_SPARK = "muse-spark-1.3"
MODEL_WONDER = "muse-wonder-1.0"
MODEL_IMAGE = "muse-image-1.0"
MODEL_TRANSCRIBE = "muse-voice-transcribe-1.0"
MODEL_SAM = "sam-3.1"


class MetaAPIError(Exception):
    """A failed Meta Model API call with the documented error envelope."""

    def __init__(
        self,
        status: int,
        message: str,
        error_type: str = "",
        code: str = "",
        param: str = "",
    ) -> None:
        super().__init__(message)
        self.status = status
        self.type = error_type
        self.message = message
        self.code = code
        self.param = param

    @property
    def retryable(self) -> bool:
        return self.status in (429, 500, 502, 503, 504)

    def as_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "type": self.type,
            "message": self.message,
            "code": self.code,
            "param": self.param,
        }


def classify_provider_error(exc: MetaAPIError) -> str:
    """Stable failure category for a MetaAPIError.

    Per https://dev.meta.ai/docs/error-handling and
    https://dev.meta.ai/docs/image-generation#errors:
    401/code invalid_api_key is auth; 403 is access (model/feature not
    entitled); 429/code rate_limit_exceeded is quota; 400/code
    content_policy_violation is a safety refusal. Status 0 means the
    request never completed (timeout vs network vs malformed response
    is split on this client's own message shapes). Anything else is a
    retryable server fault (5xx) or a client error (other 4xx).
    """
    if (
        exc.status == 401
        or exc.type == "authentication_error"
        or exc.code == "invalid_api_key"
    ):
        return "auth"
    if exc.status == 403:
        return "access"
    if (
        exc.status == 429
        or exc.type == "rate_limit_error"
        or exc.code == "rate_limit_exceeded"
    ):
        return "quota"
    if exc.status == 400:
        if exc.code == "content_policy_violation":
            return "policy"
        blob = f"{exc.message} {exc.type} {exc.code}".lower()
        if any(
            word in blob
            for word in (
                "policy",
                "moderat",
                "safety",
                "blocked",
                "rejected",
                "inappropriate",
                "disallowed",
            )
        ):
            return "policy"
        return "client_error"
    if exc.status in (500, 502, 503, 504):
        return "server"
    if exc.status == 0:
        message = exc.message.lower()
        if "timed out" in message or "timeout" in message:
            return "timeout"
        if (
            message.startswith("unexpected")
            or "has no" in message
            or "returned no" in message
        ):
            return "malformed"
        return "network"
    return "client_error"


def _redacted_headers(headers: dict[str, str]) -> dict[str, str]:
    return {
        name: ("<redacted>" if name.lower() == "authorization" else value)
        for name, value in headers.items()
    }


def encode_multipart(
    fields: dict[str, tuple[str | None, str, bytes]],
    boundary: str,
) -> bytes:
    """Encode {name: (filename_or_None, content_type, bytes)} as multipart."""
    out = io.BytesIO()
    for name, (filename, content_type, payload) in fields.items():
        out.write(f"--{boundary}\r\n".encode("ascii"))
        disposition = f'form-data; name="{name}"'
        if filename is not None:
            disposition += f'; filename="{filename}"'
        out.write(f"Content-Disposition: {disposition}\r\n".encode("ascii"))
        out.write(f"Content-Type: {content_type}\r\n\r\n".encode("ascii"))
        out.write(payload)
        out.write(b"\r\n")
    out.write(f"--{boundary}--\r\n".encode("ascii"))
    return out.getvalue()


class MetaClient:
    """Thin wrapper over Meta Model API HTTPS endpoints."""

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.meta.ai/v1",
        timeout: float = 60.0,
        max_attempts: int = 3,
    ) -> None:
        if not api_key:
            raise ValueError("MetaClient requires an API key")
        self._key = api_key
        self._base = base_url.rstrip("/")
        self._timeout = timeout
        self._max_attempts = max(1, max_attempts)

    def _headers(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        headers = {"Authorization": f"Bearer {self._key}"}
        if extra:
            headers.update(extra)
        return headers

    @staticmethod
    def _parse_error(status: int, body: bytes) -> MetaAPIError:
        try:
            envelope = json.loads(body.decode("utf-8")).get("error", {})
        except (ValueError, UnicodeDecodeError, AttributeError):
            envelope = {}
        if not isinstance(envelope, dict):
            envelope = {}
        return MetaAPIError(
            status=status,
            message=str(envelope.get("message") or f"HTTP {status}"),
            error_type=str(envelope.get("type") or ""),
            code=str(envelope.get("code") or ""),
            param=str(envelope.get("param") or ""),
        )

    def _send(
        self,
        method: str,
        path: str,
        body: bytes | None,
        content_type: str | None,
        timeout: float | None,
        accept: str = "application/json",
    ) -> tuple[int, dict[str, str], bytes]:
        url = self._base + path
        headers = self._headers({"Accept": accept})
        if content_type and body is not None:
            headers["Content-Type"] = content_type
        request = urllib.request.Request(
            url, data=body, headers=headers, method=method
        )
        wait = 0.5
        last_error: MetaAPIError | None = None
        for attempt in range(self._max_attempts):
            try:
                with urllib.request.urlopen(
                    request, timeout=timeout or self._timeout
                ) as response:
                    raw_headers = {
                        k.lower(): v for k, v in response.getheaders()
                    }
                    return response.status, raw_headers, response.read()
            except urllib.error.HTTPError as exc:
                payload = exc.read() if hasattr(exc, "read") else b""
                err = self._parse_error(exc.code, payload)
                retry_after = exc.headers.get("Retry-After") if exc.headers else None
                last_error = err
                if err.retryable and attempt + 1 < self._max_attempts:
                    delay = wait + random.uniform(0, 0.25)
                    if retry_after:
                        try:
                            delay = max(delay, float(retry_after))
                        except ValueError:
                            pass
                    time.sleep(delay)
                    wait *= 2
                    continue
                raise err from None
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                last_error = MetaAPIError(0, f"Network error: {exc}")
                if attempt + 1 < self._max_attempts:
                    time.sleep(wait + random.uniform(0, 0.25))
                    wait *= 2
                    continue
                raise last_error from None
        raise last_error or MetaAPIError(0, "Request failed")

    def _post_json(
        self, path: str, payload: dict[str, object], timeout: float | None = None
    ) -> dict[str, object]:
        body = json.dumps(payload).encode("utf-8")
        _, _, data = self._send(
            "POST", path, body, "application/json", timeout
        )
        parsed = json.loads(data.decode("utf-8"))
        if not isinstance(parsed, dict):
            raise MetaAPIError(0, "Unexpected JSON response")
        return parsed

    # -- Models ---------------------------------------------------------
    def list_models(self) -> list[str]:
        """GET /v1/models. Free, read-only; used for the access check."""
        _, _, data = self._send("GET", "/models", None, None, 15.0)
        parsed = json.loads(data.decode("utf-8"))
        items = parsed.get("data", []) if isinstance(parsed, dict) else []
        return [str(i.get("id", "")) for i in items if isinstance(i, dict)]

    # -- Chat completions (Muse Spark / Wonder) --------------------------
    def chat_completions(
        self,
        model: str,
        messages: list[dict[str, object]],
        response_format: dict[str, object] | None = None,
        timeout: float | None = None,
    ) -> dict[str, object]:
        payload: dict[str, object] = {"model": model, "messages": messages}
        if response_format is not None:
            payload["response_format"] = response_format
        return self._post_json("/chat/completions", payload, timeout)

    @staticmethod
    def chat_text(response: dict[str, object]) -> str:
        choices = response.get("choices")
        if not isinstance(choices, list) or not choices:
            raise MetaAPIError(0, "Chat response has no choices")
        first = choices[0]
        message = first.get("message", {}) if isinstance(first, dict) else {}
        content = message.get("content", "") if isinstance(message, dict) else ""
        if isinstance(content, list):  # multipart content blocks
            parts = [
                str(p.get("text", ""))
                for p in content
                if isinstance(p, dict) and "text" in p
            ]
            return "".join(parts)
        return str(content or "")

    # -- Responses SSE stream (used by SAM 3.1) ---------------------------
    def responses_stream(
        self,
        model: str,
        input_parts: list[dict[str, object]],
        extra: dict[str, object] | None = None,
        timeout: float | None = None,
    ) -> list[dict[str, object]]:
        """POST /v1/responses with stream=true; returns parsed SSE events."""
        payload: dict[str, object] = {
            "model": model,
            "input": input_parts,
            "stream": True,
        }
        if extra:
            payload.update(extra)
        body = json.dumps(payload).encode("utf-8")
        headers = self._headers({"Content-Type": "application/json"})
        request = urllib.request.Request(
            self._base + "/responses",
            data=body,
            headers=headers,
            method="POST",
        )
        events: list[dict[str, object]] = []
        try:
            with urllib.request.urlopen(
                request, timeout=timeout or 600.0
            ) as response:
                for raw_line in response:
                    line = raw_line.decode("utf-8", "replace").strip()
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if not data or data == "[DONE]":
                        continue
                    try:
                        event = json.loads(data)
                    except ValueError:
                        continue
                    if isinstance(event, dict):
                        events.append(event)
        except urllib.error.HTTPError as exc:
            payload_bytes = exc.read() if hasattr(exc, "read") else b""
            raise self._parse_error(exc.code, payload_bytes) from None
        return events

    # -- Files ------------------------------------------------------------
    def upload_file(
        self, filename: str, content: bytes, purpose: str = "user_data"
    ) -> str:
        boundary = f"----meta{random.getrandbits(64):016x}"
        body = encode_multipart(
            {
                "purpose": (None, "text/plain", purpose.encode("ascii")),
                "file": (
                    filename,
                    "application/octet-stream",
                    content,
                ),
            },
            boundary,
        )
        _, _, data = self._send(
            "POST",
            "/files",
            body,
            f"multipart/form-data; boundary={boundary}",
            300.0,
        )
        parsed = json.loads(data.decode("utf-8"))
        file_id = parsed.get("id", "") if isinstance(parsed, dict) else ""
        if not file_id:
            raise MetaAPIError(0, "File upload returned no id")
        return str(file_id)

    def delete_file(self, file_id: str) -> None:
        safe = urllib.parse.quote(file_id, safe="")
        self._send("DELETE", f"/files/{safe}", None, None, 30.0)

    # -- Speech to text ----------------------------------------------------
    def transcribe(
        self,
        wav_bytes: bytes,
        request_part: dict[str, object],
        session_id: str | None = None,
    ) -> dict[str, object]:
        """POST /v1/asr/transcribe with `request` + `audio` multipart parts."""
        boundary = f"----meta{random.getrandbits(64):016x}"
        body = encode_multipart(
            {
                "request": (
                    None,
                    "application/json",
                    json.dumps(request_part).encode("utf-8"),
                ),
                "audio": ("clip.wav", "audio/wav", wav_bytes),
            },
            boundary,
        )
        path = "/asr/transcribe"
        if session_id:
            path += f"?sessionId={urllib.parse.quote(session_id, safe='')}"
        _, _, data = self._send(
            "POST",
            path,
            body,
            f"multipart/form-data; boundary={boundary}",
            300.0,
        )
        parsed = json.loads(data.decode("utf-8"))
        if not isinstance(parsed, dict):
            raise MetaAPIError(0, "Unexpected transcription response")
        return parsed

    # -- Images (single-shot, OpenAI Images compatible) ---------------------
    def generate_image(
        self,
        prompt: str,
        size: str = "1280x720",
        timeout: float | None = None,
    ) -> bytes:
        """POST /v1/images/generations; returns raw PNG/JPEG bytes."""
        payload = self._post_json(
            "/images/generations",
            {
                "model": MODEL_IMAGE,
                "prompt": prompt,
                "n": 1,
                "size": size,
                "response_format": "b64_json",
            },
            timeout or 300.0,
        )
        items = payload.get("data", [])
        if not isinstance(items, list) or not items:
            raise MetaAPIError(0, "Image response has no data")
        first = items[0] if isinstance(items[0], dict) else {}
        b64 = str(first.get("b64_json", ""))
        if not b64:
            raise MetaAPIError(0, "Image response has no b64_json")
        import base64

        return base64.b64decode(b64)
