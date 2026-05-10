"""
뉴스픽 폴링 로그인 — 비대화형 변형 (Enter 입력 불필요)

브라우저가 열리면 partners.newspic.kr 에 카카오 로그인만 하면 SESSION
쿠키 등장을 감지하는 즉시 자동 종료. 영속 프로필
.sessions/newspick_profile/ 에 저장된다.

사용법:
    python tools/newspick_login_poll.py             # 5분 대기 (기본)
    python tools/newspick_login_poll.py --wait 600  # 10분 대기
"""
import os
import sys
import time
from pathlib import Path

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _BASE_DIR)

LOGIN_URL = "https://partners.newspic.kr/login"
PROFILE_DIR = Path(_BASE_DIR) / ".sessions" / "newspick_profile"

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


def _has_authenticated_session(context, page) -> bool:
    """SESSION 쿠키 + URL 이 /login 을 벗어난 상태 둘 다 만족할 때만 True.

    partners 가 anonymous 사용자에게도 SESSION 을 발급하므로 쿠키 단독
    체크는 false positive 발생. sources/newspick.py 의 동명 함수와 동일
    조건을 적용한다.
    """
    try:
        if "/login" in page.url:
            return False
    except Exception:
        return False
    cookies = context.cookies(["https://partners.newspic.kr"])
    return any(c.get("name") == "SESSION" for c in cookies)


def _watch_popups(context) -> None:
    def on_page(p):
        try:
            p.wait_for_load_state("domcontentloaded", timeout=8000)
        except Exception:
            pass
        try:
            p.bring_to_front()
            print(f"[POPUP] {p.url[:120]}")
        except Exception:
            pass
    context.on("page", on_page)


def collect(wait_seconds: int = 300) -> bool:
    from playwright.sync_api import sync_playwright

    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    print(f"[INFO] profile dir: {PROFILE_DIR}")

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            headless=False,
            user_agent=_USER_AGENT,
            locale="ko-KR",
            args=[
                "--disable-blink-features=AutomationControlled",
                "--disable-popup-blocking",
                "--disable-features=IsolateOrigins,site-per-process,SitePerProcess",
                "--disable-site-isolation-trials",
                "--no-sandbox",
                "--disable-infobars",
                "--disable-extensions",
            ],
        )
        _watch_popups(context)

        page = context.pages[0] if context.pages else context.new_page()
        print(f"[INFO] 브라우저 오픈 — partners.newspic.kr 에 카카오 로그인하세요 (최대 {wait_seconds}초)")
        page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=20000)

        deadline = time.time() + wait_seconds
        last_url = ""
        while time.time() < deadline:
            try:
                if _has_authenticated_session(context, page):
                    print("[OK] 인증 SESSION 감지 + /login 벗어남 — 로그인 완료")
                    break
                # 다른 탭(popup) 에서 카카오 로그인 후 닫혀도 메인 page 가
                # 자동 redirect 되지 않을 수 있어 명시적 reload 필요
                cur = page.url[:100]
                if cur != last_url:
                    print(f"[POLL] {cur}")
                    last_url = cur
            except Exception as e:
                print(f"[WARN] 폴링 오류 (계속): {e}")
            time.sleep(2)
        else:
            print("[ERROR] 시간 초과 — 로그인 미완료")
            try:
                cookies = context.cookies(["https://partners.newspic.kr"])
                print(f"[INFO] 감지 쿠키({len(cookies)}개): {[c['name'] for c in cookies][:15]}")
            except Exception:
                pass
            context.close()
            return False

        time.sleep(1)
        context.close()

    print(f"[OK] 세션 영속 저장 완료: {PROFILE_DIR}")
    return True


if __name__ == "__main__":
    wait = 300
    if "--wait" in sys.argv:
        idx = sys.argv.index("--wait")
        if idx + 1 < len(sys.argv):
            wait = int(sys.argv[idx + 1])

    ok = collect(wait_seconds=wait)
    sys.exit(0 if ok else 1)
