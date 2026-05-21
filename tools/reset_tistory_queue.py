"""tistory_queue.json 의 claimed/failed (result_url 없는 항목) → pending 일괄 복원.

usage:
    python -m tools.reset_tistory_queue
"""
from __future__ import annotations

import json
from pathlib import Path


def main() -> int:
    tq = Path(__file__).resolve().parent.parent / "data" / "tistory_queue.json"
    if not tq.exists():
        print("tistory_queue.json 없음")
        return 1
    data = json.loads(tq.read_text(encoding="utf-8"))
    n = 0
    for it in data:
        if it.get("status") in ("claimed", "failed") and not it.get("result_url"):
            it["status"] = "pending"
            it["claimed_at"] = None
            it["error"] = ""
            n += 1
    tq.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"reset {n}건")
    for it in data[-5:]:
        title = (it.get("title", "") or "")[:50]
        print(f"  {it['id'][:8]}  status={it.get('status'):8}  title={title}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
