"""티스토리 세션 진단 — publisher 와 동일한 profile 로 /manage 직접 접근."""
from __future__ import annotations
import os, sys
from dotenv import load_dotenv
load_dotenv()

from common.browser_profile import PersistentBrowserProfile


def main() -> int:
    blog_name = os.getenv("TISTORY_BLOG_NAME", "")
    if not blog_name:
        print("[ERR] TISTORY_BLOG_NAME 미설정")
        return 2
    blog_url = f"https://{blog_name}.tistory.com"

    profile = PersistentBrowserProfile("tistory_shared")
    print(f"[INFO] profile dir: {profile.user_data_dir}")
    print(f"[INFO] dir exists: {profile.user_data_dir.exists()}")

    if profile.user_data_dir.exists():
        for rel in ("Default/Network/Cookies", "Default/Cookies"):
            p = profile.user_data_dir / rel
            mark = "OK" if p.exists() else "MISSING"
            sz = p.stat().st_size if p.exists() else 0
            print(f"[INFO] [{mark}] {rel}  size={sz}")

    # publisher 와 동일한 옵션 (headful)
    with profile.launch(headless=False) as context:
        page = context.new_page() if not context.pages else context.pages[0]
        try:
            page.goto(f"{blog_url}/manage", wait_until="domcontentloaded", timeout=15000)
        except Exception as e:
            print(f"[WARN] /manage goto 예외: {e}")

        import time as _t
        _t.sleep(3)
        print(f"[RESULT] 최종 URL: {page.url}")
        try:
            print(f"[RESULT] 페이지 제목: {page.title()}")
        except Exception as e:
            print(f"[RESULT] 페이지 제목: (가져올 수 없음 - {e})")

        all_cookies = context.cookies()
        print(f"[RESULT] 전체 쿠키 {len(all_cookies)}개")
        kakao = [c for c in all_cookies if "kakao" in c.get("domain", "")]
        tistory = [c for c in all_cookies if "tistory" in c.get("domain", "")]
        print(f"[RESULT] kakao 도메인 쿠키: {len(kakao)}개")
        for c in kakao:
            print(f"  - {c['domain']:30s} {c['name']}")
        print(f"[RESULT] tistory 도메인 쿠키: {len(tistory)}개")
        for c in tistory:
            print(f"  - {c['domain']:30s} {c['name']}")

        try:
            input("\n>>> 브라우저 화면 확인 후 Enter (창 닫지 마세요): ")
        except (EOFError, KeyboardInterrupt):
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
