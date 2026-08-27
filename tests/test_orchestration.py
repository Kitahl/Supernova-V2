from __future__ import annotations

import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXPECTED_ROLES = {
    "MF01",
    "MF02",
    "MF03",
    "MF04",
    "MF05",
    "MM01",
    "MM02",
    "MM03",
    "MM04",
    "MM05",
    "MM07",
    "EXT01",
    "MM06",
    "MF06",
    "BIL00",
}


class OrchestrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.board = json.loads(
            (ROOT / "orchestration" / "BOARD.json").read_text(encoding="utf-8")
        )
        cls.tasks = json.loads(
            (ROOT / "orchestration" / "TASKS.json").read_text(encoding="utf-8")
        )

    def test_exactly_one_task_and_ticket_per_role(self) -> None:
        task_roles = [task["role"] for task in self.tasks["tasks"]]
        ticket_roles = [ticket["owner"] for ticket in self.board["tickets"]]
        self.assertEqual(EXPECTED_ROLES, set(task_roles))
        self.assertEqual(EXPECTED_ROLES, set(ticket_roles))
        self.assertEqual(len(task_roles), len(set(task_roles)))
        self.assertEqual(len(ticket_roles), len(set(ticket_roles)))

    def test_task_ids_and_ticket_ids_are_unique_and_connected(self) -> None:
        scheduler_ids = [task["scheduler_task_id"] for task in self.tasks["tasks"]]
        task_ticket_ids = {task["ticket_id"] for task in self.tasks["tasks"]}
        board_ticket_ids = {ticket["id"] for ticket in self.board["tickets"]}
        self.assertEqual(len(scheduler_ids), len(set(scheduler_ids)))
        self.assertEqual(task_ticket_ids, board_ticket_ids)

    def test_builders_start_ready_and_supervisors_wait(self) -> None:
        by_owner = {ticket["owner"]: ticket for ticket in self.board["tickets"]}
        for role in EXPECTED_ROLES - {"MM06", "MF06", "BIL00"}:
            self.assertEqual("READY", by_owner[role]["status"])
        for role in {"MM06", "MF06", "BIL00"}:
            self.assertEqual("WAITING", by_owner[role]["status"])


if __name__ == "__main__":
    unittest.main()
