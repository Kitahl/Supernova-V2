#!/usr/bin/env python3
"""Render the hourly-worker master view from role-owned status shards."""

from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORK_ORDERS = ROOT / "orchestration" / "hourly_workers" / "MASTER_WORK_ORDER.json"
STATUS_ROOT = ROOT / "worker_reports" / "hourly"
OUTPUT = ROOT / "orchestration" / "hourly_workers" / "MASTER_STATUS.md"


def _load(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def render(root: Path = ROOT) -> str:
    authority_path = root / "orchestration" / "hourly_workers" / "MASTER_WORK_ORDER.json"
    status_root = root / "worker_reports" / "hourly"
    authority = _load(authority_path)
    orders = authority.get("work_orders")
    if not isinstance(orders, list) or len(orders) != 14:
        raise ValueError("MASTER_WORK_ORDER.json must contain exactly fourteen work orders")

    rows: list[tuple[str, str, str, str, str]] = []
    seen_roles: set[str] = set()
    seen_ids: set[str] = set()
    for raw in orders:
        if not isinstance(raw, dict):
            raise ValueError("every work order must be an object")
        role = str(raw["role"])
        work_order_id = str(raw["work_order_id"])
        if role in seen_roles or work_order_id in seen_ids:
            raise ValueError("work-order roles and IDs must be unique")
        seen_roles.add(role)
        seen_ids.add(work_order_id)
        status_path = status_root / role / "STATUS.json"
        status = _load(status_path)
        if status.get("role") != role or status.get("work_order_id") != work_order_id:
            raise ValueError(f"{status_path} does not match its work order")
        latest_report = status.get("latest_report") or "—"
        rows.append(
            (
                role,
                work_order_id,
                str(status.get("state", "UNKNOWN")),
                str(latest_report),
                str(status.get("next_goal") or raw["next_goal"]),
            )
        )

    lines = [
        "# Goal 1 hourly worker master status",
        "",
        "Generated from `worker_reports/hourly/*/STATUS.json`. Do not edit by hand.",
        "",
        "| Role | Work order | State | Latest report | Next goal |",
        "|---|---|---|---|---|",
    ]
    for role, work_order_id, state, report, next_goal in sorted(rows):
        escaped = [value.replace("|", "\\|").replace("\n", " ") for value in (role, work_order_id, state, report, next_goal)]
        lines.append("| " + " | ".join(escaped) + " |")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    rendered = render()
    OUTPUT.write_text(rendered, encoding="utf-8", newline="\n")
    print(OUTPUT.relative_to(ROOT).as_posix())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
