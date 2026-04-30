"""티스토리 manage 페이지에서 x-csrf-token 추출 가능 여부 진단.

publishers/tistory.py 의 abf6345 커밋이 'x-csrf-token 자동 첨부'를 만들었지만
토큰을 *추출*하는 로직은 없음. 09:30 newspick→tistory 발행 400 의 root cause
가설 확정용.

진단 항목:
  1. login 후 /manage 페이지 HTML 에서 csrf 후보 추출 (script, meta, cookie)
  2. /manage/newpost/ 도 시도 (실제 발행 직전 페이지)
  3. 쿠키, localStorage, window 객체 dump

usage:
  .venv/bin/python -m tools.probe_tistory_csrf <blog_name>
"""
from __future__ import annotations

import sys
import re
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

    page = pub._page
    print(f"=== /manage HTML 패턴 검색 ===")
    try:
        html = page.content()
        print(f"manage url: {page.url}")
        print(f"html len: {len(html)}")

        # csrf 관련 패턴 — meta, JS variable, hidden input
        patterns = [
            r'name=["\']csrf[_-]?token["\']\s+content=["\']([^"\']+)["\']',
            r'meta[^>]*content=["\']([^"\']+)["\'][^>]*name=["\']csrf[_-]?token["\']',
            r'csrfToken["\']?\s*[:=]\s*["\']([^"\']+)["\']',
            r'_csrf["\']?\s*[:=]\s*["\']([^"\']+)["\']',
            r'CSRF["\']?\s*[:=]\s*["\']([^"\']+)["\']',
            r'tistory_csrf["\']?\s*[:=]\s*["\']([^"\']+)["\']',
            r'name=["\']_csrf["\']\s+value=["\']([^"\']+)["\']',
            r'name=["\']csrfToken["\']\s+value=["\']([^"\']+)["\']',
        ]
        found = False
        for p in patterns:
            for m in re.finditer(p, html, re.IGNORECASE):
                print(f"  HIT [{p}]: {m.group(1)[:80]}")
                found = True
        if not found:
            print("  (no csrf-like pattern in HTML)")

        # window 객체 키 dump
        keys = page.evaluate("""
            () => Object.keys(window).filter(k =>
                /csrf|token/i.test(k)
            )
        """)
        print(f"window keys (csrf/token): {keys}")

        # cookie dump (csrf/token 관련만)
        cookies = pub._context.cookies()
        print(f"=== Cookies (csrf/token/T_) ===")
        for c in cookies:
            n = c.get('name', '')
            if any(k.lower() in n.lower() for k in ['csrf', 'token', 'TSSESSION', '_T_', 'TIARA']):
                print(f"  {n} = {c.get('value', '')[:60]}... domain={c.get('domain', '')}")

    except Exception as e:
        print(f"error: {e}")

    # /manage/newpost/?type=post 도 시도
    print(f"\n=== /manage/newpost 시도 ===")
    try:
        page.goto(f"{pub.blog_url}/manage/newpost/?type=post", wait_until="domcontentloaded", timeout=15000)
        import time; time.sleep(2)
        print(f"newpost url: {page.url}")
        html2 = page.content()
        for p in patterns:
            for m in re.finditer(p, html2, re.IGNORECASE):
                print(f"  HIT [{p}]: {m.group(1)[:80]}")
        # 거기서 글로벌 변수 다시 점검
        keys2 = page.evaluate("""
            () => Object.keys(window).filter(k =>
                /csrf|token/i.test(k)
            )
        """)
        print(f"window keys (csrf/token): {keys2}")
    except Exception as e:
        print(f"newpost error: {e}")

    pub.close()
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: python -m tools.probe_tistory_csrf <blog_name>")
        sys.exit(1)
    sys.exit(main(sys.argv[1]))
