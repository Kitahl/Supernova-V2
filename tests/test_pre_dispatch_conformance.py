from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from supernova_goal1 import pre_dispatch_conformance as subject


class PreDispatchConformanceTests(unittest.TestCase):
    def test_frozen_authority_byte_drift_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            authority = root / "goal1" / "authority.json"
            authority.parent.mkdir()
            authority.write_bytes(b'{"value":"frozen"}\n')
            protocol = {
                "sealed_rules": {
                    "frozen_authorities": {
                        "test": {
                            "path": "goal1/authority.json",
                            "git_blob_sha1": subject.git_blob_sha1(
                                authority.read_bytes()
                            ),
                        }
                    }
                }
            }

            authority.write_bytes(b'{"value":"drifted"}\n')

            with self.assertRaisesRegex(
                subject.PreDispatchConformanceError,
                "authority bytes differ from sealed Git blob",
            ):
                subject.check_frozen_authority_blobs(root, protocol)

    def test_wrong_executor_binding_fails_before_execution_authority(self) -> None:
        root = Path(__file__).resolve().parents[1]
        with (
            patch.object(
                subject,
                "load_repository_execution_bindings",
                side_effect=ValueError("wrong executor image or model"),
            ),
            patch.object(subject, "load_execution_authority") as authority_loader,
            self.assertRaisesRegex(
                subject.PreDispatchConformanceError,
                r"EXECUTOR_BINDING.*wrong executor image or model",
            ),
        ):
            subject.assert_repository_conformance(root)

        authority_loader.assert_not_called()


if __name__ == "__main__":
    unittest.main()
