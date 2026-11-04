"""Status-ladder tests: truthful per-integration states.

Covers unavailable (no key), implemented_but_untested, verified,
provider_error (a failure newer than the last success), and Wonder's
planned-until-attempted rule. No Meta calls, no ffmpeg: the client and
settings are mocked. Isolated temp storage only; global marker maps
are saved and restored so mocked tests never leak state.
"""
import os
import shutil
import tempfile
import unittest
from unittest import mock

import server as server_module
from config import Settings
from meta_client import (
    MODEL_IMAGE,
    MODEL_SAM,
    MODEL_SPARK,
    MODEL_TRANSCRIBE,
    MODEL_WONDER,
    MetaAPIError,
)
from server import (
    failure_of,
    mark_failed,
    mark_verified,
    status_payload,
)


def _settings(api_key=""):
    return Settings(
        api_key=api_key,
        api_base="https://api.meta.ai/v1",
        port=8080,
        storage_dir=os.path.join(tempfile.gettempdir(), "unused"),
        ffmpeg_bin="no-such-ffmpeg-23editor",
        ffprobe_bin="no-such-ffprobe-23editor",
    )


def _row(payload, integration):
    return next(
        entry for entry in payload["integrations"]
        if entry["id"] == integration
    )


class StatusLadderTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="23editor-status-")
        self._old_storage = server_module.APP.storage
        self._old_settings = server_module.APP.settings
        self._old_verified = set(server_module.VERIFIED)
        self._old_at = dict(server_module.VERIFIED_AT)
        self._old_failed = {
            key: dict(entry)
            for key, entry in server_module.FAILED.items()
        }
        server_module.APP.storage = server_module.Storage(
            os.path.join(self.tmp, "storage")
        )
        server_module.VERIFIED.clear()
        server_module.VERIFIED_AT.clear()
        server_module.FAILED.clear()

    def tearDown(self):
        server_module.APP.storage = self._old_storage
        server_module.APP.settings = self._old_settings
        server_module.VERIFIED.clear()
        server_module.VERIFIED.update(self._old_verified)
        server_module.VERIFIED_AT.clear()
        server_module.VERIFIED_AT.update(self._old_at)
        server_module.FAILED.clear()
        server_module.FAILED.update(self._old_failed)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _payload(self, api_key="", list_models=None, list_error=None):
        server_module.APP.settings = _settings(api_key)
        client = mock.Mock()
        if list_error is not None:
            client.list_models.side_effect = list_error
        else:
            client.list_models.return_value = list_models or []
        with mock.patch.object(
            server_module.APP, "client", return_value=client
        ):
            return status_payload()

    def test_no_key_marks_meta_rows_unavailable(self):
        payload = self._payload()
        self.assertFalse(payload["api_key_present"])
        self.assertEqual(payload["api_access"], "no_key")
        for integration in (
            "spark_plan", "voice_transcribe", "sam_track", "image_assets"
        ):
            self.assertEqual(
                _row(payload, integration)["status"], "unavailable",
                integration,
            )

    def test_key_without_activity_is_untested(self):
        payload = self._payload(
            api_key="LLM_test", list_models=["muse-spark-1.3"]
        )
        self.assertTrue(payload["api_key_present"])
        self.assertEqual(payload["api_access"], "verified")
        for integration in (
            "spark_plan", "voice_transcribe", "sam_track", "image_assets"
        ):
            self.assertEqual(
                _row(payload, integration)["status"],
                "implemented_but_untested",
                integration,
            )

    def test_verified_after_live_success(self):
        mark_verified("spark_plan")
        payload = self._payload(
            api_key="LLM_test", list_models=["muse-spark-1.3"]
        )
        row = _row(payload, "spark_plan")
        self.assertEqual(row["status"], "verified")
        self.assertTrue(row["verified_at"])

    def test_failure_newer_than_success_is_provider_error(self):
        mark_verified("voice_transcribe")
        mark_failed("voice_transcribe", "upstream 503 flaked")
        payload = self._payload(
            api_key="LLM_test", list_models=["muse-spark-1.3"]
        )
        row = _row(payload, "voice_transcribe")
        self.assertEqual(row["status"], "provider_error")
        self.assertIn("upstream 503 flaked", row["detail"])
        self.assertIn("Latest attempt failed at", row["detail"])
        # The last success stamp stays visible beside the failure.
        self.assertTrue(row["verified_at"])

    def test_success_clears_the_failure(self):
        mark_failed("sam_track", "boom")
        mark_verified("sam_track")
        self.assertIsNone(failure_of("sam_track"))
        payload = self._payload(
            api_key="LLM_test", list_models=["muse-spark-1.3"]
        )
        self.assertEqual(
            _row(payload, "sam_track")["status"], "verified"
        )

    def test_failure_persists_across_reload(self):
        mark_failed("image_assets", "policy refused the prompt")
        path = os.path.join(
            server_module.APP.storage.root, "failed.json"
        )
        self.assertTrue(os.path.exists(path))
        server_module.FAILED.clear()
        self.assertIsNone(failure_of("image_assets"))
        server_module._load_verified()
        entry = failure_of("image_assets")
        self.assertIsNotNone(entry)
        assert entry is not None
        self.assertIn("policy refused", entry["message"])
        payload = self._payload(
            api_key="LLM_test", list_models=["muse-spark-1.3"]
        )
        self.assertEqual(
            _row(payload, "image_assets")["status"], "provider_error"
        )

    def test_failure_message_is_truncated(self):
        mark_failed("spark_plan", "x" * 500)
        entry = failure_of("spark_plan")
        assert entry is not None
        self.assertLessEqual(len(entry["message"]), 160)

    def test_wonder_is_planned_until_attempted(self):
        payload = self._payload(
            api_key="LLM_test", list_models=["muse-spark-1.3"]
        )
        self.assertEqual(
            _row(payload, "wonder_plan")["status"], "planned"
        )
        mark_verified("wonder_plan")
        payload = self._payload(
            api_key="LLM_test", list_models=["muse-spark-1.3"]
        )
        self.assertEqual(
            _row(payload, "wonder_plan")["status"], "verified"
        )

    def test_auth_error_access_is_reported(self):
        payload = self._payload(
            api_key="LLM_bad",
            list_error=MetaAPIError(401, "bad key"),
        )
        self.assertEqual(payload["api_access"], "auth_error")

    def test_unreachable_access_is_reported(self):
        payload = self._payload(
            api_key="LLM_test",
            list_error=MetaAPIError(503, "down"),
        )
        self.assertEqual(payload["api_access"], "unreachable")

    def test_details_carry_real_model_ids(self):
        payload = self._payload(
            api_key="LLM_test", list_models=["muse-spark-1.3"]
        )
        self.assertIn(MODEL_SPARK, _row(payload, "spark_plan")["detail"])
        self.assertIn(
            MODEL_TRANSCRIBE, _row(payload, "voice_transcribe")["detail"]
        )
        self.assertIn(MODEL_SAM, _row(payload, "sam_track")["detail"])
        self.assertIn(
            MODEL_IMAGE, _row(payload, "image_assets")["detail"]
        )
        self.assertIn(
            MODEL_WONDER, _row(payload, "wonder_plan")["detail"]
        )

    def test_payload_never_contains_the_key(self):
        payload = self._payload(
            api_key="LLM_super_secret_value",
            list_models=["muse-spark-1.3"],
        )
        self.assertNotIn("LLM_super_secret_value", repr(payload))


if __name__ == "__main__":
    unittest.main()
