"""
Naver Search Advisor 1회 수동 로그인 → Playwright persistent context 저장

색인 자동화(indexing_naver.py)는 .sessions/naver_searchadvisor_profile/ 의
세션을 재사용한다. searchadvisor.naver.com 메인 페이지가 비로그인 상태에서도
열리는 탓에 자동 로그인 분기가 우회되는 케이스가 있어, 이 헬퍼로 1회만
직접 로그인해 두면 이후 indexing_pipeline 이 그 세션으로 동작한다.

실행:
    python tools/naver_searchadvisor_login.py

브라우저 창이 뜨면 본인이 직접:
  1) 네이버 로그인 (ID/PW + 캡차/2단계 인증 등 모두 OK)
  2) searchadvisor.naver.com 메인이 정상으로 뜨는지 확인 (사이트 목록 보임)
  3) 터미널에서 Enter — 세션이 저장됨
"""
import sys
from pathlib import Path

_BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BASE_DIR))

from dotenv import load_dotenv
load_dotenv(_BASE_DIR / ".env")


_SESSIONS_DIR = _BASE_DIR / ".sessions" / "naver_searchadvisor_profile"
_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


def main() -> int:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("✗ playwright 미설치 — pip install playwright + playwright install chromium")
        return 1

    _SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    print(f"세션 저장 위치: {_SESSIONS_DIR}\n")

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=str(_SESSIONS_DIR),
            headless=False,
            user_agent=_USER_AGENT,
            args=[
                "--no-sandbox",
                "--disable-blink-features=AutomationControlled",
            ],
        )
        # navigator.webdriver 숨김 — 슬라이더/이미지 캡차 회피
        try:
            context.add_init_script(
                "Object.defineProperty(navigator, 'webdriver', "
                "{get: () => undefined});"
            )
        except Exception:
            pass

        page = context.pages[0] if context.pages else context.new_page()

        print("1) 브라우저 창이 떴으면 네이버에 로그인하세요.")
        print("   (ID/PW + 캡차 / 2단계 인증 모두 OK)")
        print("2) searchadvisor.naver.com 메인에 사이트 목록이 보이면 통과.\n")

        page.goto("https://searchadvisor.naver.com/",
                   wait_until="domcontentloaded", timeout=60000)

        input(">>> 로그인 완료 후 Enter (사이트 목록이 보이는 화면) <<<")

        # 인증 쿠키가 잡혔는지 검증
        cookies = context.cookies(["https://www.naver.com",
                                    "https://searchadvisor.naver.com",
                                    "https://nid.naver.com"])
        cookie_names = {c.get("name", "") for c in cookies}
        auth_markers = {"NID_AUT", "NID_SES", "NID_JKL", "BUC", "page_uid"}
        has_auth = bool(cookie_names & auth_markers)

        if not has_auth:
            print(f"\n✗ 인증 쿠키가 보이지 않습니다 (쿠키 {len(cookie_names)}개): "
                  f"{sorted(cookie_names)[:10]}")
            print("  로그인이 완료되지 않은 것 같습니다 — 다시 시도하세요.")
            context.close()
            return 1

        # persistent context 는 user_data_dir 에 자동 저장 — 명시 호출 불필요
        # 그냥 닫기만 해도 다음 실행 시 세션 재사용됨.
        context.close()

    print(f"\n✓ 세션 저장 완료: {_SESSIONS_DIR}")
    print(f"✓ 감지된 인증 쿠키: {sorted(cookie_names & auth_markers)}")
    print("\n다음 단계:")
    print("  python tools/test_indexing.py https://kkkseok.tistory.com/14")
    return 0


if __name__ == "__main__":
    sys.exit(main())
