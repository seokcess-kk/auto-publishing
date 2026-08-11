"""
Naver Search Advisor 1회 수동 로그인 → Playwright persistent context 저장

색인 자동화(common/indexing_naver.py)는 .sessions/naver_searchadvisor_profile/
의 세션을 재사용한다. 네이버는 자동 로그인(ID/PW 입력)을 캡차·2단계 인증으로
차단하므로, 세션이 끊기면 이 헬퍼로 사람이 1회 로그인해 둬야 한다.

실행 (프로젝트 루트에서):
    python tools/naver_searchadvisor_login.py

브라우저 창이 뜨면 네이버에 로그인만 하면 된다 — 로그인이 자동 감지되면
저장하고 종료한다(Enter 불필요).

로그인 판정에 주의할 점:
  - `https://searchadvisor.naver.com/` 는 **비로그인 상태에서도 정상 렌더**된다
    (로그인 링크가 있는 소개 페이지). 이 URL 로는 판정할 수 없다.
  - NID_AUT / NID_SES / BUC / page_uid 같은 쿠키도 비로그인 방문에 발급되므로
    쿠키 존재로도 판정할 수 없다. 실제로 2026-08 에 이 두 오판 때문에 네이버
    색인이 64건 연속 조용히 실패했다.
  → 로그인이 필요한 콘솔 경로(/console/board)가 nid.naver.com 으로 튕기지
    않는지로만 판정한다. 이건 실제 색인 제출이 쓰는 것과 같은 신호다.
"""
import sys
from pathlib import Path

# Windows 콘솔(cp949)에서 한글/기호 출력이 깨지거나 UnicodeEncodeError 로 죽는다.
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

_BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BASE_DIR))

from dotenv import load_dotenv
load_dotenv(_BASE_DIR / ".env")


_SESSIONS_DIR = _BASE_DIR / ".sessions" / "naver_searchadvisor_profile"
_CONSOLE = "https://searchadvisor.naver.com/console/board"
_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


def main(timeout_sec: int = 600) -> int:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("playwright 미설치 — pip install playwright && playwright install chromium")
        return 1

    import threading
    import time as _t

    _SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    print(f"세션 저장 위치: {_SESSIONS_DIR}\n")

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=str(_SESSIONS_DIR),
            headless=False,
            user_agent=_USER_AGENT,
            args=["--no-sandbox", "--disable-blink-features=AutomationControlled"],
        )
        try:
            context.add_init_script(
                "Object.defineProperty(navigator, 'webdriver', "
                "{get: () => undefined});"
            )
        except Exception:
            pass

        page = context.pages[0] if context.pages else context.new_page()

        def logged_in() -> bool:
            """콘솔 접근이 로그인 페이지로 튕기지 않으면 로그인 상태.

            APIRequestContext 로는 판정할 수 없다 — 서치어드바이저는
            nid.naver.com/oauth2.0/authorize 를 거치는 OAuth 를 쓰는데, 이
            핸드셰이크는 브라우저 내비게이션이 있어야 끝난다. request 로는
            로그인이 끝난 뒤에도 authorize 페이지에서 멈춰 항상 False 가
            나온다(2026-08-11 실측: status 200 이지만 url 은 authorize).

            대신 **별도 탭**에서 이동해 확인한다. 사용자가 로그인 폼을 채우고
            있는 탭은 건드리지 않는다.
            """
            probe = None
            try:
                probe = context.new_page()
                probe.goto(_CONSOLE, wait_until="domcontentloaded", timeout=30000)
                probe.wait_for_timeout(1500)
                url = probe.url
                return "nid.naver.com" not in url and "/login" not in url.lower()
            except Exception:
                return False
            finally:
                if probe is not None:
                    try:
                        probe.close()
                    except Exception:
                        pass

        print(">>> 브라우저에서 네이버 로그인을 완료하세요 <<<")
        print("    (ID/PW + 캡차 / 2단계 인증 모두 OK)")
        print("    로그인이 자동 감지되면 저장하고 종료합니다.\n")

        page.goto("https://nid.naver.com/nidlogin.login",
                  wait_until="domcontentloaded", timeout=60000)

        # Enter 는 tty 일 때만 받는다. 백그라운드/파이프 실행에서 input() 은
        # 즉시 EOFError 로 죽어 도구 자체를 쓸 수 없게 만든다.
        enter_pressed = threading.Event()
        try:
            _tty = sys.stdin is not None and sys.stdin.isatty()
        except Exception:
            _tty = False

        def _wait_enter():
            try:
                input()
            except Exception:
                return
            enter_pressed.set()

        if _tty:
            print("    (수동 저장: 로그인 후 Enter)")
            threading.Thread(target=_wait_enter, daemon=True).start()
        else:
            print("    (비대화형 실행 — 자동 감지만 사용)")

        ok = False
        last = ""
        deadline = _t.time() + timeout_sec
        while _t.time() < deadline:
            ok = logged_in()
            msg = ("로그인 확인됨 (콘솔 접근 성공) — 저장합니다" if ok
                   else "아직 로그인 전 — 브라우저에서 로그인을 완료하세요")
            if msg != last:
                print(msg, flush=True)
                last = msg
            if ok:
                break
            if enter_pressed.is_set():
                if logged_in():
                    ok = True
                    print("Enter — 로그인 확인됨, 저장합니다", flush=True)
                    break
                print("Enter 를 눌렀지만 콘솔 접근이 안 됩니다 — 로그인 미완료. "
                      "로그인 후 다시 Enter.", flush=True)
                enter_pressed.clear()
                if _tty:
                    threading.Thread(target=_wait_enter, daemon=True).start()
            _t.sleep(3)

        if not ok:
            print("\n로그인 확인 실패 (시간 초과 또는 미완료) — 저장하지 않습니다.",
                  flush=True)
            context.close()
            return 1

        # persistent context 는 user_data_dir 에 자동 저장 — close 만 하면 된다.
        context.close()

    print(f"\n세션 저장 완료: {_SESSIONS_DIR}")
    print("콘솔 접근 확인됨 — 진짜 로그인 세션입니다.")
    print("\n다음 단계:")
    print("  python -m pipelines.indexing_pipeline    # 밀린 색인 제출")
    return 0


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--timeout", type=int, default=600,
                    help="로그인 대기 제한시간(초). 기본 600(10분)")
    args = ap.parse_args()
    sys.exit(main(timeout_sec=args.timeout))
