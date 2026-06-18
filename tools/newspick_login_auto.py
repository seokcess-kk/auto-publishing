"""뉴스픽 파트너스 수동 로그인 — Enter 불필요 자동감지 버전.

기존 newspick_manual_login.py 는 input() 으로 사용자 Enter 를 기다려서
비대화형/백그라운드 실행(스케줄러, 에이전트)에 부적합하다. 이 버전은 SESSION
쿠키가 잡힐 때까지 폴링해 자동 저장 후 종료한다.

사용:
    python tools/newspick_login_auto.py [--timeout 300]

브라우저가 열리면 partners.newspic.kr 에서 카카오 로그인만 하면 된다.
관리 페이지 도달 → SESSION 쿠키 발급 → 자동 저장 (Enter 불필요).
"""
import argparse
import os
import sys
import time
from pathlib import Path

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _BASE_DIR)

LOGIN_URL = "https://partners.newspic.kr/login"
_PROFILE_DIR = Path(_BASE_DIR) / ".sessions" / "newspick_profile"
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


def _has_session(context) -> bool:
    try:
        return any(c.get("name") == "SESSION"
                   for c in context.cookies(["https://partners.newspic.kr"]))
    except Exception:
        return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--timeout", type=int, default=300,
                    help="로그인 대기 제한시간(초)")
    args = ap.parse_args()

    from playwright.sync_api import sync_playwright
    _PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    print(f"profile: {_PROFILE_DIR}", flush=True)

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=str(_PROFILE_DIR),
            headless=False,
            user_agent=_UA,
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

        def on_page(pg):
            try:
                pg.wait_for_load_state("domcontentloaded", timeout=8000)
            except Exception:
                pass
            try:
                pg.bring_to_front()
                print(f"[popup] {pg.url[:100]}", flush=True)
            except Exception:
                pass

        context.on("page", on_page)
        page = context.pages[0] if context.pages else context.new_page()
        print(">>> 브라우저가 열렸습니다. partners.newspic.kr 에 카카오로 로그인하세요 "
              "(Enter 불필요, 자동 감지) <<<", flush=True)
        try:
            page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=20000)
        except Exception as e:
            print(f"goto 예외(무시): {e}", flush=True)

        deadline = time.time() + args.timeout
        saved = False
        while time.time() < deadline:
            if _has_session(context):
                print("SESSION 쿠키 감지 — 안정화 5초 후 저장/종료", flush=True)
                time.sleep(5)  # 로그인 직후 후속 쿠키/리다이렉트 안정화
                saved = True
                break
            time.sleep(2)

        try:
            context.close()
        except Exception:
            pass

    if saved:
        print("RESULT: SUCCESS — 세션이 영속 프로필에 저장됐습니다", flush=True)
        return 0
    print("RESULT: TIMEOUT — 제한시간 내 로그인이 완료되지 않았습니다", flush=True)
    return 1


if __name__ == "__main__":
    sys.exit(main())
