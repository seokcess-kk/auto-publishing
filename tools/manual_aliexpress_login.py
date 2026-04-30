"""알리 수동 로그인 — Chromium 창 띄우고 사용자가 직접 처리.

자동 약관 동의/자동 클릭 일절 안 함. 사용자가 화면 보고 직접:
  1. 약관 동의 (4개 체크박스 + '동의 및 계속')
  2. 카카오 로그인 (ID/PW + 추가 인증)
  3. portals.aliexpress.com 도달

코드는 portals 도달을 30초마다 폴링 → 도달 시 storage_state.json 저장.

usage:
  .venv/bin/python -m tools.manual_aliexpress_login
"""
from __future__ import annotations

import os
import pickle
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(REPO_ROOT / ".env")
except ImportError:
    pass


LOGIN_URL = "https://login.aliexpress.com/"
PORTALS_URL = "https://portals.aliexpress.com/affiportals/web/link_generator.htm"
DATA_DIR = REPO_ROOT / "data"
COOKIE_PATH = DATA_DIR / "aliexpress_cookies.pkl"
STORAGE_PATH = DATA_DIR / "aliexpress_storage.json"
WAIT_MIN = int(os.getenv("ALIEXPRESS_LOGIN_WAIT", "600"))  # 기본 10분


def main() -> int:
    from playwright.sync_api import sync_playwright

    DATA_DIR.mkdir(parents=True, exist_ok=True)

    print(f"[manual] LOGIN_URL: {LOGIN_URL}")
    print(f"[manual] storage 저장 경로: {STORAGE_PATH}")
    print(f"[manual] 대기 시간: {WAIT_MIN}초")
    print(f"[manual] 사용자가 직접 약관 동의 + 카카오 로그인 + portals 도달까지 진행하세요.")
    print(f"[manual] 30초마다 portals 접근으로 로그인 성공 여부 확인.")

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=False,
            args=["--disable-blink-features=AutomationControlled"],
        )
        context = browser.new_context(
            user_agent=("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/124.0.0.0 Safari/537.36"),
            locale="ko-KR",
        )
        page = context.new_page()

        try:
            page.goto(LOGIN_URL, timeout=30000, wait_until="domcontentloaded")
        except Exception as e:
            print(f"[manual] goto 예외: {e}")

        print(f"[manual] 페이지 열림: {page.url}")
        print(f"[manual] === 사용자 작업 대기 시작 ===")

        deadline = time.time() + WAIT_MIN
        logged_in = False
        last_url = ""
        while time.time() < deadline:
            time.sleep(30)
            cur_url = page.url
            if cur_url != last_url:
                print(f"[manual] 현재 URL: {cur_url[:120]}")
                last_url = cur_url

            # 별도 페이지에서 portals 접근 시도
            bg = None
            try:
                bg = context.new_page()
                bg.goto(PORTALS_URL, timeout=15000, wait_until="domcontentloaded")
                time.sleep(1)
                if "login" not in bg.url.lower():
                    print(f"[manual] ✅ portals 접근 성공 — 로그인 확정")
                    logged_in = True
                    bg.close()
                    break
                bg.close()
            except Exception:
                try:
                    if bg:
                        bg.close()
                except Exception:
                    pass

            remaining = int(deadline - time.time())
            if remaining > 0:
                print(f"[manual] 아직 미완료 — 잔여 {remaining}초")

        if not logged_in:
            print(f"[manual] ❌ 시간 초과 — 로그인 미완료")
            try:
                browser.close()
            except Exception:
                pass
            return 1

        # 쿠키 + storage 저장
        raw = context.cookies()
        cookie_dict = {c["name"]: c["value"] for c in raw}
        with open(COOKIE_PATH, "wb") as f:
            pickle.dump(cookie_dict, f)
        print(f"[manual] 쿠키 저장: {COOKIE_PATH} ({len(cookie_dict)}개)")

        context.storage_state(path=str(STORAGE_PATH))
        print(f"[manual] storage 저장: {STORAGE_PATH}")

        try:
            browser.close()
        except Exception:
            pass
        return 0


if __name__ == "__main__":
    sys.exit(main())
