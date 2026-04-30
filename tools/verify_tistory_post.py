"""티스토리 post.json CSRF 패치 검증 — visibility=0 비공개 발행.

CSRF 토큰 lazy 추출이 작동하는지 실제로 post() 호출. visibility=0 으로
비공개 발행하므로 라이브에 노출 안 됨.

usage:
  .venv/bin/python -m tools.verify_tistory_post <blog_name>
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(REPO_ROOT / ".env")
except ImportError:
    pass

from publishers.tistory import TistoryPublisher  # noqa: E402


def main(blog: str) -> int:
    pub = TistoryPublisher(blog)
    if not pub.login():
        print("login FAILED")
        return 1

    print(f"_csrf_token before post: {pub._csrf_token!r}")

    cat_name = sys.argv[2] if len(sys.argv) > 2 else ""
    result = pub.post(
        title="[TEST] CSRF 패치 검증 — 비공개",
        content="<p>이 글은 자동 검증용 비공개 발행입니다. 무시하셔도 됩니다.</p>",
        tags=["테스트"],
        category=cat_name,
        visibility=0,  # 0=비공개
    )

    print(f"_csrf_token after post: {pub._csrf_token!r}")
    print(f"\n=== RESULT ===")
    print(f"success: {result.success}")
    print(f"url: {result.url}")
    print(f"post_id: {result.post_id}")
    print(f"message: {result.message}")

    pub.close()
    return 0 if result.success else 1


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: python -m tools.verify_tistory_post <blog_name>")
        sys.exit(1)
    sys.exit(main(sys.argv[1]))
