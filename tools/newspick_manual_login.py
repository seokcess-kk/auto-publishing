"""
뉴스픽 파트너스(partners.newspic.kr) 수동 로그인 헬퍼

sources.newspick._kakao_login 은 NEWSPICK_ID/NEWSPICK_PW 로 자동 입력하는데,
간편로그인 / 추가 인증 단계가 다양해 자동화가 자주 막힌다. 이 헬퍼는
PersistentBrowserProfile(.sessions/newspick_profile/) 을 headful 로 띄워
사용자가 직접 카카오 로그인 → 세션 쿠키 영속 보존을 보장한다.

사용법 (프로젝트 루트에서):
    python tools/newspick_manual_login.py

브라우저가 열리면 partners.newspic.kr/login 에서 카카오 로그인 (간편로그인
or ID/PW + 캡차/2FA 모두 OK). 로그인 후 partners.newspic.kr/main 같은
관리 페이지가 뜨면 Enter — SESSION 쿠키가 영속 프로필에 저장돼 다음
파이프라인 실행부터 자동 인증된다.
"""
import os
import sys
import time
from pathlib import Path

# Windows 콘솔(cp949)에서 한글 print 가 깨지는 것 방지
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _BASE_DIR)

LOGIN_URL = "https://partners.newspic.kr/login"
PROFILE_NAME = "newspick"
_PROFILE_DIR = Path(_BASE_DIR) / ".sessions" / f"{PROFILE_NAME}_profile"

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


def _session_cookie_value(context):
    """현재 SESSION 쿠키 값 (없으면 None)."""
    for c in context.cookies(["https://partners.newspic.kr"]):
        if c.get("name") == "SESSION":
            return c.get("value")
    return None


def _watch_popups(context) -> None:
    """새 페이지(popup) 등장 시 즉시 전면으로 가져오고 URL 출력."""
    def on_page(p):
        try:
            p.wait_for_load_state("domcontentloaded", timeout=8000)
        except Exception:
            pass
        try:
            p.bring_to_front()
            print(f"[popup 감지] {p.url[:120]}")
        except Exception as e:
            print(f"[popup 감지 (bring_to_front 실패)] {p.url[:120]} ({e})")
    context.on("page", on_page)


def collect() -> bool:
    from playwright.sync_api import sync_playwright

    _PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    print(f"profile dir: {_PROFILE_DIR}")

    with sync_playwright() as p:
        # 직접 launch_persistent_context 호출 — popup 차단/about:blank 회피용
        # 추가 args 를 줄 수 있게. 카카오 SSO 가 새 window 로 navigation 못
        # 하는 사례 ("창은 뜨는데 about:blank") 는 보통 SitePerProcess /
        # Cross-Origin Window 정책 충돌이라 관련 기능을 비활성화한다.
        context = p.chromium.launch_persistent_context(
            user_data_dir=str(_PROFILE_DIR),
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

        # 카카오 SSO popup 이 뒤편/다른 모니터에 숨는 사례를 방지.
        _watch_popups(context)

        page = context.pages[0] if context.pages else context.new_page()

        print("브라우저가 열렸습니다. partners.newspic.kr 에 로그인하세요.")
        print("(카카오 간편로그인 / ID·PW / 캡차 모두 가능)")
        print("카카오 버튼을 누르면 popup 창이 뜹니다. 작업표시줄/Alt-Tab 으로 확인하세요.")
        page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=20000)

        # 로그인 완료를 SESSION 쿠키로 자동 감지 (폴링). input() 으로 Enter 를
        # 기다리지 않으므로 비대화형 실행(`! python ...`, 파이프 등)에서도
        # EOFError 없이 동작한다. 프로필에 남은 만료 쿠키를 오인하지 않도록
        # '시작 시점과 다른 새 SESSION 값'이 생겼을 때만 성공으로 본다.
        initial = _session_cookie_value(context)
        wait_sec = int(os.getenv("NEWSPICK_LOGIN_WAIT_SEC", "300"))
        print(f">>> 브라우저에서 로그인하세요. 로그인이 감지되면 자동 저장됩니다 "
              f"— 최대 {wait_sec}s 대기 <<<")
        deadline = time.time() + wait_sec
        detected = False
        while time.time() < deadline:
            try:
                cur = _session_cookie_value(context)
                if cur and cur != initial:
                    detected = True
                    print("로그인 감지됨 — 새 SESSION 쿠키 확인.")
                    break
            except Exception:
                pass
            time.sleep(3)

        # 디버그 — 현재 컨텍스트의 모든 페이지 URL 출력
        try:
            print(f"열린 탭/창 ({len(context.pages)}):")
            for i, pg in enumerate(context.pages):
                print(f"  [{i}] {pg.url[:120]}")
        except Exception:
            pass

        if not detected:
            print(f"새 로그인 미감지 ({wait_sec}s 대기) — 세션 저장 안 함.")
            print("브라우저에서 partners.newspic.kr 관리 페이지까지 로그인했는지 확인 후 재실행하세요.")
            try:
                cookies = context.cookies(["https://partners.newspic.kr"])
                print(f"감지 쿠키({len(cookies)}개): {[c.get('name') for c in cookies][:15]}")
            except Exception:
                pass
            context.close()
            return False

        time.sleep(1)
        context.close()

    print(f"세션 영속 저장 완료 (profile: {_PROFILE_DIR})")
    return True


if __name__ == "__main__":
    ok = collect()
    sys.exit(0 if ok else 1)
