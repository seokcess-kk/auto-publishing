"""뉴스픽 파트너스 운영 FAQ 페이지를 인증된 세션으로 fetch.

partners.newspic.kr 의 FAQ 는 로그인 후 접근 가능. 우리가 이미 갖춰둔
영속 프로필 + Kakao SSO 자동 로그인 흐름을 재활용해 HTML 을 받아 print.

usage:
    python -m tools.fetch_newspick_faq
"""
from __future__ import annotations
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from dotenv import load_dotenv  # noqa: E402
load_dotenv(REPO_ROOT / ".env")

from sources.newspick import NewspickSource  # noqa: E402


def main() -> int:
    src = NewspickSource()
    if not src.ensure_session():
        print("[ERROR] newspick 세션 확보 실패")
        return 1
    url = "https://partners.newspic.kr/management/operation/faq"
    try:
        # Playwright 로 fetch — SPA 일 가능성 높아 JS 렌더 필요
        from common.browser_profile import PersistentBrowserProfile
        profile = PersistentBrowserProfile("newspick")
        with profile.launch(headless=True) as context:
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=20000)
            import time
            time.sleep(3)
            html = page.content()
            # body innerText 만 — script 제외
            body_text = page.evaluate(
                "() => document.body ? document.body.innerText : ''"
            )
            out_html = REPO_ROOT / "data" / "newspick_faq.html"
            out_html.write_text(html, encoding="utf-8")
            out_txt = REPO_ROOT / "data" / "newspick_faq.txt"
            out_txt.write_text(body_text, encoding="utf-8")
            print(f"HTML 저장: {out_html} ({len(html)}자)")
            print(f"본문 텍스트 저장: {out_txt} ({len(body_text)}자)")
            print()
            print("=" * 60)
            print(body_text[:5000])
    except Exception as e:
        print(f"[ERROR] fetch 예외: {e}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
