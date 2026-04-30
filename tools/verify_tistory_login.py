"""patched _kakao_login() fast-path 검증.

publishers/tistory.py 의 TistoryPublisher.login() 만 호출하고 close().
발행은 일절 하지 않음. 로그를 보고 'Kakao SSO fast-path — manage 직접 도달'
메시지가 뜨는지 확인.
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


def main(blog_name: str) -> int:
    pub = TistoryPublisher(blog_name)
    try:
        ok = pub.login()
    finally:
        pub.close()
    print(f"\n=== RESULT: {blog_name} login = {ok} ===")
    return 0 if ok else 1


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: python -m tools.verify_tistory_login <blog_name>")
        sys.exit(1)
    sys.exit(main(sys.argv[1]))
