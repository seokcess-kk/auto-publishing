"""DKAPTCHA 풀이가 Playwright Chromium 에선 reject 되는 문제 검증.

가설: Daum/Kakao 캡차 서버가 Playwright Chromium 의 fingerprint 를 봇으로
감지해 사용자 풀이를 silent reject 한다. Playwright 가 실제 Chrome 바이너리
를 사용하면 (channel='chrome') 일반 Chrome 과 동일한 fingerprint 라 통과
할 가능성이 있다.

전제: 시스템에 Google Chrome 이 설치되어 있어야 한다.
       (Windows 기본: C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe)

흐름:
  1. Playwright + channel='chrome' + persistent profile (.sessions/tistory_shared_profile)
  2. 사용자가 직접 로그인 → 글쓰기 → 발행 → DKAPTCHA 풀이
  3. 캡차 통과/실패 결과를 사용자가 직접 보고
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from dotenv import load_dotenv  # noqa: E402
load_dotenv(REPO_ROOT / ".env")

from common.logger import log  # noqa: E402


PROFILE_DIR = REPO_ROOT / ".sessions" / "tistory_shared_profile"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)


def main(blog: str) -> int:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("[ERROR] playwright 미설치")
        return 1

    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    # orphan chromium 정리 (혹시 모르니)
    try:
        from common.browser_profile import _kill_orphan_chromium_windows
        _kill_orphan_chromium_windows(PROFILE_DIR)
    except Exception:
        pass

    print("=" * 70)
    print(" Playwright + 실제 Chrome 바이너리 (channel='chrome')")
    print("=" * 70)
    print(f" 프로필: {PROFILE_DIR}")
    print()

    with sync_playwright() as p:
        try:
            context = p.chromium.launch_persistent_context(
                user_data_dir=str(PROFILE_DIR),
                channel="chrome",  # ← Chromium 대신 실제 Chrome
                headless=False,
                user_agent=USER_AGENT,
                locale="ko-KR",
                viewport={"width": 1280, "height": 800},
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--disable-features=IsolateOrigins,site-per-process",
                ],
            )
        except Exception as e:
            print(f"[ERROR] Chrome 채널 launch 실패: {e}")
            print()
            print(" 가능한 원인:")
            print("   • Google Chrome 미설치 — chrome.google.com 에서 설치 후 재시도")
            print("   • Chrome 이 이미 실행 중 (같은 user_data_dir 점유 가능성)")
            print("   • Playwright 버전이 channel 미지원")
            return 1

        # navigator.webdriver / chrome 등 봇 마커 마스킹
        try:
            context.add_init_script(
                r"""
                Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
                window.chrome = window.chrome || {};
                window.chrome.runtime = window.chrome.runtime || {};
                """
            )
        except Exception:
            pass

        page = context.pages[0] if context.pages else context.new_page()

        blog_url = f"https://{blog}.tistory.com"
        page.goto(f"{blog_url}/manage", wait_until="domcontentloaded", timeout=20000)
        time.sleep(2)

        print(" Chrome 창이 열렸습니다. 직접 다음을 해주세요:")
        print(f"  (1) /manage 화면에서 글쓰기 진입 (또는 {blog_url}/manage/posts 에서 글쓰기)")
        print("  (2) 제목/본문 작성 → 우상단 '완료' → '공개' 선택 → '공개 발행'")
        print("  (3) DKAPTCHA 위젯 풀이 후 '답변 제출'")
        print()
        print(" 결과 보고:")
        print("   ✅ 캡차 통과 + 발행 성공 → 자동화 가능성 있음")
        print("   ❌ 캡차 풀이 후에도 reject → channel='chrome' 으로도 안 됨")
        print()
        print(" 작업 끝나면 이 콘솔에 Enter 입력 → 브라우저 닫고 종료")
        try:
            input(" >>> Enter: ")
        except (KeyboardInterrupt, EOFError):
            pass

        context.close()
    return 0


if __name__ == "__main__":
    blog = sys.argv[1] if len(sys.argv) > 1 else os.getenv("TISTORY_BLOG_NAME", "kkkseok")
    sys.exit(main(blog))
