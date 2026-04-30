"""
네이버 브라우저 수동 로그인 → 세션 쿠키 저장

사용법:
    python3 tools/naver_login_browser.py

브라우저가 열리면 직접 로그인 (캡차/2차인증 포함) 후 Enter를 누르면
쿠키가 .sessions/naver_blog_<BLOG_ID>.pkl 에 저장됩니다.
"""
import os
import pickle
import requests
from dotenv import load_dotenv
load_dotenv()

blog_id = os.getenv("NAVER_BLOG_ID", "")
session_path = f".sessions/naver_blog_{blog_id}.pkl"


def extract_cookies_to_requests_session() -> requests.Session:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context()
        page = context.new_page()

        print("브라우저에서 네이버 로그인을 완료한 뒤 Enter를 누르세요...")
        page.goto("https://nid.naver.com/nidlogin.login?mode=form&url=https://www.naver.com")

        input(">>> 로그인 완료 후 Enter <<<")

        # 쿠키 추출
        cookies = context.cookies()
        browser.close()

    # requests.Session에 쿠키 주입
    session = requests.Session()
    session.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        )
    })
    for c in cookies:
        session.cookies.set(c["name"], c["value"], domain=c.get("domain", ""))

    cookie_names = [c["name"] for c in cookies]
    print(f"수집된 쿠키: {cookie_names}")

    has_auth = "NID_AUT" in cookie_names or "NID_SES" in cookie_names
    if not has_auth:
        print("⚠️  NID_AUT/NID_SES 쿠키가 없습니다. 로그인이 완료되지 않은 것 같습니다.")
        return session

    # 저장
    os.makedirs(".sessions", exist_ok=True)
    with open(session_path, "wb") as f:
        pickle.dump(session, f)
    print(f"✅ 세션 저장 완료: {session_path}")
    return session


if __name__ == "__main__":
    if not blog_id:
        print("❌ .env에 NAVER_BLOG_ID가 없습니다.")
        exit(1)

    session = extract_cookies_to_requests_session()

    # 저장된 세션으로 로그인 상태 확인
    r = session.get("https://www.naver.com", timeout=5)
    logged_in = "로그아웃" in r.text or "NID_AUT" in str(r.cookies)
    print(f"로그인 상태 확인: {'✅ 성공' if logged_in else '❌ 실패'}")
