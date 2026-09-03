from __future__ import annotations

import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MASTER = ROOT / "orchestration" / "hourly_workers" / "MASTER_WORK_ORDER.json"
TASKS = ROOT / "orchestration" / "TASKS.json"


def _load(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def _renderer():
    path = ROOT / "scripts" / "render_hourly_worker_master.py"
    spec = importlib.util.spec_from_file_location("render_hourly_worker_master", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_exact_existing_fourteen_workers_and_status_shards() -> None:
    master = _load(MASTER)
    tasks = _load(TASKS)
    registered = {
        item["role"]: item["scheduler_task_id"]
        for item in tasks["tasks"]
        if item["role"] != "BIL00"
    }
    orders = master["work_orders"]
    assert len(orders) == 14
    assert {item["role"] for item in orders} == set(registered)
    assert {
        item["role"]: item["scheduler_task_id"] for item in orders
    } == registered
    for item in orders:
        status = _load(ROOT / "worker_reports" / "hourly" / item["role"] / "STATUS.json")
        assert status["role"] == item["role"]
        assert status["work_order_id"] == item["work_order_id"]


def test_worker_paths_are_role_scoped_and_no_frozen_goal1_inputs() -> None:
    master = _load(MASTER)
    implementation_owners: dict[str, str] = {}
    for item in master["work_orders"]:
        role = item["role"]
        own_report = f"worker_reports/hourly/{role}/"
        assert own_report in item["allowed_paths"]
        for path in item["allowed_paths"]:
            assert not path.startswith("goal1/")
            if path.startswith(("src/", "scripts/", "runtime/", "integration/", "tests/", "pyproject")):
                previous = implementation_owners.setdefault(path, role)
                assert previous == role


def test_master_status_is_deterministic_and_current() -> None:
    module = _renderer()
    expected = (ROOT / "orchestration" / "hourly_workers" / "MASTER_STATUS.md").read_text(encoding="utf-8")
    assert module.render(ROOT) == expected
