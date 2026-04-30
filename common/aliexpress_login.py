"""
알리익스프레스 자동 로그인 (Playwright)

첫 실행에서 캡차/2FA가 뜨면 사용자가 수동으로 해결해야 한다.
로그인 후 쿠키를 지정된 경로에 pickle 로 저장한다.

환경변수:
    ALIEXPRESS_EMAIL       로그인 이메일
    ALIEXPRESS_PASSWORD    비밀번호
    ALIEXPRESS_HEADLESS    "true" 면 headless 모드 (기본 "false" — 캡차 대응)
    ALIEXPRESS_LOGIN_WAIT  로그인 수동 대기 시간(초, 기본 180)
"""
import os
import pickle
import time

from common.logger import log


LOGIN_URL     = "https://login.aliexpress.com/"
PORTALS_URL   = "https://portals.aliexpress.com/affiportals/web/link_generator.htm"
LOGGED_IN_URL_HINT = "aliexpress.com"


def _check_login_in_background(context) -> bool:
    """별도 페이지(탭)에서 portals 접근 시도. 사용자 입력 페이지 건드리지 않음.

    로그인 페이지로 리다이렉트되지 않으면 로그인 성공으로 판단.
    """
    bg_page = None
    try:
        bg_page = context.new_page()
        bg_page.goto(PORTALS_URL, timeout=15000, wait_until="domcontentloaded")
        time.sleep(1)
        is_logged = "login" not in bg_page.url.lower()
        bg_page.close()
        return is_logged
    except Exception:
        try:
            if bg_page:
                bg_page.close()
        except Exception:
            pass
        return False


def login_and_save_cookies(cookie_path: str) -> bool:
    """Playwright 로 로그인 후 쿠키 저장. 성공 시 True."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        log("playwright 미설치: pip install playwright && playwright install chromium", "error")
        return False

    email    = os.getenv("ALIEXPRESS_EMAIL", "").strip()
    password = os.getenv("ALIEXPRESS_PASSWORD", "").strip()
    headless = os.getenv("ALIEXPRESS_HEADLESS", "false").lower() == "true"
    wait_sec = int(os.getenv("ALIEXPRESS_LOGIN_WAIT", "180"))

    if not email or not password:
        log("ALIEXPRESS_EMAIL / ALIEXPRESS_PASSWORD 환경변수 필요", "error")
        return False

    os.makedirs(os.path.dirname(cookie_path), exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless, args=["--disable-blink-features=AutomationControlled"])
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            locale="ko-KR",
        )
        page = context.new_page()

        try:
            log(f"알리 로그인 페이지 이동: {email}", "info")
            page.goto(LOGIN_URL, timeout=30000, wait_until="domcontentloaded")
            time.sleep(3)

            # 1) 진입 페이지 분기 — 약관 화면 vs 일반 로그인 폼.
            #    알리는 첫 로그인 시 곧장 ug-login-page (title='이용 약관') 로 보내며,
            #    약관 동의만 하면 알리 측에 매핑된 SSO(카카오 등) 로 자동 redirect 한다.
            #    카카오 버튼은 약관 화면에 존재하지 않음 — 클릭 시도는 무의미.
            #    약관 모달이 늦게 렌더되는 경우가 있어 동의 버튼/체크박스 등장까지 폴링.
            is_terms_screen = False
            poll_deadline = time.time() + 8
            while time.time() < poll_deadline:
                try:
                    found = page.evaluate("""
                        () => {
                            const labels = ['동의 및 계속', '동의 및 시작'];
                            const hasAgree = Array.from(document.querySelectorAll('*'))
                                .some(el => el.children.length === 0
                                           && labels.includes((el.textContent || '').trim()));
                            const hasNfmCheck = document.querySelectorAll('div.nfm-checkbox').length > 0;
                            const isUgLogin = location.href.includes('ug-login-page');
                            return hasAgree || hasNfmCheck || isUgLogin;
                        }
                    """)
                    if found:
                        is_terms_screen = True
                        break
                except Exception:
                    pass
                time.sleep(0.5)

            if is_terms_screen:
                log("이용약관 화면 감지 — 카카오 버튼 단계 건너뜀", "info")
            else:
                # 일반 로그인 폼 — 카카오 버튼 클릭
                try:
                    page.click('button[aria-label="kakao"]', timeout=5000)
                    log("카카오 로그인 버튼 클릭", "ok")
                except Exception as e:
                    log(f"카카오 버튼 클릭 실패: {e}", "error")
                    browser.close()
                    return False
                time.sleep(3)

            # 2) 이용약관 동의 화면 처리 (첫 로그인 시 또는 약관 변경 시 노출).
            #    동의 버튼이 visible 해질 때까지 최대 10초 폴링 후 클릭.
            try:
                # 체크박스 등장까지 폴링
                cb_deadline = time.time() + 10
                cb_count = 0
                while time.time() < cb_deadline:
                    cb_count = page.evaluate(
                        "() => document.querySelectorAll('div.nfm-checkbox').length"
                    )
                    if cb_count > 0:
                        break
                    time.sleep(0.5)
                # 체크박스 모두 클릭
                page.evaluate("""
                    () => {
                        document.querySelectorAll('div.nfm-checkbox').forEach(c => c.click());
                    }
                """)
                time.sleep(1)
                # 동의 버튼 등장 + 활성화까지 폴링
                btn_deadline = time.time() + 10
                clicked_agree = None
                while time.time() < btn_deadline:
                    clicked_agree = page.evaluate("""
                        () => {
                            const labels = ['동의 및 계속', '동의 및 시작'];
                            const all = Array.from(document.querySelectorAll('*'));
                            for (const lbl of labels) {
                                const target = all.find(el => el.textContent?.trim() === lbl
                                                             && el.children.length === 0
                                                             && el.offsetParent !== null);
                                if (target) {
                                    let cur = target;
                                    for (let i = 0; i < 5 && cur; i++) {
                                        cur.click();
                                        cur = cur.parentElement;
                                    }
                                    return lbl;
                                }
                            }
                            return null;
                        }
                    """)
                    if clicked_agree:
                        break
                    time.sleep(0.5)
                if clicked_agree:
                    log(f"이용약관 동의 클릭: '{clicked_agree}' (체크박스 {cb_count}개)", "ok")
                else:
                    # 진단 — DOM 상태 dump + 스크린샷
                    try:
                        body_text = page.evaluate(
                            "() => (document.body.innerText || '').slice(0, 300)"
                        )
                        log(f"이용약관 동의 버튼 미발견 (체크박스 {cb_count}개)", "warn")
                        log(f"  body 일부: {body_text!r}", "info")
                        import os as _os
                        ss = _os.path.join(_os.path.dirname(cookie_path), "..", "screenshots", "aliexpress",
                                           f"login_terms_blank_{int(time.time())}.png")
                        _os.makedirs(_os.path.dirname(ss), exist_ok=True)
                        page.screenshot(path=ss, full_page=True)
                        log(f"  진단 스크린샷: {ss}", "info")
                    except Exception:
                        pass
            except Exception as e:
                log(f"약관 처리 예외: {e}", "warn")

            # 3) 카카오 로그인 페이지 이동 폴링 (최대 15초).
            #    약관 동의 후 thirdparty.aliexpress.com → accounts.kakao.com 으로
            #    redirect 하는 데 보통 5~7초 소요. 고정 sleep 대신 폴링으로 안정화.
            kakao_page = page
            deadline = time.time() + 30
            last_logged_urls: set = set()
            while time.time() < deadline:
                found = None
                for pg in context.pages:
                    try:
                        u = pg.url
                    except Exception:
                        continue
                    # 진단 — URL 변화를 한 번씩 로그
                    if u and u not in last_logged_urls:
                        last_logged_urls.add(u)
                        log(f"  redirect → {u[:100]}", "info")
                    if "kauth.kakao.com" in u or "accounts.kakao.com" in u:
                        found = pg
                        break
                if found is not None:
                    kakao_page = found
                    log(f"카카오 로그인 페이지 감지: {kakao_page.url[:80]}", "ok")
                    break
                time.sleep(0.5)

            # 현재 URL 검증 — 카카오로 이동 안 했으면 알리 페이지에 자격증명을
            # 평문 입력하는 사고를 막기 위해 즉시 종료.
            if "kakao" not in kakao_page.url.lower():
                log(f"카카오 페이지 이동 실패 — 현재 URL: {kakao_page.url}", "error")
                log("자동 로그인 중단 — ALIEXPRESS_HEADLESS=false 로 수동 로그인 후 storage_state.json 저장 필요", "warn")
                # 사용자에게 수동 로그인 안내 (24h throttle)
                try:
                    from common.notifier import notify_login_required
                    notify_login_required(
                        "알리익스프레스",
                        "ALIEXPRESS_HEADLESS=false .venv/bin/python -m common.aliexpress_login",
                    )
                except Exception:
                    pass
                browser.close()
                return False

            # 4) 카카오 ID/PW 입력 (실측 셀렉터: input[name="loginId"], input[name="password"])
            filled_id = False
            try:
                kakao_page.fill('input[name="loginId"]', email, timeout=10000)
                filled_id = True
                log("카카오 ID 입력 완료", "ok")
            except Exception as e:
                log(f"카카오 ID 입력 실패: {e}", "error")

            filled_pw = False
            try:
                kakao_page.fill('input[name="password"]', password, timeout=5000)
                filled_pw = True
                log("카카오 비밀번호 입력 완료", "ok")
            except Exception as e:
                log(f"카카오 비밀번호 입력 실패: {e}", "error")

            # 5) 로그인 버튼 클릭 (button.btn_g.highlight.submit)
            if filled_id and filled_pw:
                try:
                    kakao_page.click('button.btn_g.highlight.submit', timeout=5000)
                    log("카카오 로그인 버튼 클릭", "ok")
                except Exception:
                    try:
                        kakao_page.click('button[type="submit"]:has-text("로그인")', timeout=3000)
                        log("카카오 로그인 버튼 클릭 (fallback)", "ok")
                    except Exception as e:
                        log(f"카카오 로그인 버튼 클릭 실패: {e}", "error")
            else:
                log("ID/PW 입력 불완전 — 사용자가 직접 입력하세요", "warn")

            # 5) 2FA / 동의 / 캡차 대기 — 별도 탭에서 30초마다 포털 접근 시도
            log(f"{wait_sec}초 내에 로그인을 완료하세요 (2FA/동의 포함)", "warn")
            log("※ 로그인 탭은 덮어쓰지 않음 — 별도 탭에서 포털 접근으로 확인합니다", "info")
            deadline = time.time() + wait_sec
            logged_in = False
            while time.time() < deadline:
                time.sleep(30)
                if _check_login_in_background(context):
                    log("포털 접근 성공 — 로그인 확정", "ok")
                    logged_in = True
                    break
                remaining = int(deadline - time.time())
                if remaining > 0:
                    log(f"아직 로그인 미완료 — 잔여 {remaining}초", "info")

            if not logged_in:
                log("로그인 대기 시간 초과", "error")
                browser.close()
                return False

            # 쿠키 수집 후 pickle 저장 (requests.cookies 호환 dict)
            raw_cookies = context.cookies()
            cookie_dict = {c["name"]: c["value"] for c in raw_cookies}

            with open(cookie_path, "wb") as f:
                pickle.dump(cookie_dict, f)
            log(f"알리 쿠키 저장 완료: {cookie_path} ({len(cookie_dict)}개)", "ok")

            # Playwright storage_state 저장 (브라우저 세션 재사용용 — localStorage 포함)
            storage_path = os.path.join(os.path.dirname(cookie_path), "aliexpress_storage.json")
            context.storage_state(path=storage_path)
            log(f"알리 Playwright 스토리지 저장: {storage_path}", "ok")

            browser.close()
            return True

        except Exception as e:
            log(f"알리 로그인 오류: {e}", "error")
            try:
                browser.close()
            except Exception:
                pass
            return False


if __name__ == "__main__":
    # 수동 실행: python -m common.aliexpress_login
    from dotenv import load_dotenv
    load_dotenv()

    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    default_path = os.path.join(base_dir, "data", "aliexpress_cookies.pkl")
    ok = login_and_save_cookies(default_path)
    print("OK" if ok else "FAIL")
