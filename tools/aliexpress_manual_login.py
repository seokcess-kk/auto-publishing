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
        print("※ Google 팝업이 닫혀도 알리 페이지가 멈춰있으면, 같은 창에서")
        print("  https://www.aliexpress.com 로 직접 이동해보세요.")
        page.goto(_LOGIN_URL)

        # 인증 쿠키 후보 — region/계정 유형에 따라 발급 키가 달라 후보를 넓게 잡는다.
        # xman_t/_hvn_login/x_user_id 등 '확실히 로그인됨'을 의미하는 marker 와
        # ali_apache_id/_m_h5_tk 같은 'visit 만 있어도 발급되는' marker 를 분리.
        strong_markers = {"xman_t", "_hvn_login", "x_user_id", "x_alimid",
                          "ali_apache_track_ae", "xman_us_f", "xman_us_t",
                          "ae_u_p_s", "_ali_apache_session"}
        # weak markers 는 fallback (메인을 한 번이라도 방문하면 발급)
        weak_markers = {"ali_apache_id", "aep_usuc_f", "_m_h5_tk", "intl_locale"}

        # 인증 쿠키가 잡힐 때까지 폴링 — Enter 입력 전에도 자동 감지해서
        # 사용자가 더 명확하게 진행 상황을 볼 수 있도록.
        import time as _t
        domains = ["https://www.aliexpress.com",
                   "https://login.aliexpress.com",
                   "https://passport.aliexpress.com",
                   "https://ko.aliexpress.com",
                   "https://my.aliexpress.com"]

        def _read_cookie_names() -> set:
            try:
                return {c["name"] for c in context.cookies(domains)}
            except Exception:
                return set()

        print()
        print(">>> 로그인 완료 후 Enter (자동 감지도 30초마다 표시) <<<")

        # Enter 입력은 별도 스레드에서 받고, 본 스레드는 쿠키 감지 폴링.
        import threading
        enter_pressed = threading.Event()

        def _wait_enter():
            try:
                input()
            except Exception:
                pass
            enter_pressed.set()

        threading.Thread(target=_wait_enter, daemon=True).start()

        cookie_names: set = set()
        last_status = ""
        nav_tried = False
        deadline = _t.time() + 600  # 최대 10분 대기
        while not enter_pressed.is_set() and _t.time() < deadline:
            cookie_names = _read_cookie_names()
            strong_hit = cookie_names & strong_markers
            weak_hit = cookie_names & weak_markers
            if strong_hit:
                status = f"✓ 강한 인증 쿠키 감지: {sorted(strong_hit)[:5]} — Enter 로 저장"
            elif weak_hit:
                status = f"… 약한 쿠키만 있음 ({sorted(weak_hit)}). 메인 페이지가 정상 로딩됐는지 확인 후 Enter"
            else:
                status = f"… 인증 쿠키 미감지 (현재 {len(cookie_names)}개)"
            if status != last_status:
                print(status)
                last_status = status

            # Google 로그인 후 부모 페이지가 멈춰있는 케이스 자동 회복:
            # 강한 마커가 없는데 60초 이상 지나면 한 번만 www.aliexpress.com 로
            # 페이지를 이동시켜 봄 (쿠키 propagation 강제).
            if (not strong_hit and not nav_tried
                    and _t.time() - (deadline - 600) > 60):
                try:
                    print("  60초 경과 — www.aliexpress.com 으로 자동 이동 시도")
                    page.goto("https://www.aliexpress.com",
                              wait_until="domcontentloaded", timeout=15000)
                    nav_tried = True
                except Exception as e:
                    print(f"  자동 이동 예외 (무시): {e}")
                    nav_tried = True

            _t.sleep(3)

        cookie_names = _read_cookie_names()
        all_markers = strong_markers | weak_markers
        has_auth = bool(cookie_names & all_markers)
        if not has_auth:
            print(f"인증 쿠키가 보이지 않습니다 (보유 {len(cookie_names)}개): "
                  f"{sorted(cookie_names)[:15]}")
            print("로그인이 완료되지 않은 것 같습니다 — 브라우저에서 다시 시도 후 재실행하세요.")
            context.close()
            return False

        context.storage_state(path=str(_STORAGE_PATH))
        context.close()

    print(f"세션 저장 완료: {_STORAGE_PATH}")
    print(f"감지된 인증 쿠키: {sorted(cookie_names & all_markers)}")
    return True


if __name__ == "__main__":
    ok = collect_and_save_state()
    sys.exit(0 if ok else 1)
