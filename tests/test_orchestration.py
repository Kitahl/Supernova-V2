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
EXPECTED_SCHEDULER_IDS = {
    "MF01": "6a8416348c848191b6b4908fea710e9f",
    "MF02": "6a84163cd4fc81918b57cf7a25923976",
    "MF03": "6a84164563ec8191ac9a0902622eb676",
    "MF04": "6a84164c7bb0819192e2750f51587099",
    "MF05": "6a8416576c288191b31eb63a52854388",
    "MM01": "6a84166107d48191aaa2d95880e3b19e",
    "MM02": "6a84166713ac819185e377ef68a73788",
    "MM03": "6a84166ec498819187f84ad1bc350979",
    "MM04": "6a84167554548191904b4dbc7d2e059b",
    "MM05": "6a84167f810881919e2a300295a082fd",
    "MM07": "6a841794fe288191893747cc6114f34b",
    "EXT01": "6a84192d0f888191a4aaa9523fc07d7e",
    "MM06": "6a84168b056481919c5ebfcb4f0a1ce4",
    "MF06": "6a841697574481918e67222cacb7fcfb",
    "BIL00": "6a8413a9d7648191b06b1e8303dbcf77",
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
        cls.archive = json.loads(
            (ROOT / "orchestration" / "ARCHIVE.json").read_text(encoding="utf-8")
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
        self.assertEqual(
            EXPECTED_SCHEDULER_IDS,
            {task["role"]: task["scheduler_task_id"] for task in self.tasks["tasks"]},
        )
        self.assertEqual(task_ticket_ids, board_ticket_ids)

    def test_worker_states_are_typed_and_supervisors_wait(self) -> None:
        by_owner = {ticket["owner"]: ticket for ticket in self.board["tickets"]}
        for ticket in self.board["tickets"]:
            self.assertIn(ticket["status"], VALID_STATUSES)
        for role in EXPECTED_ROLES - SUPERVISORS:
            self.assertIn(by_owner[role]["status"], {"READY", "DONE"})
        for role in SUPERVISORS:
            self.assertEqual("WAITING", by_owner[role]["status"])

    def test_current_done_tickets_bind_their_merged_pull_request(self) -> None:
        done = [ticket for ticket in self.board["tickets"] if ticket["status"] == "DONE"]
        for ticket in done:
            completion = ticket["completion"]
            self.assertIsInstance(completion["pull_request"], int)
            self.assertGreater(completion["pull_request"], 0)
            self.assertRegex(completion["merge_commit"], re.compile(r"^[0-9a-f]{40}$"))

    def test_archive_preserves_completed_ticket_evidence(self) -> None:
        archived = self.archive["completed_tickets"]
        archived_ids = [ticket["id"] for ticket in archived]
        current_ids = {ticket["id"] for ticket in self.board["tickets"]}
        self.assertEqual({"G1-101", "G1-102", "G1-103", "G1-107"}, set(archived_ids))
        self.assertEqual(len(archived_ids), len(set(archived_ids)))
        self.assertTrue(current_ids.isdisjoint(archived_ids))
        for ticket in archived:
            self.assertIn(ticket["owner"], EXPECTED_ROLES)
            completion = ticket["completion"]
            self.assertIsInstance(completion["pull_request"], int)
            self.assertGreater(completion["pull_request"], 0)
            self.assertRegex(completion["merge_commit"], re.compile(r"^[0-9a-f]{40}$"))


if __name__ == "__main__":
    unittest.main()
