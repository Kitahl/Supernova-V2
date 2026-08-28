from __future__ import annotations

from hashlib import sha256
import unittest

from supernova_goal1.artifacts import (
    ScheduledChatArtifactEnvelope,
    ScheduledChatArtifactKind,
)
from supernova_goal1.contracts import Arm


class ScheduledChatArtifactEnvelopeTests(unittest.TestCase):
    def make(
        self, payload: str | bytes = "hello π"
    ) -> ScheduledChatArtifactEnvelope:
        return ScheduledChatArtifactEnvelope.from_visible_utf8(
            payload,
            kind=ScheduledChatArtifactKind.REQUEST,
            run_id="run-001",
            problem_id="problem-001",
            arm=Arm.ORDINARY,
            attempt=2,
            media_type="text/plain; charset=utf-8",
        )

    def test_content_address_binds_exact_utf8_bytes_and_metadata(self) -> None:
        payload = "hello π"
        encoded = payload.encode("utf-8")
        envelope = self.make(payload)
        self.assertEqual(envelope.byte_length, len(encoded))
        self.assertEqual(envelope.sha256_hex, sha256(encoded).hexdigest())
        self.assertEqual(
            envelope.artifact_id, f"sha256:{sha256(encoded).hexdigest()}"
        )
        self.assertEqual(envelope.kind, ScheduledChatArtifactKind.REQUEST)
        self.assertEqual(envelope.run_id, "run-001")
        self.assertEqual(envelope.problem_id, "problem-001")
        self.assertEqual(envelope.arm, Arm.ORDINARY)
        self.assertEqual(envelope.attempt, 2)
        self.assertEqual(envelope.media_type, "text/plain; charset=utf-8")

    def test_str_and_exact_utf8_bytes_produce_same_envelope(self) -> None:
        self.assertEqual(self.make("café"), self.make("café".encode("utf-8")))

    def test_request_and_terminal_response_are_distinct_kinds(self) -> None:
        request = self.make("same")
        response = ScheduledChatArtifactEnvelope.from_visible_utf8(
            "same",
            kind=ScheduledChatArtifactKind.TERMINAL_RESPONSE,
            run_id="run-001",
            problem_id="problem-001",
            arm=Arm.ORDINARY,
            attempt=2,
        )
        self.assertNotEqual(request.kind, response.kind)
        self.assertEqual(request.sha256_hex, response.sha256_hex)

    def test_verification_rejects_changed_bytes_and_invalid_utf8(self) -> None:
        envelope = self.make("answer\n")
        self.assertTrue(envelope.verifies("answer\n"))
        self.assertFalse(envelope.verifies("answer"))
        self.assertFalse(envelope.verifies(b"\xff"))

    def test_mapping_round_trip_is_strict_and_has_no_hidden_compute_claims(self) -> None:
        envelope = self.make()
        raw = envelope.to_mapping()
        self.assertEqual(raw["measurement_basis"], "visible_utf8_bytes")
        self.assertNotIn("provider_tokens", raw)
        self.assertNotIn("reasoning_tokens", raw)
        self.assertNotIn("hidden_compute", raw)
        self.assertEqual(ScheduledChatArtifactEnvelope.from_mapping(raw), envelope)
        bad = dict(raw)
        bad["provider_tokens"] = 10
        with self.assertRaises(ValueError):
            ScheduledChatArtifactEnvelope.from_mapping(bad)

    def test_from_mapping_rejects_forged_artifact_id(self) -> None:
        raw = self.make().to_mapping()
        raw["artifact_id"] = "sha256:" + "0" * 64
        with self.assertRaisesRegex(ValueError, "artifact_id"):
            ScheduledChatArtifactEnvelope.from_mapping(raw)

    def test_rejects_invalid_utf8_and_lone_surrogate(self) -> None:
        with self.assertRaisesRegex(ValueError, "valid UTF-8"):
            self.make(b"\xff")
        with self.assertRaisesRegex(ValueError, "Unicode scalar"):
            self.make("\ud800")

    def test_rejects_invalid_identity_attempt_arm_digest_and_media_type(self) -> None:
        cases = [
            {"run_id": " run"},
            {"problem_id": "problem\n1"},
            {"arm": "unknown"},
            {"attempt": True},
            {"media_type": "text/plain"},
            {"media_type": "text/plain; charset=latin-1"},
        ]
        for override in cases:
            kwargs = dict(
                kind=ScheduledChatArtifactKind.REQUEST,
                run_id="run",
                problem_id="problem",
                arm=Arm.ORDINARY,
                attempt=0,
                media_type="text/plain; charset=utf-8",
            )
            kwargs.update(override)
            with self.subTest(override=override), self.assertRaises(ValueError):
                ScheduledChatArtifactEnvelope.from_visible_utf8("x", **kwargs)

        with self.assertRaisesRegex(ValueError, "sha256_hex"):
            ScheduledChatArtifactEnvelope(
                kind=ScheduledChatArtifactKind.REQUEST,
                run_id="run",
                problem_id="problem",
                arm=Arm.ORDINARY,
                attempt=0,
                media_type="text/plain; charset=utf-8",
                byte_length=1,
                sha256_hex="x" * 64,
            )

    def test_envelope_is_tuple_immutable_and_non_subclassable(self) -> None:
        envelope = self.make()
        with self.assertRaises((AttributeError, TypeError)):
            envelope.byte_length = 99  # type: ignore[misc]
        with self.assertRaises(TypeError):

            class Child(ScheduledChatArtifactEnvelope):
                pass


if __name__ == "__main__":
    unittest.main()
