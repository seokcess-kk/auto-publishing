"""
네이버 브라우저 수동 로그인 → 세션 쿠키 저장

사용법 (프로젝트 루트에서):
    python3 tools/naver_manual_login.py

브라우저가 열리면 직접 로그인 (캡차/2차인증 포함) 후 Enter를 누르면
쿠키가 .sessions/naver_blog_<BLOG_ID>.pkl 에 저장됩니다.

저장 형식: {name: value} dict (SessionManager 호환)
"""
import os
import pickle
from dotenv import load_dotenv

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(_BASE_DIR, ".env"))

blog_id = os.getenv("NAVER_BLOG_ID", "")
session_dir = os.path.join(_BASE_DIR, ".sessions")
session_path = os.path.join(session_dir, f"naver_blog_{blog_id}.pkl")


def collect_and_save_cookies() -> dict:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context()
        page = context.new_page()

        print("브라우저가 열렸습니다. 네이버에 로그인하세요.")
        page.goto("https://nid.naver.com/nidlogin.login?mode=form&url=https://www.naver.com")

        input(">>> 로그인 완료 후 Enter <<<")

        # 로그인 완료 검증: NID_AUT 쿠키가 있는지 먼저 확인.
        # 미완료 상태에서 blog.naver.com 으로 가면 nid.naver.com 으로 redirect
        # 되며 Playwright 가 'Navigation interrupted' 예외를 던지므로,
        # 이 시점에 쿠키만 보고 빠르게 실패시키는 편이 명확하다.
        pre_cookies = context.cookies(["https://www.naver.com", "https://nid.naver.com"])
        pre_names = {c["name"] for c in pre_cookies}
        if not (pre_names & {"NID_AUT", "NID_SES"}):
            print("NID_AUT/NID_SES 쿠키가 없습니다 — 로그인이 완료되지 않은 것 같습니다.")
            print("브라우저에서 로그인(캡차·2단계 포함)을 마친 뒤 재실행하세요.")
            browser.close()
            return {}

        # blog.naver.com 방문 → blog 전용 쿠키도 함께 적재.
        # networkidle 은 광고/추적 스크립트로 timeout 이 자주 나서 domcontentloaded 사용.
        try:
            page.goto("https://blog.naver.com", wait_until="domcontentloaded", timeout=15000)
        except Exception as e:
            print(f"blog.naver.com 이동 실패 (계속 진행): {e}")

        # 쿠키 추출
        cookies = context.cookies(["https://www.naver.com", "https://blog.naver.com",
                                    "https://nid.naver.com"])
        browser.close()

    cookie_names = [c["name"] for c in cookies]
    print(f"수집된 쿠키 ({len(cookies)}개): {cookie_names}")

    has_auth = "NID_AUT" in cookie_names or "NID_SES" in cookie_names
    if not has_auth:
        print("NID_AUT/NID_SES 쿠키가 없습니다. 로그인이 완료되지 않은 것 같습니다.")
        return {}

    # SessionManager 호환 형식: {name: value} dict
    cookie_dict = {c["name"]: c["value"] for c in cookies}

    os.makedirs(session_dir, exist_ok=True)
    with open(session_path, "wb") as f:
        pickle.dump(cookie_dict, f)
    print(f"세션 저장 완료: {session_path}")
    return cookie_dict


if __name__ == "__main__":
    if not blog_id:
        print(".env에 NAVER_BLOG_ID가 없습니다.")
        exit(1)

    cookies = collect_and_save_cookies()
    if cookies:
        print(f"저장된 쿠키 키: {list(cookies.keys())}")
    print("완료.")
