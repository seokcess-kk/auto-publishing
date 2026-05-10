"""
네이버 자동 폴링 로그인 — 비대화형 변형 (Enter 입력 불필요)

브라우저가 열리면 직접 로그인만 하면 됨. 스크립트가 NID_AUT 쿠키 등장을
감지하는 순간 자동으로 .sessions/naver_blog_<BLOG_ID>.pkl 에 저장하고 종료.

사용법:
    python tools/naver_login_poll.py             # 5분 대기 (기본)
    python tools/naver_login_poll.py --wait 600  # 10분 대기

기존 tools/naver_manual_login.py 와 동일한 형식으로 저장하므로 publisher
자동 인증 흐름과 호환됨.
"""
import os
import pickle
import sys
import time

from dotenv import load_dotenv

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(_BASE_DIR, ".env"))

BLOG_ID = os.getenv("NAVER_BLOG_ID", "")
SESSION_DIR = os.path.join(_BASE_DIR, ".sessions")
SESSION_PATH = os.path.join(SESSION_DIR, f"naver_blog_{BLOG_ID}.pkl")


def collect_and_save(wait_seconds: int = 300) -> dict:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context()
        page = context.new_page()

        print(f"[INFO] 브라우저 오픈 — 네이버에 로그인하세요 (최대 {wait_seconds}초 대기)")
        page.goto("https://nid.naver.com/nidlogin.login?mode=form&url=https://www.naver.com")

        # NID_AUT/NID_SES 쿠키 등장까지 폴링
        deadline = time.time() + wait_seconds
        last_status = ""
        while time.time() < deadline:
            try:
                cookies = context.cookies(["https://www.naver.com",
                                            "https://nid.naver.com"])
                names = {c["name"] for c in cookies}
                if names & {"NID_AUT", "NID_SES"}:
                    print("[OK] NID_AUT/NID_SES 쿠키 감지 — 로그인 완료")
                    break
                cur = page.url[:80]
                if cur != last_status:
                    print(f"[POLL] 현재 URL: {cur}")
                    last_status = cur
            except Exception as e:
                print(f"[WARN] 쿠키 폴링 오류 (계속): {e}")
            time.sleep(2)
        else:
            print("[ERROR] 시간 초과 — 로그인 미완료")
            browser.close()
            return {}

        # blog.naver.com 방문해 블로그 전용 쿠키도 적재
        try:
            page.goto("https://blog.naver.com",
                      wait_until="domcontentloaded", timeout=15000)
            time.sleep(2)
        except Exception as e:
            print(f"[WARN] blog.naver.com 이동 실패 (계속): {e}")

        cookies = context.cookies(["https://www.naver.com",
                                    "https://blog.naver.com",
                                    "https://nid.naver.com"])
        browser.close()

    cookie_dict = {c["name"]: c["value"] for c in cookies}
    print(f"[INFO] 수집된 쿠키 {len(cookie_dict)}개: {sorted(cookie_dict.keys())}")

    if not ({"NID_AUT", "NID_SES"} & cookie_dict.keys()):
        print("[ERROR] NID_AUT/NID_SES 누락 — 저장 안 함")
        return {}

    os.makedirs(SESSION_DIR, exist_ok=True)
    with open(SESSION_PATH, "wb") as f:
        pickle.dump(cookie_dict, f)
    print(f"[OK] 세션 저장 완료: {SESSION_PATH}")
    return cookie_dict


if __name__ == "__main__":
    if not BLOG_ID:
        print("[ERROR] .env 의 NAVER_BLOG_ID 가 비어있습니다.")
        sys.exit(1)

    wait = 300
    if "--wait" in sys.argv:
        idx = sys.argv.index("--wait")
        if idx + 1 < len(sys.argv):
            wait = int(sys.argv[idx + 1])

    collected = collect_and_save(wait_seconds=wait)
    sys.exit(0 if collected else 1)
