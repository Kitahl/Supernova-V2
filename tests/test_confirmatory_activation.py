from __future__ import annotations

import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from supernova_goal1.activation import DurableActivationAuthority


class ConfirmatoryActivationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.protocol = json.loads(
            (ROOT / "goal1" / "CONFIRMATORY_PROTOCOL.json").read_text(encoding="utf-8")
        )
        cls.goal1 = json.loads(
            (ROOT / "goal1" / "GOAL1.json").read_text(encoding="utf-8")
        )

    def authority(self, root: str) -> DurableActivationAuthority:
        return DurableActivationAuthority(Path(root) / "activation.sqlite")

    def test_activation_is_atomic_durable_and_readback_verified(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            authority = self.authority(raw)
            result = authority.activate_once(
                self.protocol,
                self.goal1,
                operator_seed=b"o" * 32,
                activation_nonce=b"n" * 32,
            )
            reopened = self.authority(raw).read_active()
        self.assertEqual(result, reopened)
        self.assertEqual(
            "AUTHORIZED_BY_VALIDATED_EXECUTION_AUTHORITY",
            result.activation.manifest.public_manifest["dispatch_status"],
        )

    def test_replay_and_distinct_second_activation_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            authority = self.authority(raw)
            authority.activate_once(
                self.protocol,
                self.goal1,
                operator_seed=b"o" * 32,
                activation_nonce=b"n" * 32,
            )
            for nonce in (b"n" * 32, b"m" * 32):
                with (
                    self.subTest(nonce=nonce),
                    self.assertRaisesRegex(PermissionError, "already consumed"),
                ):
                    authority.activate_once(
                        self.protocol,
                        self.goal1,
                        operator_seed=b"o" * 32,
                        activation_nonce=nonce,
                    )

    def test_concurrent_activation_has_exactly_one_winner(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            authority = self.authority(raw)
            barrier = threading.Barrier(2)
            outcomes: list[str] = []

            def activate(nonce: bytes) -> None:
                barrier.wait()
                try:
                    authority.activate_once(
                        self.protocol,
                        self.goal1,
                        operator_seed=b"o" * 32,
                        activation_nonce=nonce,
                    )
                except PermissionError:
                    outcomes.append("rejected")
                else:
                    outcomes.append("activated")

            threads = [
                threading.Thread(target=activate, args=(b"a" * 32,)),
                threading.Thread(target=activate, args=(b"b" * 32,)),
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            self.assertEqual(["activated", "rejected"], sorted(outcomes))
            authority.read_active()

    def test_failure_before_commit_does_not_consume_activation(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            authority = self.authority(raw)
            with (
                patch(
                    "supernova_goal1.activation.activate_confirmatory_execution",
                    side_effect=RuntimeError("injected precommit failure"),
                ),
                self.assertRaisesRegex(RuntimeError, "precommit"),
            ):
                authority.activate_once(
                    self.protocol,
                    self.goal1,
                    operator_seed=b"o" * 32,
                    activation_nonce=b"n" * 32,
                )
            with self.assertRaises(LookupError):
                authority.read_active()
            authority.activate_once(
                self.protocol,
                self.goal1,
                operator_seed=b"o" * 32,
                activation_nonce=b"n" * 32,
            )

    def test_relative_memory_and_short_nonce_are_rejected(self) -> None:
        for path in ("relative.sqlite", ":memory:"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                DurableActivationAuthority(path)
        with (
            tempfile.TemporaryDirectory() as raw,
            self.assertRaisesRegex(ValueError, "32 random bytes"),
        ):
            self.authority(raw).activate_once(
                self.protocol,
                self.goal1,
                operator_seed=b"o" * 32,
                activation_nonce=b"short",
            )


if __name__ == "__main__":
    unittest.main()
