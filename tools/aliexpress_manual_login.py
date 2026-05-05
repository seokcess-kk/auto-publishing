"""
알리익스프레스 수동 로그인 → storage_state.json 저장

common.aliexpress_login 의 자동 로그인은 카카오 SSO 경로만 지원한다.
Google 계정 연동, 이메일/비밀번호 직접 로그인 등 다른 방식이면 이 헬퍼로
1회 수동 로그인 후 세션을 저장한다.

사용법 (프로젝트 루트에서):
    python tools/aliexpress_manual_login.py

브라우저가 열리면 본인이 평소 쓰는 방식으로 로그인 (Google/Kakao/이메일/캡차/2FA
모두 OK), 알리익스프레스 메인이나 portals 가 정상으로 로드되면
터미널에서 Enter — storage_state 가 data/aliexpress_storage.json 에 저장된다.

알리는 Playwright 기본 컨텍스트(automation 노출)에 캡차를 더 까다롭게 띄운다.
launch_persistent_context + AutomationControlled 비활성화 + 일반 데스크톱
User-Agent 로 봇 fingerprint 를 줄여 캡차 통과율을 높인다.
"""
import os
import sys
from pathlib import Path

_BASE_DIR = Path(__file__).resolve().parent.parent
_DATA_DIR = _BASE_DIR / "data"
_STORAGE_PATH = _DATA_DIR / "aliexpress_storage.json"
_PROFILE_DIR = _BASE_DIR / ".sessions" / "aliexpress_login_profile"
_LOGIN_URL = "https://login.aliexpress.com/"

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


def collect_and_save_state() -> bool:
    from playwright.sync_api import sync_playwright

    _PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    _DATA_DIR.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        # 알리는 launch() 보다 launch_persistent_context() + 봇탐지 비활성 플래그가
        # 캡차 통과율이 훨씬 높다 (실 브라우저처럼 fingerprint 일관 유지).
        context = p.chromium.launch_persistent_context(
            user_data_dir=str(_PROFILE_DIR),
            headless=False,
            user_agent=_USER_AGENT,
            locale="ko-KR",
            args=[
                "--disable-blink-features=AutomationControlled",
                "--disable-infobars",
                "--disable-extensions",
                "--no-sandbox",
            ],
        )
        # navigator.webdriver 까지 숨겨 슬라이더/이미지 캡차 경로 회피.
        try:
            context.add_init_script(
                "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
            )
        except Exception:
            pass

        page = context.pages[0] if context.pages else context.new_page()

        print("브라우저가 열렸습니다. 알리익스프레스에 로그인하세요.")
        print("(Google/Kakao 연동, 이메일/비밀번호, 캡차, 2FA 모두 가능)")
        page.goto(_LOGIN_URL)

        input(">>> 로그인 완료 후 Enter <<<")

        # 로그인 완료 검증: 알리 인증 쿠키 (xman_t / _hvn_login / x_user_id /
        # ali_apache_id 등 가운데 하나라도 있으면 OK). region 별로 키 이름이
        # 미세히 다르니 후보를 넓게 두고 매칭.
        cookies = context.cookies(["https://www.aliexpress.com",
                                    "https://login.aliexpress.com",
                                    "https://passport.aliexpress.com",
                                    "https://ko.aliexpress.com"])
        cookie_names = {c["name"] for c in cookies}
        auth_markers = {"xman_t", "_hvn_login", "x_user_id", "ali_apache_id",
                        "aep_usuc_f", "ali_apache_track", "_m_h5_tk",
                        "ali_apache_track_ae", "intl_locale", "x_alimid"}
        has_auth = bool(cookie_names & auth_markers)
        if not has_auth:
            print(f"인증 쿠키가 보이지 않습니다 (보유 {len(cookie_names)}개): "
                  f"{sorted(cookie_names)[:15]}")
            print("로그인이 완료되지 않은 것 같습니다 — 브라우저에서 다시 시도 후 재실행하세요.")
            context.close()
            return False

        context.storage_state(path=str(_STORAGE_PATH))
        context.close()

    print(f"세션 저장 완료: {_STORAGE_PATH}")
    print(f"감지된 인증 쿠키: {sorted(cookie_names & auth_markers)}")
    return True


if __name__ == "__main__":
    ok = collect_and_save_state()
    sys.exit(0 if ok else 1)
