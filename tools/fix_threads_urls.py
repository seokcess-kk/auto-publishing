"""publish_queue.json 의 잘못된 Threads URL 일괄 보정.

publishers/threads.py 의 버그로 발행된 글들의 URL 이 `https://www.threads.net/t/<numeric>`
형식 (404 페이지) 으로 기록됨. 정식 URL 은 Graph API 의 permalink 필드로 조회 필요.

각 항목에 대해 GET /v1.0/{post_id}?fields=permalink 호출 후 URL 갱신.

usage:
    python -m tools.fix_threads_urls
    python -m tools.fix_threads_urls --dry-run
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

# 한글/유니코드 안전 출력
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import requests
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(REPO_ROOT / ".env")

GRAPH_BASE = "https://graph.threads.net/v1.0"


def fetch_permalink(post_id: str, access_token: str) -> str:
    try:
        r = requests.get(
            f"{GRAPH_BASE}/{post_id}",
            params={"fields": "permalink", "access_token": access_token},
            timeout=10,
        )
        if r.ok:
            return r.json().get("permalink", "")
        print(f"  ✗ {post_id}: {r.status_code} {r.text[:80]}")
    except Exception as e:
        print(f"  ✗ {post_id}: {e}")
    return ""


def main() -> int:
    dry = "--dry-run" in sys.argv
    token = os.getenv("THREADS_ACCESS_TOKEN", "").strip().strip('"').strip("'")
    if not token:
        print("[ERROR] THREADS_ACCESS_TOKEN 미설정")
        return 1

    pq_path = REPO_ROOT / "data" / "publish_queue.json"
    data = json.loads(pq_path.read_text(encoding="utf-8"))

    targets = [
        x for x in data
        if x.get("platform") == "threads"
        and re.match(r"https?://www\.threads\.net/t/\d+/?$", x.get("url", ""))
    ]
    print(f"보정 대상: {len(targets)}건 ({'DRY-RUN' if dry else '실행'})")

    fixed = 0
    for it in targets:
        m = re.search(r"/t/(\d+)", it["url"])
        if not m:
            continue
        post_id = m.group(1)
        permalink = fetch_permalink(post_id, token)
        if permalink:
            print(f"  ✓ {post_id} → {permalink}")
            if not dry:
                it["url"] = permalink
            fixed += 1

    if not dry and fixed:
        pq_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n저장 완료. {fixed}건 URL 갱신.")
    elif dry:
        print(f"\n(dry-run) 실제 적용하려면 --dry-run 빼고 재실행")
    else:
        print(f"\n갱신 가능한 항목 없음 (Graph API 응답 모두 실패 — 토큰 만료?)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
