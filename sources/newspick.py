"""
뉴스픽 파트너스 콘텐츠 수집 모듈
- partners.newspic.kr API 기반
- 세션 유지: PersistentBrowserProfile (.sessions/newspick_profile/) — Chromium user_data_dir
- 로그인: Kakao SSO (NEWSPICK_ID/NEWSPICK_PW)
- 카테고리별 콘텐츠(추천+일반) 수집
- 파트너 단축링크(bltly.link) 생성

참조: 00.Old_Source/newspic/뉴스픽(requests)_링크생성기_배포용_ver6.py
"""
import os
import random
import time
from urllib.parse import quote

from dotenv import load_dotenv
load_dotenv()

from common.browser_profile import PersistentBrowserProfile
from common.logger import log
from common.session import SessionManager

# ─── 설정 ────────────────────────────────────────────────────────────────────

PARTNERS_BASE = "https://partners.newspic.kr"
CONTENT_URL = f"{PARTNERS_BASE}/main/contentList"
SHORTEN_URL = f"{PARTNERS_BASE}/management/share/getShortUrl"
LOGIN_URL = f"{PARTNERS_BASE}/login"

PROFILE_NAME = "newspick"  # PersistentBrowserProfile 식별자 → .sessions/newspick_profile/

NEWSPICK_CATEGORIES = {
    "메인":       "1",
    "유머이슈":   "89",
    "스토리":     "87",
    "만화":       "92",
    "연예가화제": "36",
    "정치":       "31",
    "경제":       "14",
    "사회":       "32",
    "사건사고":   "12",
    "TV연예":     "51",
    "영화":       "53",
    "K-뮤직":     "57",
    "스포츠":     "7",
    "축구":       "15",
    "야구":       "16",
    "반려동물":   "3",
    "생활픽":     "33",
    "해외연예":   "58",
    "BBC News":   "11",
    "NNA코리아":  "38",
    "글로벌":     "39",
}


# 카테고리 회전 기본 풀 — 수익(상품매칭) 가능 + 참여도 높은 소프트 카테고리 위주.
# 하드뉴스(정치/사회/사건사고)는 상품카드가 skip 되고 민감하므로 기본 제외.
_DEFAULT_ROTATE_CATEGORIES = (
    "연예가화제,TV연예,K-뮤직,생활픽,스포츠,반려동물,경제,유머이슈,글로벌"
)


def resolve_category(category: str) -> str:
    """발행 카테고리 결정 — '추천'/빈값이면 회전 풀에서 랜덤 1개를 고른다.

    기존엔 '추천'이 NEWSPICK_CATEGORIES 에 없어 메인(1)으로만 폴백 → 매 발행이 같은
    풀이었다. 이제 '추천'(또는 빈값/'rotate')이면 NEWSPICK_CATEGORIES_ROTATE
    (쉼표구분 카테고리명) 중 하나를 랜덤 선택해 소스를 다변화한다. 명시적 카테고리
    (예: '경제')는 그대로 고정. NEWSPICK_CATEGORY_ROTATE=false 면 기존 메인 동작.
    """
    cat = (category or "").strip()
    if cat not in ("", "추천", "rotate"):
        return category  # 명시적 카테고리 고정
    if os.getenv("NEWSPICK_CATEGORY_ROTATE", "true").strip().lower() != "true":
        return "메인"
    pool = [c.strip() for c in
            os.getenv("NEWSPICK_CATEGORIES_ROTATE", _DEFAULT_ROTATE_CATEGORIES).split(",")
            if c.strip() and c.strip() in NEWSPICK_CATEGORIES]
    if not pool:
        return "메인"
    return random.choice(pool)


class NewspickSource:
    """뉴스픽 파트너스에서 콘텐츠를 수집하고 단축 링크를 생성하는 클래스."""

    def __init__(self, referral_code: str = ""):
        self.session_mgr = SessionManager("newspick")
        self.referral_code = referral_code or os.getenv("NEWSPICK_REFERRAL", "")
        self.profile = PersistentBrowserProfile(PROFILE_NAME)

    def _check_session(self) -> bool:
        """requests.Session 에 주입된 쿠키가 partners API 호출에 유효한지 확인."""
        try:
            resp = self.session_mgr.post(
                f"{CONTENT_URL}?channelNo=1&inputSwitch=false"
                f"&adultContentCheck=N&totalRow=0&pageSize=1",
                timeout=10,
            )
            if resp.status_code == 200:
                ct = resp.headers.get("content-type", "")
                if "json" in ct:
                    data = resp.json()
                    return "recomList" in data or "contentList" in data
        except Exception:
            pass
        return False

    def _inject_cookies(self, context) -> int:
        """BrowserContext 의 newspic.kr 쿠키를 session_mgr 에 옮겨 담는다.

        SESSION 쿠키는 HttpOnly 라 context.cookies(urls=...) 호출이 필요하다.
        """
        self.session_mgr.session.cookies.clear()
        cookies = context.cookies([
            "https://partners.newspic.kr",
            "https://www.newspic.kr",
        ])
        injected = 0
        for c in cookies:
            if "newspic.kr" not in c.get("domain", ""):
                continue
            self.session_mgr.session.cookies.set(
                c["name"], c["value"],
                domain=c.get("domain", "").lstrip("."),
                path=c.get("path", "/"),
            )
            injected += 1
        return injected

    def _page_has_session(self, context, page) -> bool:
        """로그인 완료 페이지에 도달했는지(SESSION 쿠키 + 비-login URL).

        partners 가 anonymous 사용자에게도 SESSION 을 발급하므로 SESSION 쿠키
        하나만으로는 인증 여부 판단이 불가하다. URL 이 /login 을 벗어난 상태
        에서 SESSION 도 있으면 진짜 인증된 것.
        """
        try:
            if "/login" in page.url:
                return False
        except Exception:
            return False
        cookies = context.cookies(["https://partners.newspic.kr"])
        return any(c["name"] == "SESSION" for c in cookies)

    def _click_saved_account(self, kakao_page, email: str) -> bool:
        """간편로그인 (`accounts.kakao.com/login/simple`) 화면에서 저장된 계정 클릭.

        DOM 구조:
            <ul class="kc_lst_select">
              <li class="kc_item_select">
                <button class="kc_btn_simple" ...>     ← 클릭 타겟
                <button class="btn_delete">X</button>  ← 절대 클릭 금지
              </li>
            </ul>

        이메일 텍스트 단순 검색은 X 버튼에 매칭될 위험이 있어
        Kakao 전용 클래스(`kc_btn_simple` / `kc_item_select`)를 우선 사용한다.

        클릭 후 navigation 발생을 검증해 false positive (UI는 클릭됐는데 로그인
        동작 없음)를 걸러낸다. tistory.py 의 동명 메서드와 동일 전략.
        """
        # 1) Kakao 전용 클래스 — 가장 신뢰
        kakao_selectors = [
            ".kc_item_select .kc_btn_simple",
            "button.kc_btn_simple",
            ".kc_item_select",
            "[data-testid*='account']",
        ]
        # 2) 텍스트 기반 폴백 — btn_delete (X) 는 제외
        text_selectors = [
            f'a:has-text("{email}"):not(.btn_delete)',
            f'button:has-text("{email}"):not(.btn_delete)',
            f'li:has-text("{email}")',
        ]

        attempted = False
        for sel in kakao_selectors + text_selectors:
            try:
                loc = kakao_page.locator(sel).first
                if loc.count() == 0:
                    continue
                cur_url = kakao_page.url
                try:
                    with kakao_page.expect_navigation(timeout=8000, wait_until="domcontentloaded"):
                        loc.click(timeout=2000)
                    log(f"  간편로그인 계정 클릭 ({sel})", "ok")
                    return True
                except Exception:
                    # navigation 실패 시 URL 변화로 성공 여부 재확인
                    try:
                        new_url = kakao_page.url
                    except Exception:
                        new_url = cur_url
                    if new_url != cur_url:
                        log(f"  간편로그인 계정 클릭 ({sel}) — URL 변화 감지", "ok")
                        return True
                    attempted = True
                    log(f"  간편로그인 클릭 ({sel}) 후 URL 변화 없음 — 다른 셀렉터 시도", "warn")
                    continue
            except Exception:
                continue

        # 3) JS evaluate 최종 폴백 — 이메일 텍스트 포함 + btn_delete 제외
        try:
            ok = kakao_page.evaluate(
                """(target) => {
                    const candidates = Array.from(document.querySelectorAll(
                        '.kc_item_select, .kc_btn_simple, button:not(.btn_delete), a, li'
                    ));
                    const cand = candidates.find(el => {
                        if (!el.offsetParent) return false;
                        if (el.classList.contains('btn_delete')) return false;
                        const txt = (el.innerText || '').trim();
                        return txt.includes(target);
                    });
                    if (cand) { cand.click(); return true; }
                    return false;
                }""",
                email,
            )
            if ok:
                log(f"  간편로그인 계정 클릭 (JS fallback, {email})", "ok")
                return True
        except Exception:
            pass

        if attempted:
            log("  간편로그인 모든 셀렉터 클릭 후에도 navigation 미발생", "warn")
        return False

    def _kakao_login(self, context) -> bool:
        """Kakao SSO 로그인 — Kakao 버튼 클릭으로 뜨는 popup 창에 자동 입력.

        뉴스픽은 accounts.kakao.com/login 팝업을 새 window 로 띄운다.
        따라서 page.click() 후 원래 page.url 을 봐도 Kakao 로 이동하지 않고,
        popup 이 뜨는 것을 context.expect_page() 로 받아 거기서 입력해야 한다.

        popup 진입 후 3가지 상태로 분기:
          (A) /oauth/authorize    → '계속하기' 클릭만 (이미 인증됨)
          (B) /login/simple       → 저장된 계정 클릭 (_click_saved_account)
          (C) 일반 로그인 폼       → ID/PW 자동 입력
        """
        email = os.getenv("NEWSPICK_ID", "")
        password = os.getenv("NEWSPICK_PW", "")
        # ID/PW 미설정이라도 영속 프로필에 카카오 간편로그인 토큰이 살아 있으면
        # popup 이 곧바로 /oauth/authorize (이미 인증됨, 동의 단계) 로 떨어져
        # '계속하기' 클릭만으로 로그인이 완료된다. 그래서 ID/PW 부재로 즉시
        # 종료하지 않고 popup 진입까지 진행한다.
        creds_available = bool(email and password)
        if not creds_available:
            log("NEWSPICK_ID/NEWSPICK_PW 미설정 — 영속 프로필 의존 모드로 시도", "info")

        id_selectors = [
            'input[name="loginKey"]',
            'input[name="email"]',
            'input#loginId--1',
            'input#loginId',
            'input[placeholder*="이메일"]',
            'input[placeholder*="아이디"]',
            'input[placeholder*="카카오메일"]',
            'input[type="text"]',
        ]
        pw_selectors = [
            'input[name="password"]',
            'input#password--2',
            'input#password',
            'input[type="password"]',
        ]

        try:
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=30000)
            time.sleep(1)

            # popup 을 기다리며 Kakao 버튼 클릭
            try:
                with context.expect_page(timeout=10000) as popup_info:
                    page.click('button[data-role="kakaoSignin"]', timeout=5000)
                kakao_page = popup_info.value
            except Exception as e:
                log(f"Kakao popup 감지 실패: {e}", "warn")
                return False

            try:
                kakao_page.wait_for_load_state("domcontentloaded", timeout=15000)
            except Exception:
                pass
            log(f"  Kakao popup URL → {kakao_page.url}", "info")

            # Kakao popup 진입 상태 분기:
            #  (A) /oauth/authorize       이미 인증됨 — '계속하기'만 처리
            #  (B) /login/simple          간편로그인 — 저장된 계정 클릭
            #  (C) 일반 로그인 폼          ID/PW 자동 입력
            cur_url = kakao_page.url
            # /oauth/authorize, /oauth/code/confirm 모두 '이미 인증됨, 동의/계속'
            # 단계로 ID/PW 입력이 필요 없는 경로다.
            if "/oauth/authorize" in cur_url or "/oauth/code/confirm" in cur_url:
                log("  이미 Kakao 로그인됨 — oauth 동의 단계로 바로 이동", "info")
                on_login_form = False
            elif "/login/simple" in cur_url or "simpleLogin" in cur_url:
                if not creds_available:
                    log("  간편로그인 화면이지만 NEWSPICK_ID 미설정 — 자동 클릭 불가", "error")
                    log("  partners.newspic.kr 에서 ID/PW 로그인 후 영속 프로필 의존 모드로 재시도하세요", "info")
                    return False
                log("  간편로그인 화면 감지 — 저장된 계정 클릭", "info")
                if not self._click_saved_account(kakao_page, email):
                    log("  간편로그인 클릭 실패 — Kakao SSO 중단", "error")
                    return False
                on_login_form = False
            else:
                on_login_form = True

            if on_login_form and not creds_available:
                log(f"  일반 로그인 폼 진입했으나 NEWSPICK_ID/PW 미설정 — 자동 입력 불가 (URL: {kakao_page.url})", "error")
                return False

            if on_login_form:
                # ID 입력
                id_el = None
                for sel in id_selectors:
                    try:
                        kakao_page.wait_for_selector(sel, timeout=3000, state="visible")
                        id_el = sel
                        break
                    except Exception:
                        continue
                if not id_el:
                    log(f"Kakao popup 에서 ID 필드 감지 실패 (URL: {kakao_page.url})", "warn")
                    return False
                kakao_page.fill(id_el, email)
                time.sleep(0.3)

                # PW 입력
                pw_filled = False
                for sel in pw_selectors:
                    try:
                        kakao_page.fill(sel, password)
                        pw_filled = True
                        break
                    except Exception:
                        continue
                if not pw_filled:
                    log("Kakao popup 에서 PW 필드 감지 실패", "warn")
                    return False

                # "간편로그인 정보 저장" 체크 — persistent profile 에 신뢰 기기 토큰을 남긴다
                # Kakao 2026+ accounts.kakao.com 신/구 셀렉터 모두 시도
                for sel in [
                    # 현행 (2026+)
                    'label[for="saveSignedIn--4"]',
                    'label[for="saveSignedIn"]',
                    'input[name="saveSignedIn"]',
                    'input#saveSignedIn--4',
                    # 구 버전 호환
                    'label[for="staySignedIn"]',
                    'label:has-text("간편로그인")',
                    'input[name="staySignedIn"]',
                    'input#staySignedIn',
                    'input[type="checkbox"]',
                ]:
                    try:
                        el = kakao_page.locator(sel).first
                        if el.count() > 0:
                            try:
                                el.check(timeout=1500)
                            except Exception:
                                el.click(timeout=1500, force=True)
                            log("  '간편로그인 정보 저장' 체크", "ok")
                            break
                    except Exception:
                        continue

                time.sleep(0.3)

                # 로그인 버튼
                clicked = False
                for sel in [
                    'button.btn_g.highlight.submit',
                    'button[type="submit"]',
                    'button.submit',
                    'button:has-text("로그인")',
                    '.submit',
                ]:
                    try:
                        kakao_page.click(sel, timeout=3000)
                        clicked = True
                        break
                    except Exception:
                        continue
                if not clicked:
                    kakao_page.keyboard.press("Enter")

                log("Kakao popup 자동 입력 완료 — 로그인 처리 대기", "info")
            else:
                log("  이미 Kakao 로그인됨 — oauth 동의 단계로 바로 이동", "info")

            # popup 이 닫히거나 원래 page 가 partners.newspic.kr 내부로 이동하면 성공.
            # 중간에 oauth/authorize 동의 화면이 뜨면 "계속하기" 버튼을 자동 클릭한다.
            deadline = time.time() + 120
            last_main = ""
            last_popup = ""
            popup_closed = False
            consent_clicked = False
            while time.time() < deadline:
                # popup 이 닫혔는지 + URL 추적
                if not popup_closed:
                    try:
                        p_url = kakao_page.url
                        if p_url != last_popup:
                            log(f"  popup URL → {p_url}", "info")
                            last_popup = p_url
                        # oauth 동의/확인 화면 감지 시 "계속하기" 클릭 (여러 번 재시도)
                        if not consent_clicked and ("oauth/authorize" in p_url or "oauth/code/confirm" in p_url):
                            clicked_now = False
                            # 1) text 기반 role=button 선호 (Kakao 가 자주 바꾸는 클래스에 덜 의존)
                            for sel in [
                                'button:has-text("계속하기")',
                                'a:has-text("계속하기")',
                                '[role="button"]:has-text("계속하기")',
                                'button.btn_agree',
                                'button.submit',
                                'button[type="submit"]',
                            ]:
                                try:
                                    loc = kakao_page.locator(sel).first
                                    if loc.count() == 0:
                                        continue
                                    loc.wait_for(state="visible", timeout=1500)
                                    loc.click(timeout=2000)
                                    clicked_now = True
                                    log(f"  oauth 동의 클릭 ({sel})", "ok")
                                    break
                                except Exception:
                                    continue
                            # 2) JS evaluate fallback
                            if not clicked_now:
                                try:
                                    clicked_now = kakao_page.evaluate("""
                                        () => {
                                            const cand = Array.from(
                                                document.querySelectorAll('button, a, [role=\"button\"]')
                                            ).find(el => (el.innerText||'').trim().includes('계속하기'));
                                            if (cand) { cand.click(); return true; }
                                            return false;
                                        }
                                    """)
                                    if clicked_now:
                                        log("  oauth 동의 클릭 (JS fallback)", "ok")
                                except Exception:
                                    pass
                            if clicked_now:
                                consent_clicked = True
                    except Exception:
                        popup_closed = True
                        log("  popup 닫힘 감지", "info")

                # 메인 페이지 URL 변화 감지
                try:
                    cur = page.url
                except Exception:
                    cur = last_main
                if cur != last_main:
                    log(f"  메인 URL → {cur}", "info")
                    last_main = cur
                if "partners.newspic.kr" in cur and "/login" not in cur:
                    time.sleep(2)
                    return True
                time.sleep(1)

            # 타임아웃 시 스크린샷으로 원인 파악
            try:
                from pathlib import Path
                ss_dir = Path(__file__).parent.parent / "screenshots" / "newspick"
                ss_dir.mkdir(parents=True, exist_ok=True)
                ts = int(time.time())
                page.screenshot(path=str(ss_dir / f"main_{ts}.png"), full_page=True)
                if not popup_closed:
                    try:
                        kakao_page.screenshot(path=str(ss_dir / f"popup_{ts}.png"), full_page=True)
                    except Exception:
                        pass
                log(f"스크린샷 저장: {ss_dir}/{{main,popup}}_{ts}.png", "info")
            except Exception:
                pass

            log(f"로그인 완료 대기 시간 초과 (메인 URL: {page.url})", "warn")
            return False
        except Exception as e:
            log(f"Kakao 로그인 중 예외: {e}", "error")
            return False

    def ensure_session(self, max_attempts: int = 3) -> bool:
        """매 호출마다 persistent profile 로 Chromium 을 짧게 띄워 SESSION 을 재발급받는다.

        뉴스픽 SESSION 쿠키는 브라우저 종료 시 휘발되는 session-only 타입이라
        pickle 재사용이 불가능. 대신 persistent profile 에 저장된 Kakao 간편로그인
        토큰이 있으면 ID/PW 재입력 없이 1~3초 안에 /login → 대시보드로 자동 진입.

        max_attempts: chromium startup race ('Target page closed') 에 한해 재시도.
            3s 백오프. 자동 스케줄 시간대에 launch 직후 page 가 닫혀버리는 패턴 회복용.
        """
        def _auth_failed(reason: str) -> bool:
            """인증 실패 — 로그 + throttled 텔레그램으로 정확한 복구 명령 안내 후 False.

            뉴스픽 SESSION 은 session-only 라 토큰 만료 시 수동 재로그인 외 자동
            복구가 불가능하다. 매 슬롯마다 같은 실패를 반복하므로 notify_login_required
            의 24h throttle 로 하루 1회만 알린다 (스팸 방지).
            """
            log(reason, "error")
            try:
                from common.notifier import notify_login_required
                notify_login_required(
                    "뉴스픽",
                    instructions="python tools/newspick_manual_login.py",
                )
            except Exception:
                pass
            return False

        for attempt in range(1, max_attempts + 1):
            try:
                with self.profile.launch(headless=True) as context:
                    page = context.pages[0] if context.pages else context.new_page()
                    page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=20000)

                    # 간편로그인 토큰으로 자동 리다이렉트 대기 (최대 8초)
                    deadline = time.time() + 8
                    while time.time() < deadline:
                        if self._page_has_session(context, page):
                            break
                        time.sleep(0.5)

                    if not self._page_has_session(context, page):
                        # 토큰이 만료됐거나 최초 로그인 — Kakao SSO 전체 플로우 실행
                        log("간편로그인 미적용 — Kakao SSO 자동 로그인 시도", "warn")
                        if not self._kakao_login(context):
                            return _auth_failed("Kakao SSO 로그인 실패")

                        # 재진입 확인
                        if not self._page_has_session(context, page):
                            return _auth_failed("로그인은 성공했으나 SESSION 쿠키 미확보")

                    # SESSION 확보 완료 → requests 에 쿠키 주입
                    injected = self._inject_cookies(context)
                    log(f"newspic 쿠키 주입 완료: {injected}개", "ok")

                # 브라우저 종료 후 requests 단에서 API 검증
                if self._check_session():
                    self.session_mgr.save()  # 다른 세션에서 참고 가능 (pickle)
                    log("뉴스픽 세션 유효", "ok")
                    return True

                return _auth_failed("requests 쪽 세션 검증 실패")
            except Exception as e:
                msg = str(e)
                transient = ("Target page" in msg or
                             "browser has been closed" in msg)
                if attempt < max_attempts and transient:
                    log(f"ensure_session 시도 {attempt}/{max_attempts} — 3s 후 재시도: "
                        f"{type(e).__name__}: {msg[:120]}", "warn")
                    time.sleep(3)
                    continue
                log(f"ensure_session 예외: {e}", "error")
                return False
        return False

    def fetch(self, category: str = "메인", count: int = 10) -> list:
        """카테고리에서 콘텐츠 목록을 가져온다 (추천 + 일반).

        Returns:
            list of {"title", "url", "image", "category", "nid", "providerNo", "recomType"}
        """
        cat_no = NEWSPICK_CATEGORIES.get(category, "1")
        log(f"뉴스픽 크롤링: 카테고리={category}({cat_no}), count={count}", "step")

        url = (
            f"{CONTENT_URL}?channelNo={cat_no}"
            f"&inputSwitch=false&adultContentCheck=N"
            f"&totalRow=0&pageSize={count}"
        )

        try:
            resp = self.session_mgr.post(url, timeout=10)
            if resp.status_code != 200:
                log(f"API 요청 실패: HTTP {resp.status_code}", "error")
                return []

            data = resp.json()
        except Exception as e:
            log(f"API 요청 오류: {e}", "error")
            return []

        articles = []

        for item in data.get("recomList", []):
            articles.append({
                "title":      item.get("title", ""),
                "url":        item.get("link", ""),
                "image":      item.get("imgUrl", ""),
                "category":   category,
                # 글별 실제 카테고리 코드(CAxxyy) — 본문 맥락에 맞는 상품 매칭용.
                # 요청 카테고리("추천"=혼합)와 달리 글 자체의 주제를 가리킨다.
                "cate_code":  item.get("category", ""),
                "nid":        str(item.get("nid", "")),
                "providerNo": str(item.get("providerNo", "")),
                "recomType":  item.get("recomType", ""),
                "source":     "recom",
            })

        for item in data.get("contentList", []):
            articles.append({
                "title":      item.get("title", ""),
                "url":        item.get("link", ""),
                "image":      item.get("imgUrl", ""),
                "category":   category,
                "cate_code":  item.get("category", ""),   # 글별 실제 카테고리 코드(CAxxyy)
                "nid":        str(item.get("nid", "")),
                "providerNo": str(item.get("providerNo", "")),
                "recomType":  item.get("recomType", ""),
                "source":     "content",
            })

        log(f"수집 완료: 추천 {len(data.get('recomList', []))}건 + "
            f"일반 {len(data.get('contentList', []))}건 = 총 {len(articles)}건", "ok")
        return articles

    def shorten_link(self, article: dict, category: str = "메인") -> str:
        """파트너 단축 링크 생성. 실패 시 빈 문자열 반환."""
        if not self.referral_code:
            log("추천인 코드(referral_code) 없음", "warn")
            return ""

        cat_no = NEWSPICK_CATEGORIES.get(category, "1")
        nid = article.get("nid", "")
        pn = article.get("providerNo", "")
        recom_type = article.get("recomType", "")
        source_type = article.get("source", "content")

        shared_from = "CT-R-L" if source_type == "recom" else "CT-NO-L"

        query_parts = (
            f"nid={nid}&pn={pn}&cp={self.referral_code}"
            f"&utm_medium=affiliate&utm_campaign={nid}"
        )
        if recom_type:
            query_parts += f"&rssOption={recom_type}"
        # utm_source 접두어는 파트너 계정마다 다르다(실제 앱 공유값 np<YYMMDD>).
        # 기본 np220822 는 구 하드코딩 값 — NEWSPICK_UTM_PREFIX 로 본인 값 지정.
        utm_prefix = os.getenv("NEWSPICK_UTM_PREFIX", "np220822")
        query_parts += (
            f"&channelName={quote(category)}&channelNo={cat_no}"
            f"&sharedFrom={shared_from}"
            f"&utm_source={utm_prefix}{self.referral_code}"
        )

        url = f"{SHORTEN_URL}?queryString=%3F{quote(query_parts, safe='')}"

        try:
            resp = self.session_mgr.get(url, timeout=10)
            if resp.status_code == 200 and resp.text.strip():
                return f"https://bltly.link/{resp.text.strip()}"
        except Exception as e:
            log(f"단축링크 생성 실패: {e}", "warn")

        return ""

    def fetch_with_links(self, category: str = "메인",
                         count: int = 10) -> list:
        """fetch() + 각 아티클에 단축 링크 추가."""
        articles = self.fetch(category, count)
        for a in articles:
            a["short_url"] = self.shorten_link(a, category)
            time.sleep(random.uniform(0.3, 0.8))
        return articles
