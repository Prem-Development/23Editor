"""Provider error classification and secret redaction. Pure tests.

classify_provider_error implements the documented taxonomy from
https://dev.meta.ai/docs/error-handling (401 auth, 403 access,
429 quota, 400/code content_policy_violation) plus this client's own
status-0 shapes (timeout vs network vs malformed response). Policy
refusals route to the cover suggestion; nothing is bypassed or
reworded. Secrets (the API key) must never appear in payloads,
messages, or debug headers. No network, ffmpeg, or Meta calls;
isolated temp storage with saved/restored marker globals.
"""
import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

import server as server_module
from config import Settings
from meta_client import (
    MetaAPIError,
    _redacted_headers,
    classify_provider_error,
)
from server import ApiError, route_thumbnail, status_payload

FAKE_KEY = "LLM_0000000000000000_testkey0000000000000001"

_ISO_TMP = None
_ISO_STORAGE = None
_ISO_SETTINGS = None
_ISO_VERIFIED = None
_ISO_AT = None
_ISO_FAILED = None


def setUpModule():
    """Isolate marker writes and settings for this whole file."""
    global _ISO_TMP, _ISO_STORAGE, _ISO_SETTINGS
    global _ISO_VERIFIED, _ISO_AT, _ISO_FAILED
    _ISO_TMP = tempfile.mkdtemp(prefix="23editor-iso-")
    _ISO_STORAGE = server_module.APP.storage
    _ISO_SETTINGS = server_module.APP.settings
    _ISO_VERIFIED = set(server_module.VERIFIED)
    _ISO_AT = dict(server_module.VERIFIED_AT)
    _ISO_FAILED = {
        key: dict(entry) for key, entry in server_module.FAILED.items()
    }
    server_module.APP.storage = server_module.Storage(
        os.path.join(_ISO_TMP, "storage")
    )


def tearDownModule():
    server_module.APP.storage = _ISO_STORAGE
    server_module.APP.settings = _ISO_SETTINGS
    server_module.VERIFIED.clear()
    server_module.VERIFIED.update(_ISO_VERIFIED)
    server_module.VERIFIED_AT.clear()
    server_module.VERIFIED_AT.update(_ISO_AT)
    server_module.FAILED.clear()
    server_module.FAILED.update(_ISO_FAILED)
    shutil.rmtree(_ISO_TMP, ignore_errors=True)


class ClassifyProviderErrorTest(unittest.TestCase):
    def test_documented_policy_code(self):
        exc = MetaAPIError(
            400, "blocked by policy",
            code="content_policy_violation", param="prompt",
        )
        self.assertEqual(classify_provider_error(exc), "policy")

    def test_legacy_policy_shapes_still_match(self):
        exc = MetaAPIError(
            400, "Rejected by safety policy", error_type="moderation"
        )
        self.assertEqual(classify_provider_error(exc), "policy")

    def test_other_400_is_client_error(self):
        exc = MetaAPIError(
            400, "`n` The number must be `<= 10`.",
            error_type="invalid_request_error", param="n",
        )
        self.assertEqual(classify_provider_error(exc), "client_error")

    def test_auth(self):
        self.assertEqual(
            classify_provider_error(MetaAPIError(401, "nope")),
            "auth",
        )
        self.assertEqual(
            classify_provider_error(MetaAPIError(
                401, "bad key", error_type="authentication_error",
                code="invalid_api_key",
            )),
            "auth",
        )

    def test_access(self):
        self.assertEqual(
            classify_provider_error(MetaAPIError(
                403, "model not entitled"
            )),
            "access",
        )

    def test_quota(self):
        self.assertEqual(
            classify_provider_error(MetaAPIError(429, "slow down")),
            "quota",
        )
        self.assertEqual(
            classify_provider_error(MetaAPIError(
                429, "Rate limit exceeded.",
                error_type="rate_limit_error",
                code="rate_limit_exceeded",
            )),
            "quota",
        )

    def test_server_faults(self):
        for status in (500, 502, 503, 504):
            self.assertEqual(
                classify_provider_error(
                    MetaAPIError(status, "backend sad")
                ),
                "server",
                status,
            )

    def test_timeout_vs_network_vs_malformed(self):
        self.assertEqual(
            classify_provider_error(MetaAPIError(
                0, "Network error: timed out"
            )),
            "timeout",
        )
        self.assertEqual(
            classify_provider_error(MetaAPIError(
                0, "Network error: [Errno 11001] getaddrinfo failed"
            )),
            "network",
        )
        for message in (
            "Unexpected JSON response",
            "Chat response has no choices",
            "Image response has no b64_json",
            "File upload returned no id",
        ):
            self.assertEqual(
                classify_provider_error(MetaAPIError(0, message)),
                "malformed",
                message,
            )


class ThumbnailPolicyRouteTest(unittest.TestCase):
    def _thumbnail_with(self, exc):
        client = mock.Mock()
        client.generate_image.side_effect = exc
        with mock.patch.object(
            server_module.APP, "client", return_value=client
        ):
            return route_thumbnail({"prompt": "a calm lake"})

    def test_documented_policy_code_suggests_cover(self):
        with self.assertRaises(ApiError) as ctx:
            self._thumbnail_with(MetaAPIError(
                400, "blocked by policy",
                code="content_policy_violation", param="prompt",
            ))
        self.assertEqual(ctx.exception.status, 422)
        self.assertTrue(ctx.exception.details.get("suggest_cover"))
        self.assertIn("never bypassed", ctx.exception.message)

    def test_quota_and_auth_pass_through_unreworded(self):
        for exc in (
            MetaAPIError(
                429, "Rate limit exceeded.",
                error_type="rate_limit_error",
                code="rate_limit_exceeded",
            ),
            MetaAPIError(
                401, "invalid key",
                error_type="authentication_error",
                code="invalid_api_key",
            ),
        ):
            with self.assertRaises(MetaAPIError) as ctx:
                self._thumbnail_with(exc)
            self.assertEqual(ctx.exception.message, exc.message)


class SecretRedactionTest(unittest.TestCase):
    def test_debug_headers_redact_authorization(self):
        headers = _redacted_headers({
            "Authorization": f"Bearer {FAKE_KEY}",
            "Content-Type": "application/json",
        })
        self.assertEqual(headers["Authorization"], "<redacted>")
        self.assertEqual(headers["Content-Type"], "application/json")
        self.assertNotIn(FAKE_KEY, json.dumps(headers))
        lower = _redacted_headers({"authorization": f"Bearer {FAKE_KEY}"})
        self.assertEqual(lower["authorization"], "<redacted>")

    def test_config_redacted_view_holds_no_key(self):
        redacted = Settings(api_key=FAKE_KEY).redacted()
        self.assertTrue(redacted["api_key_present"])
        self.assertNotIn(FAKE_KEY, json.dumps(redacted))

    def test_status_payload_reports_presence_never_value(self):
        server_module.APP.settings = Settings(
            api_key=FAKE_KEY,
            api_base="https://api.meta.ai/v1",
            port=8080,
            storage_dir=os.path.join(_ISO_TMP, "storage"),
            ffmpeg_bin="no-such-ffmpeg-23editor",
            ffprobe_bin="no-such-ffprobe-23editor",
        )
        try:
            client = mock.Mock()
            client.list_models.return_value = ["muse-spark-1.3"]
            with mock.patch.object(
                server_module.APP, "client", return_value=client
            ):
                payload = status_payload()
        finally:
            server_module.APP.settings = _ISO_SETTINGS
        self.assertTrue(payload["api_key_present"])
        self.assertNotIn(FAKE_KEY, json.dumps(payload))
