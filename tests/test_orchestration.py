from __future__ import annotations

import json
import re
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
SUPERVISORS = {"MM06", "MF06", "BIL00"}
VALID_STATUSES = {"READY", "WAITING", "DONE"}


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

    def test_worker_states_are_typed_and_supervisors_wait(self) -> None:
        by_owner = {ticket["owner"]: ticket for ticket in self.board["tickets"]}
        for ticket in self.board["tickets"]:
            self.assertIn(ticket["status"], VALID_STATUSES)
        for role in EXPECTED_ROLES - SUPERVISORS:
            self.assertIn(by_owner[role]["status"], {"READY", "DONE"})
        for role in SUPERVISORS:
            self.assertEqual("WAITING", by_owner[role]["status"])

    def test_done_tickets_bind_their_merged_pull_request(self) -> None:
        done = [ticket for ticket in self.board["tickets"] if ticket["status"] == "DONE"]
        self.assertEqual({"G1-101", "G1-102", "G1-103"}, {ticket["id"] for ticket in done})
        for ticket in done:
            completion = ticket["completion"]
            self.assertIsInstance(completion["pull_request"], int)
            self.assertGreater(completion["pull_request"], 0)
            self.assertRegex(completion["merge_commit"], re.compile(r"^[0-9a-f]{40}$"))


if __name__ == "__main__":
    unittest.main()
