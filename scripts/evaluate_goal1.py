from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from supernova_goal1 import evaluate_experiment  # noqa: E402


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print("usage: evaluate_goal1.py SPEC.json RECORDS.json", file=sys.stderr)
        return 2
    try:
        spec = json.loads(Path(argv[1]).read_text(encoding="utf-8"))
        records = json.loads(Path(argv[2]).read_text(encoding="utf-8"))
        if not isinstance(records, list):
            raise ValueError("records file must contain a JSON array")
        result = evaluate_experiment(spec, records)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"decision": "INVALID", "reason": str(exc)}, sort_keys=True))
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
