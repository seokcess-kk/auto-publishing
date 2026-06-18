"""네이버 서치어드바이저 수동 로그인 — Enter 불필요 자동감지 버전.

searchadvisor 세션이 만료되면 색인 파이프라인(indexing_pipeline)이 막힌다.
코드의 자동 ID/PW 로그인은 네이버 캡차/2FA 로 자주 막히므로, 이 헬퍼는
headful persistent-context 브라우저를 띄워 사용자가 직접 네이버 로그인을
하면 새 NID_AUT 발급을 감지해 자동 저장 후 종료한다 (Enter 불필요).

사용:
    python tools/naver_login_auto.py [--timeout 420]

브라우저가 열리면 네이버 로그인(아이디/비번 + 캡차/2FA)만 하면 된다.
서치어드바이저 콘솔에 도달하면 세션이 영속 프로필에 저장된다.
"""
import argparse
import os
import sys
import time
from pathlib import Path

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _BASE_DIR)

_PROFILE_DIR = Path(_BASE_DIR) / ".sessions" / "naver_searchadvisor_profile"
_CONSOLE = "https://searchadvisor.naver.com/console/board"
_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


def _nid_auth(context):
    """현재 NID_AUT 쿠키 값(네이버 로그인 토큰). 없으면 None."""
    try:
        for c in context.cookies():
            if c.get("name") == "NID_AUT":
                return c.get("value")
    except Exception:
        pass
    return None


def _console_ok(ctx) -> bool:
    """별도 탭으로 콘솔 접근 — nid 로그인으로 안 튕기면 세션 유효.

    사용자가 로그인 중인 page[0] 를 건드리지 않도록 새 탭에서 확인 후 닫는다.
    """
    pg = ctx.new_page()
    try:
        pg.goto(_CONSOLE, wait_until="domcontentloaded", timeout=30000)
        time.sleep(2)
        u = pg.url
        return ("nid.naver.com" not in u) and ("login" not in u.lower())
    except Exception:
        return False
    finally:
        try:
            pg.close()
        except Exception:
            pass


def _enable_keep(page) -> None:
    """네이버 로그인 페이지의 '로그인 상태 유지'(#keep) 토글을 ON.

    이 토글이 꺼져 있으면 NID_AUT 가 session-only 로 발급돼 브라우저를 닫을 때
    세션이 사라진다(영속 실패). 사용자가 깜빡해도 되도록 자동으로 켜준다.
    """
    try:
        page.wait_for_selector("#keep", timeout=8000)
        if page.get_attribute("#keep", "aria-checked") != "true":
            page.click("#keep")
            time.sleep(0.3)
        state = page.get_attribute("#keep", "aria-checked")
        print(f"[설정] '로그인 상태 유지' 토글 = {'ON' if state == 'true' else state}", flush=True)
    except Exception as e:
        print(f"[설정] 토글 자동활성 실패 — 직접 켜주세요: {e}", flush=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--timeout", type=int, default=420, help="로그인 대기 제한시간(초)")
    args = ap.parse_args()

    from playwright.sync_api import sync_playwright
    _PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    print(f"profile: {_PROFILE_DIR}", flush=True)

    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            user_data_dir=str(_PROFILE_DIR),
            headless=False,
            user_agent=_UA,
            locale="ko-KR",
            args=[
                "--no-sandbox",
                "--disable-blink-features=AutomationControlled",
                "--disable-infobars",
            ],
        )
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        init_nid = _nid_auth(ctx)  # 만료된 기존 토큰 — 이 값이 '바뀌면' 새 로그인
        print(">>> 브라우저가 열렸습니다. 네이버로 로그인하세요 "
              "(아이디/비번 + 캡차/2FA, Enter 불필요) <<<", flush=True)
        try:
            # 네이버 로그인 폼으로 직접 이동 — #keep 토글이 즉시 존재해 자동 ON 가능.
            # url 파라미터로 로그인 후 서치어드바이저로 복귀시킨다.
            page.goto(
                "https://nid.naver.com/nidlogin.login?url=https%3A%2F%2Fsearchadvisor.naver.com%2F",
                wait_until="domcontentloaded", timeout=20000)
        except Exception as e:
            print(f"goto 예외(무시): {e}", flush=True)
        # '로그인 상태 유지' 자동 ON (영속 쿠키 보장)
        _enable_keep(page)

        deadline = time.time() + args.timeout
        saved = False
        last_beat = 0.0
        nid_seen = False
        while time.time() < deadline:
            cur = _nid_auth(ctx)
            now = time.time()
            # 로그인 페이지(재로드/캡차 후 포함)면 '상태 유지' 토글을 계속 ON 으로 유지
            try:
                if "nid.naver.com" in (page.url or "") and \
                        page.get_attribute("#keep", "aria-checked") == "false":
                    page.click("#keep")
            except Exception:
                pass
            if cur and not nid_seen:
                nid_seen = True
                print("[감지] NID_AUT 발급됨 (로그인 진행됨) — 콘솔 접근 확인 중...", flush=True)
            if cur and cur != init_nid:
                # 새 NID_AUT 발급 = 로그인 성공 → 별도 탭으로 콘솔 접근 확정
                if _console_ok(ctx):
                    print("로그인 확인 — 안정화 5초 후 저장/종료", flush=True)
                    time.sleep(5)
                    saved = True
                    break
            if now - last_beat >= 30:
                print(f"[대기] NID_AUT={'있음' if cur else '없음'} | 남은시간 {int(deadline - now)}s",
                      flush=True)
                last_beat = now
            time.sleep(3)

        try:
            ctx.close()
        except Exception:
            pass

    print("RESULT: " + ("SUCCESS — 세션이 영속 프로필에 저장됐습니다"
                        if saved else "TIMEOUT — 제한시간 내 로그인이 완료되지 않았습니다"),
          flush=True)
    return 0 if saved else 1


if __name__ == "__main__":
    sys.exit(main())
