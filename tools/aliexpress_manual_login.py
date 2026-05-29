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

# Windows 콘솔(cp949)은 '✓' 등 유니코드 출력에서 UnicodeEncodeError 로 죽는다.
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

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

        # 진짜 로그인 판정 — 쿠키 이름(xman_t 등)은 비로그인 방문에도 발급되어
        # 신뢰할 수 없다. portals 제휴 API 가 JSON 을 돌려주는지로 확정한다.
        # 이건 실제 발행 시 sources/aliexpress.py._shorten_link 가 쓰는 것과
        # 동일한 신호 — 이게 통과하면 발행도 된다.
        import time as _t

        track_id = os.getenv("ALIEXPRESS_TRACKING_ID", "wordpress")

        def _portals_logged_in() -> bool:
            url = ("https://portals.aliexpress.com/tools/linkGenerate/"
                   "generatePromotionLink.htm"
                   f"?trackId={track_id}"
                   "&targetUrl=https%3A%2F%2Fwww.aliexpress.com")
            try:
                res = context.request.get(url, headers={
                    "accept": "application/json, text/plain, */*",
                    "referer": ("https://portals.aliexpress.com/"
                                "affiportals/web/link_generator.htm"),
                    "user-agent": _USER_AGENT,
                }, timeout=15000)
                if not res.ok:
                    return False
                # 로그인 안 됐으면 JSON 대신 로그인 HTML 페이지가 온다.
                return res.text().strip().startswith("{")
            except Exception:
                return False

        print()
        print(">>> 브라우저에서 로그인을 끝까지 완료하세요 (제휴 계정으로!) <<<")
        print("    로그인이 자동 감지되면 저장하고 종료합니다 (Enter 불필요).")

        # Enter 입력은 별도 스레드에서 받고, 본 스레드는 portals 접근 폴링.
        # 단, 백그라운드 실행(stdin 이 tty 가 아님)에서는 input() 이 즉시 EOF 로
        # 반환돼 'Enter 눌림'이 무한 오발동하므로 tty 일 때만 Enter 스레드를 띄운다.
        import threading
        enter_pressed = threading.Event()
        _stdin_tty = False
        try:
            _stdin_tty = sys.stdin is not None and sys.stdin.isatty()
        except Exception:
            _stdin_tty = False

        def _wait_enter():
            try:
                input()
            except Exception:
                return  # stdin 없음 — enter_pressed 설정하지 않음
            enter_pressed.set()

        if _stdin_tty:
            print("    (수동 저장: 로그인 후 Enter)")
            threading.Thread(target=_wait_enter, daemon=True).start()
        else:
            print("    (백그라운드 실행 — 자동 감지만 사용)")

        logged_in = False
        last_status = ""
        deadline = _t.time() + 600  # 최대 10분 대기
        while _t.time() < deadline:
            logged_in = _portals_logged_in()
            status = ("✓ 로그인 확인됨 (portals 제휴 접근 성공) — 저장합니다"
                      if logged_in else
                      "… 아직 제휴 로그인 전 — 브라우저에서 제휴 계정으로 로그인을 완료하세요")
            if status != last_status:
                print(status, flush=True)
                last_status = status

            if logged_in:
                break  # 자동 저장

            if enter_pressed.is_set():
                # 사용자가 '로그인 다 됐다'고 Enter — portals 로 재확인.
                if _portals_logged_in():
                    logged_in = True
                    print("✓ Enter — 로그인 확인됨, 저장합니다", flush=True)
                    break
                print("✗ Enter 눌렀지만 portals 제휴 접근이 안 됩니다 — 로그인이 "
                      "아직 완료되지 않았습니다. 로그인 후 다시 Enter.", flush=True)
                enter_pressed.clear()
                if _stdin_tty:
                    threading.Thread(target=_wait_enter, daemon=True).start()

            _t.sleep(3)

        if not logged_in:
            print("제휴 로그인 확인 실패 (시간 초과 또는 미완료) — 저장하지 않습니다.", flush=True)
            print("브라우저에서 제휴(Partners) 계정으로 로그인한 뒤 다시 실행하세요.", flush=True)
            context.close()
            return False

        context.storage_state(path=str(_STORAGE_PATH))
        context.close()

    print(f"세션 저장 완료: {_STORAGE_PATH}", flush=True)
    print("portals 제휴 접근 확인됨 — 진짜 로그인 세션입니다.", flush=True)
    return True


if __name__ == "__main__":
    ok = collect_and_save_state()
    sys.exit(0 if ok else 1)
