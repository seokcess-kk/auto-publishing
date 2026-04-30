"""
티스토리 자동 발행 Publisher — Playwright persistent context 기반.

이전 버전은 Playwright 로 로그인 후 쿠키를 requests.Session 에 주입해
HTTP 로 발행했으나, 티스토리 봇 탐지(/auth/login 리다이렉트)와 Kakao
OAuth 쿠키의 크로스도메인 바인딩 문제로 세션이 금방 만료됐다.

변경 후 구조:
* .sessions/tistory_<blog>_profile/ 디렉토리를 user_data_dir 로 사용
* Kakao 간편로그인 토큰이 영구 저장되어 수 주간 재로그인 불필요
* 이미지 업로드·카테고리 조회·글 발행 모두 context.request.post() 로 호출
  → 브라우저의 쿠키·User-Agent·TLS fingerprint 그대로 사용해 봇 탐지 회피
* post() 실행 중엔 Playwright 가 열려 있고, close() 호출 시 닫힘

엔드포인트 (manage API, 2024+ 동일):
    GET  {blog_url}/manage                            세션 유효성 확인
    GET  {blog_url}/manage/category.json              카테고리 목록
    POST {blog_url}/manage/post/attach.json           이미지 업로드 (multipart)
    POST {blog_url}/manage/post.json                  글 발행 (JSON)

환경변수:
    TISTORY_BLOG_NAME   블로그 ID (전역 폴백)
    TISTORY_CATEGORY    기본 카테고리
    TISTORY_EMAIL       Kakao 계정 (최초 로그인 자동 입력)
    TISTORY_PASSWORD    Kakao 비밀번호
    TISTORY_HEADLESS    'true' 면 headless (기본 false — CAPTCHA 대응)
    TISTORY_DEBUG       디버그 스크린샷 (기본 false)
"""
from __future__ import annotations

import json
import mimetypes
import os
import time
from pathlib import Path
from typing import Optional

from common.browser_profile import PersistentBrowserProfile
from common.image import download as download_image, cleanup as cleanup_image, get_suffix
from common.logger import log
from .base import Publisher, PostResult


SESSION_DIR = Path(__file__).parent.parent / ".sessions"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

# 티스토리 카카오 SSO 앱 client_id (운영 로그에서 추출).
# 티스토리 페이지의 카카오 SDK는 Kakao.Auth.authorize({prompt:'select_account', ...})
# 를 호출해 신뢰토큰을 매번 깨뜨리는 부작용이 있다. publisher 는 이 SDK 호출을
# 우회하고 직접 /oauth/authorize URL 로 navigate 해 prompt 파라미터를 생략한다.
TISTORY_KAKAO_CLIENT_ID = "3e6ddd834b023f24221217e370daed18"
TISTORY_KAKAO_REDIRECT_URI = "https://www.tistory.com/auth/kakao/redirect"


class TistoryPublisher(Publisher):
    """티스토리 발행기 — Playwright persistent context 유지."""

    def __init__(self, blog_name: str):
        """
        Args:
            blog_name: 티스토리 블로그 ID (예: 'myblog' → myblog.tistory.com)
        """
        self.blog_name = blog_name
        self.blog_url = f"https://{blog_name}.tistory.com"

        SESSION_DIR.mkdir(exist_ok=True)
        # 한 Kakao 계정에 묶인 여러 티스토리 블로그를 동시 운영할 때, profile 을
        # 공유하면 한 블로그 로그인 후 다른 블로그도 바로 /manage 접근 가능하다.
        # blog_name 별 profile 이 필요한 경우 TISTORY_ISOLATED_PROFILE=true 로 오버라이드.
        profile_name = (
            f"tistory_{blog_name}"
            if os.getenv("TISTORY_ISOLATED_PROFILE", "").lower() == "true"
            else "tistory_shared"
        )
        self.profile = PersistentBrowserProfile(
            profile_name,
            user_agent=USER_AGENT,
        )

        # 티스토리 Kakao SSO 는 headless 에서 자주 막히고 UI 구조가 변동이 심해
        # 사용자가 화면을 보고 바로 개입할 수 있도록 **항상 headful** 로 고정.
        # (TISTORY_HEADLESS env 는 더 이상 효과 없음 — 호환성 차원에서 경고만)
        if os.getenv("TISTORY_HEADLESS", "").lower() == "true":
            log("[tistory] TISTORY_HEADLESS=true 는 무시 — 항상 headful 로 실행", "warn")
        self.headless = False
        self.debug = os.getenv("TISTORY_DEBUG", "false").lower() == "true"

        self.screenshot_dir = Path(__file__).parent.parent / "screenshots" / "tistory"
        # 항상 생성해둔다 (실패 시 스크린샷은 디버그 여부와 무관하게 유용)
        self.screenshot_dir.mkdir(parents=True, exist_ok=True)

        # 런타임 상태 (login() 성공 시 세팅, close() 로 정리)
        self._playwright = None
        self._context = None
        self._page = None

        self._csrf_token = ""

    # ─── 로그인 ──────────────────────────────────────────────────────────────

    def login(self) -> bool:
        """persistent profile 로 context 를 띄우고 세션 유효성 확인.

        profile 에 저장된 Kakao 간편로그인 토큰이 유효하면 ID/PW 재입력
        없이 자동 로그인되고, 만료됐다면 _kakao_login() 자동 호출.
        """
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            log("playwright 미설치: pip install playwright && playwright install chromium", "error")
            return False

        self.profile.user_data_dir.mkdir(parents=True, exist_ok=True)
        self._playwright = sync_playwright().start()
        try:
            self._context = self._playwright.chromium.launch_persistent_context(
                user_data_dir=str(self.profile.user_data_dir),
                headless=self.headless,
                user_agent=USER_AGENT,
                locale="ko-KR",
                args=["--disable-blink-features=AutomationControlled"],
            )
        except Exception as e:
            log(f"[tistory:{self.blog_name}] persistent context 실행 실패: {e}", "error")
            self._stop_playwright()
            return False

        # 브라우저 안에서 /manage 접근으로 세션 확인
        self._page = self._context.pages[0] if self._context.pages else self._context.new_page()

        if self._is_logged_in():
            log(f"[tistory:{self.blog_name}] 기존 세션 유효", "ok")
            return True

        log(f"[tistory:{self.blog_name}] 세션 만료 — Kakao 자동 로그인 시도", "warn")
        if self._kakao_login():
            if self._is_logged_in():
                log(f"[tistory:{self.blog_name}] Kakao 로그인 성공", "ok")
                return True
            log(f"[tistory:{self.blog_name}] 로그인 후 /manage 접근 실패", "error")
        else:
            log(f"[tistory:{self.blog_name}] Kakao 로그인 실패", "error")

        self.close()
        return False

    def _is_logged_in(self) -> bool:
        """persistent profile 이 블로그별 /manage 에 바로 들어갈 수 있는지 확인.

        티스토리는 blog-specific 세션이 별도라 Kakao/tistory 전역 로그인이
        돼있어도 /manage 직접 방문 시 auth/login 으로 리다이렉트되는 게 정상.
        따라서 이 메서드는 '더 빠른 경로' 용도 — False 반환 시 _kakao_login()
        으로 폴백해 redirectUrl 포함 전체 플로우를 다시 탄다.
        """
        try:
            self._page.goto(
                f"{self.blog_url}/manage",
                wait_until="domcontentloaded", timeout=15000,
            )
            time.sleep(1)
        except Exception as e:
            log(f"[tistory:{self.blog_name}] /manage goto 예외: {e}", "warn")
            return False

        try:
            cur = self._page.url
        except Exception:
            return False

        # auth/login 리다이렉트 = 세션 재인증 필요
        if "/auth/login" in cur:
            return False
        # 루트 tistory.com 으로 빠지면 blog-specific 세션 없음
        if cur.rstrip("/") in ("https://www.tistory.com", "https://tistory.com"):
            return False
        # 정상적인 manage 경로
        return "tistory.com/manage" in cur

    def _kakao_login(self) -> bool:
        """Kakao SSO 로그인 — www.tistory.com/auth/login 에서 Kakao 버튼 클릭.

        뉴스픽과 달리 티스토리의 Kakao 로그인은 **같은 창에서 리다이렉트**로
        진행된다 (popup 아님). ID/PW 폼 → 간편로그인 저장 체크 → 로그인 →
        oauth '계속하기' 동의 → tistory.com 복귀 순.
        """
        email = os.getenv("TISTORY_EMAIL", "")
        password = os.getenv("TISTORY_PASSWORD", "")
        if not email or not password:
            log("TISTORY_EMAIL / TISTORY_PASSWORD 미설정", "error")
            return False

        page = self._page
        try:
            # 로그인 후 자동으로 블로그별 manage 로 돌아가도록 redirectUrl 지정.
            # 이렇게 하면 Kakao SSO 성공 시 www.tistory.com/ 가 아니라
            # <blog>.tistory.com/manage 로 가서 블로그 세션 쿠키도 발급됨.
            from base64 import b64encode
            from urllib.parse import quote, urlencode
            redirect_url = f"{self.blog_url}/manage"

            # 카카오 SDK 우회 — Kakao.Auth.authorize 는 prompt=select_account 를
            # 강제로 붙여 신뢰토큰을 무효화한다. 직접 /oauth/authorize URL 을
            # 만들어 navigate 하면 저장된 간편로그인 세션이 그대로 유지된다.
            # state 는 티스토리 SDK 와 동일하게 redirectUrl 의 base64 인코딩.
            state = b64encode(redirect_url.encode("utf-8")).decode("ascii").rstrip("=")
            authorize_url = (
                "https://kauth.kakao.com/oauth/authorize?"
                + urlencode({
                    "client_id": TISTORY_KAKAO_CLIENT_ID,
                    "redirect_uri": TISTORY_KAKAO_REDIRECT_URI,
                    "response_type": "code",
                    "state": state,
                    "through_account": "true",
                })
            )
            try:
                page.goto(authorize_url, wait_until="domcontentloaded", timeout=30000)
                log("  Kakao OAuth URL 직접 진입 (prompt 옵션 생략)", "info")
            except Exception as e:
                # 폴백: 기존 경로 (티스토리 SDK 경유)
                log(f"  /oauth/authorize 직접 진입 실패 — SDK 폴백: {e}", "warn")
                page.goto(
                    f"https://www.tistory.com/auth/login?redirectUrl={quote(redirect_url, safe='')}",
                    wait_until="domcontentloaded", timeout=30000,
                )
                time.sleep(1)
                for sel in ['.link_kakao_id', 'a[href*="kauth.kakao.com"]',
                            'a[class*="kakao"]', 'button[class*="kakao"]']:
                    try:
                        page.click(sel, timeout=3000)
                        break
                    except Exception:
                        continue

            # Kakao 페이지 전환 대기 (최대 15초).
            # 카카오 신뢰토큰이 살아있으면 OAuth 가 카카오 도메인을 거치지 않고
            # 바로 blog_host/manage 로 점프하는 SSO fast-path 가 있다. 둘 다 잡아야 함.
            blog_host = self.blog_url.replace("https://", "").replace("http://", "")
            deadline = time.time() + 15
            on_kakao = False
            sso_done = False
            while time.time() < deadline:
                cur = page.url
                if blog_host in cur and "/manage" in cur and "/auth/" not in cur:
                    sso_done = True
                    break
                if "kauth.kakao.com" in cur or "accounts.kakao.com" in cur:
                    on_kakao = True
                    break
                time.sleep(0.3)

            if sso_done:
                log("  Kakao SSO fast-path — manage 직접 도달", "ok")
                return True

            if not on_kakao:
                log(f"Kakao 페이지 전환 실패 (현재 URL: {page.url})", "warn")
                return False

            # Kakao 페이지 상태 분기:
            #  (A) /oauth/authorize ... 이미 인증됨, 동의 단계로
            #  (B) /login/simple ...     간편로그인 — 저장된 계정 클릭만 하면 됨
            #  (C) /login(그 외)         일반 로그인 폼 — ID/PW 자동 입력
            cur_url = page.url
            if "/oauth/authorize" in cur_url:
                log("  이미 Kakao 로그인됨 — oauth 동의 단계로", "info")
            elif "/login/simple" in cur_url or "simpleLogin" in cur_url:
                if not self._click_saved_account(email):
                    # 실패 시 '새로운 계정으로 로그인' 버튼 눌러 일반 폼으로 폴백
                    log("  간편로그인 클릭 실패 — 일반 로그인 폼으로 폴백", "warn")
                    if not self._go_to_standard_form():
                        return False
                    if not self._fill_kakao_form(email, password):
                        return False
            else:
                if not self._fill_kakao_form(email, password):
                    return False

            # 성공 감지: redirectUrl 덕분에 최종적으로 blog_url/manage 에 도달해야 함.
            # context 의 모든 page 를 순회 (새 탭 가능성 대비). 최대 120초.
            deadline = time.time() + 120
            consent_clicked = False
            intervention_notified = False  # (E) 추가 인증 알림 1회만
            kakao_stuck_since: float | None = None  # 카카오 도메인 잔류 시작 시각
            last_urls: set[str] = set()
            root_goto_attempted = False  # (B) 루트 도달 후 /manage goto 중복 방지
            blog_host = self.blog_url.replace("https://", "").replace("http://", "")
            while time.time() < deadline:
                urls: list[str] = []
                for p in self._context.pages:
                    try:
                        urls.append(p.url)
                    except Exception:
                        continue
                for u in urls:
                    if u not in last_urls:
                        log(f"  URL → {u}", "info")
                        last_urls.add(u)

                # (A) 최종 목표: blog_host/manage 도달
                for u in urls:
                    if blog_host in u and "/manage" in u and "/auth/" not in u:
                        time.sleep(2)
                        return True

                # (B) www.tistory.com (루트) 에 이미 도달했으면 직접 manage 방문 트리거
                # 단, 이미 한 번 시도했으면 재진입하지 않는다 (무한 루프 방지).
                has_root = any(
                    ("www.tistory.com" in u or u.rstrip("/") == "https://tistory.com")
                    and "/auth/" not in u
                    and "kauth.kakao.com" not in u
                    and "accounts.kakao.com" not in u
                    for u in urls
                )
                if has_root and not root_goto_attempted:
                    root_goto_attempted = True
                    try:
                        page.goto(f"{self.blog_url}/manage",
                                  wait_until="domcontentloaded", timeout=20000)
                        time.sleep(2)
                        # 다시 URL 평가 — goto 후 바로 manage 에 있으면 성공
                        try:
                            after = page.url
                        except Exception:
                            after = ""
                        if blog_host in after and "/manage" in after and "/auth/" not in after:
                            return True
                        # auth/login 리다이렉트면 계속 루프 (Kakao 폼 자동 입력 기회 탐색)
                    except Exception:
                        pass

                # (C) Kakao 로그인 폼이 다시 떠있으면 한 번 더 자동 입력
                try:
                    cur = page.url
                except Exception:
                    cur = ""
                if ("accounts.kakao.com/login" in cur
                        and "/oauth/authorize" not in cur
                        and "/login/simple" not in cur):
                    # ID 필드 또는 PW 필드 중 하나라도 visible 이면 재입력
                    try:
                        has_id = page.locator('input[name="loginId"]').count() > 0
                        has_pw = page.locator('input[type="password"]').count() > 0
                        has_input = has_id or has_pw
                    except Exception:
                        has_input = False
                    if has_input:
                        log("  Kakao 폼 재등장 — 자동 재입력", "info")
                        self._fill_kakao_form(email, password)

                # (D) oauth 동의 화면 자동 처리
                if not consent_clicked and "oauth/authorize" in cur:
                    consent_clicked = self._click_consent(page)

                # (E) 카카오 도메인에 25초 이상 머무르면 추가 인증/캡차로 추정 — 즉시 통지
                on_kakao_now = "kakao.com" in cur
                if on_kakao_now:
                    if kakao_stuck_since is None:
                        kakao_stuck_since = time.time()
                    elif (not intervention_notified
                            and time.time() - kakao_stuck_since > 25):
                        self._notify_login_stuck(cur)
                        intervention_notified = True
                else:
                    kakao_stuck_since = None

                time.sleep(1)

            # 시간 초과 시 화면에 떠 있는 에러/안내 메시지를 추출해 사용자 개입 가이드
            hint = self._extract_login_hint()
            if hint:
                log(f"  화면 단서: {hint[:300]}", "warn")
                if not intervention_notified:
                    self._notify_login_stuck(page.url, hint)
                    intervention_notified = True
            log(f"로그인 완료 대기 시간 초과 (URL: {page.url})", "warn")
            try:
                ts = int(time.time())
                page.screenshot(
                    path=str(self.screenshot_dir / f"{self.blog_name}_timeout_{ts}.png"),
                    full_page=True,
                )
            except Exception:
                pass

            # 폴백: timeout 됐지만 실제로는 manage 도달했을 가능성 (false negative).
            # 운영 로그·스크린샷 기준 — timeout 직후 page.url 이 manage 인 사례가
            # 실제로 있었다. /manage 한 번 더 시도해 _is_logged_in() 으로 확인.
            if self._is_logged_in():
                log(f"[tistory:{self.blog_name}] timeout 이후 /manage 도달 확인 — 성공으로 처리", "ok")
                return True
            return False
        except Exception as e:
            log(f"Kakao 로그인 예외: {e}", "error")
            return False

    def _extract_login_hint(self) -> str:
        """현재 페이지에서 로그인 차단/추가 인증 안내 문구를 추출."""
        try:
            return self._page.evaluate("""
                () => {
                    const body = document.body ? document.body.innerText : '';
                    const keywords = ['비밀번호','보안','인증','실패','일치','캡차','captcha','CAPTCHA','2단계','차단','5회','잠금','확인','보호'];
                    const lines = body.split('\\n').map(s => s.trim()).filter(Boolean)
                        .filter(l => keywords.some(k => l.includes(k)));
                    return lines.slice(0, 6).join(' | ');
                }
            """) or ""
        except Exception:
            return ""

    def _notify_login_stuck(self, url: str, hint: str = "") -> None:
        """카카오 추가 인증/캡차 화면 감지 시 텔레그램·카톡 즉시 통지."""
        try:
            from common.notifier import notify_login_intervention
        except Exception:
            return
        if not hint:
            hint = self._extract_login_hint()
        try:
            notify_login_intervention(
                f"티스토리 카카오 로그인 ({self.blog_name})",
                hint or "추가 인증/캡차 화면 감지 — 수동 개입 필요",
                url,
            )
        except Exception:
            pass
        # 디버그 스크린샷도 남겨둔다
        try:
            ts = int(time.time())
            self._page.screenshot(
                path=str(self.screenshot_dir / f"{self.blog_name}_intervention_{ts}.png"),
                full_page=True,
            )
        except Exception:
            pass

    def _click_saved_account(self, email: str) -> bool:
        """간편로그인 화면에서 저장된 이메일 계정 항목을 클릭.

        Kakao 간편로그인 (`accounts.kakao.com/login/simple`) DOM 구조:
            <ul class="kc_lst_select">
              <li class="kc_item_select">
                <button class="kc_btn_simple" ...>     ← 클릭 타겟
                  ...아이콘/이메일 표시...
                </button>
                <button class="btn_delete">X</button>  ← 절대 클릭 금지
              </li>
            </ul>

        이메일 텍스트 단순 검색은 'btn_delete' 의 aria-describedby 같은
        속성에 매칭되어 잘못된 X 버튼을 클릭할 위험이 있어 의도적으로
        kc_item_select / kc_btn_simple 등 Kakao 전용 클래스 우선 사용한다.

        클릭 후 navigation 발생을 검증해 false positive (UI 는 클릭됐는데
        실제 로그인 동작 없음) 를 걸러낸다.
        """
        page = self._page

        # 1) 신 DOM (2026+) — 이메일 텍스트 가진 wrap_profile 앵커가 진짜 계정.
        #    .kc_item_select 단독은 언어 선택 박스에 매치되므로 사용 금지.
        kakao_selectors = [
            f'a.wrap_profile[role="button"]:has-text("{email}")',
            f'a.wrap_profile:has-text("{email}")',
            # 구 DOM 폴백 (kc_btn_simple 은 진짜 계정 버튼에 붙던 클래스)
            ".kc_item_select .kc_btn_simple",
            "button.kc_btn_simple",
            "[data-testid*='account']",
        ]

        # 2) 텍스트 기반 폴백 — 단, 'btn_delete' 등 X 버튼 클래스는 제외
        text_selectors = [
            f'a:has-text("{email}"):not(.btn_delete)',
            f'button:has-text("{email}"):not(.btn_delete)',
            f'li:has-text("{email}")',
        ]

        attempted = False
        for sel in kakao_selectors + text_selectors:
            try:
                loc = page.locator(sel).first
                if loc.count() == 0:
                    continue
                loc.wait_for(state="visible", timeout=2000)
                # 클릭 후 navigation 까지 한 호흡에 검증
                cur_url = page.url
                try:
                    with page.expect_navigation(timeout=8000, wait_until="domcontentloaded"):
                        loc.click(timeout=2000)
                    log(f"  간편로그인 계정 클릭 ({sel})", "ok")
                    return True
                except Exception:
                    # navigation 이 안 일어났으면 클릭이 잘못된 element 였을 가능성.
                    # URL 이 그대로면 다음 셀렉터로 재시도, 변했으면 성공으로 인정.
                    try:
                        new_url = page.url
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

        # 3) JS evaluate 최종 폴백 — 이메일 텍스트가 포함되고 btn_delete 가 아닌 요소
        try:
            ok = page.evaluate(
                """(target) => {
                    const candidates = Array.from(document.querySelectorAll(
                        '.kc_item_select, .kc_btn_simple, button:not(.btn_delete), a, li'
                    ));
                    // 이메일 텍스트가 포함된 첫 번째 visible 요소
                    const cand = candidates.find(el => {
                        if (!el.offsetParent) return false;
                        if (el.classList.contains('btn_delete')) return false;
                        return (el.innerText || '').includes(target);
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

    def _go_to_standard_form(self) -> bool:
        """간편로그인 화면에서 '새로운 계정으로 로그인' 클릭해 일반 폼 진입."""
        page = self._page
        for sel in [
            'button:has-text("새로운 계정으로 로그인")',
            'a:has-text("새로운 계정으로 로그인")',
            'button:has-text("다른 계정으로 로그인")',
            'a[href*="login?continue"]',
        ]:
            try:
                page.click(sel, timeout=2000)
                import time; time.sleep(1)
                return True
            except Exception:
                continue
        return False

    def _fill_kakao_form(self, email: str, password: str) -> bool:
        """Kakao accounts.kakao.com 로그인 폼 자동 입력."""
        page = self._page
        id_selectors = [
            # 현행 Kakao accounts (2026+)
            'input[name="loginId"]',
            'input#loginId--1',
            'input#loginId',
            # 구 버전 호환
            'input[name="loginKey"]',
            'input[name="email"]',
            'input[placeholder*="카카오메일"]',
            'input[placeholder*="이메일"]',
            'input[placeholder*="아이디"]',
            'input[type="text"]',
        ]
        pw_selectors = [
            'input[name="password"]',
            'input#password--2',
            'input#password',
            'input[type="password"]',
        ]

        # ID 필드 가시 여부 먼저 확인
        id_el = None
        for sel in id_selectors:
            try:
                page.wait_for_selector(sel, timeout=1500, state="visible")
                id_el = sel
                break
            except Exception:
                continue

        # PW 필드 가시 여부 확인
        pw_already_visible = False
        for sel in pw_selectors:
            try:
                page.wait_for_selector(sel, timeout=1000, state="visible")
                pw_already_visible = True
                break
            except Exception:
                continue

        if id_el:
            # ID 필드가 보이면 항상 ID 입력 (PW 동시 노출 여부 무관)
            page.fill(id_el, email)
            time.sleep(0.3)
        elif pw_already_visible:
            # ID 없이 PW만 보이는 경우 = 2단계에서 PW 단계만 남은 것
            log("  Kakao PW 단계 직접 감지 — ID 입력 생략", "info")
        else:
            log(f"Kakao ID 필드 감지 실패 (URL: {page.url})", "warn")
            return False

        # 카카오 2단계 플로우: ID 입력 후 PW가 바로 안 나타나면 "다음" 버튼 클릭
        pw_visible = pw_already_visible
        for sel in pw_selectors:
            try:
                page.wait_for_selector(sel, timeout=1500, state="visible")
                pw_visible = True
                break
            except Exception:
                continue

        if not pw_visible:
            # "다음" 버튼 클릭 시도 (ID/PW 분리 2단계 플로우)
            for next_sel in [
                'button[type="submit"]:has-text("다음")',
                'button:has-text("다음")',
                'button.btn_g.highlight',
                'button[type="submit"]',
            ]:
                try:
                    page.click(next_sel, timeout=2000)
                    log("  Kakao '다음' 버튼 클릭 — PW 필드 대기", "info")
                    time.sleep(1.5)
                    break
                except Exception:
                    continue

        pw_filled = False
        for attempt in range(4):
            for sel in pw_selectors:
                try:
                    page.wait_for_selector(sel, timeout=3000, state="visible")
                    page.fill(sel, password)
                    pw_filled = True
                    break
                except Exception:
                    continue
            if pw_filled:
                break
            time.sleep(1)
        if not pw_filled:
            log("Kakao PW 필드 감지 실패", "warn")
            return False

        # 간편로그인 정보 저장 체크 (신뢰 기기 토큰 발급)
        for sel in [
            # 현행 Kakao accounts (2026+)
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
                el = page.locator(sel).first
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
        clicked = False
        for sel in [
            'button[type="submit"]:has-text("로그인")',
            'button.btn_g.highlight.submit',
            'button[type="submit"]',
            'button.submit',
            'button:has-text("로그인")',
        ]:
            try:
                page.click(sel, timeout=3000)
                clicked = True
                break
            except Exception:
                continue
        if not clicked:
            page.keyboard.press("Enter")
        log("Kakao 자동 입력 완료 — 로그인 처리 대기", "info")
        return True

    @staticmethod
    def _click_consent(kakao_page) -> bool:
        """oauth/authorize 동의 화면 '계속하기' 자동 클릭."""
        for sel in [
            'button:has-text("계속하기")', 'a:has-text("계속하기")',
            '[role="button"]:has-text("계속하기")',
            'button.btn_agree', 'button.submit', 'button[type="submit"]',
        ]:
            try:
                loc = kakao_page.locator(sel).first
                if loc.count() == 0:
                    continue
                loc.wait_for(state="visible", timeout=1500)
                loc.click(timeout=2000)
                log(f"  oauth 동의 클릭 ({sel})", "ok")
                return True
            except Exception:
                continue
        # JS fallback
        try:
            ok = kakao_page.evaluate("""
                () => {
                    const cand = Array.from(
                        document.querySelectorAll('button, a, [role="button"]')
                    ).find(el => (el.innerText||'').trim().includes('계속하기'));
                    if (cand) { cand.click(); return true; }
                    return false;
                }
            """)
            if ok:
                log("  oauth 동의 클릭 (JS fallback)", "ok")
                return True
        except Exception:
            pass
        return False

    # ─── API 호출 (context.request) ──────────────────────────────────────────

    def _api_headers(self, *, content_type: str = "application/json;charset=UTF-8") -> dict:
        headers = {
            "accept": "application/json, text/plain, */*",
            "content-type": content_type,
            "origin": self.blog_url,
            "referer": f"{self.blog_url}/manage/newpost/?type=post&returnURL=%2Fmanage%2Fposts%2F",
        }
        token = getattr(self, "_csrf_token", "")
        # 토큰 lazy 추출 — 티스토리는 TOP-XSRF-TOKEN 쿠키로 발급. 빈 헤더로
        # post.json 호출 시 400 {"data":null,"message":""} 반환됨.
        if not token and self._context is not None:
            try:
                for c in self._context.cookies():
                    if c.get("name") == "TOP-XSRF-TOKEN":
                        token = c.get("value", "")
                        self._csrf_token = token
                        break
            except Exception:
                pass
        if token:
            headers["x-csrf-token"] = token
        return headers

    # ─── 카테고리 ─────────────────────────────────────────────────────────────

    def get_categories(self) -> list[dict]:
        """블로그 카테고리 목록 반환 (브라우저 쿠키·TLS 사용)."""
        if self._context is None:
            return []
        try:
            resp = self._context.request.get(
                f"{self.blog_url}/manage/category.json",
                headers={
                    "accept": "*/*",
                    "content-type": "application/json",
                    "referer": f"{self.blog_url}/manage/category",
                },
                timeout=10000,
            )
            if not resp.ok:
                log(f"카테고리 조회 실패: {resp.status}", "error")
                return []
            data = resp.json()
            return data.get("categories", [])
        except Exception as e:
            log(f"카테고리 조회 예외: {e}", "error")
            return []

    def get_category_id(self, name: str) -> str:
        if not name:
            return ""
        for cat in self.get_categories():
            if cat.get("name") == name or cat.get("label") == name:
                return str(cat.get("id", ""))
        return ""

    # ─── 이미지 업로드 ────────────────────────────────────────────────────────

    def upload_image(self, local_path: str) -> dict:
        """티스토리에 이미지 업로드. Playwright context.request multipart 사용."""
        if self._context is None:
            log("upload_image: context 없음", "error")
            return {}
        ext = os.path.splitext(local_path)[1].lstrip(".").lower()
        mime = mimetypes.types_map.get(f".{ext}") or (
            f"image/{ext}" if ext != "jpg" else "image/jpeg"
        )
        filename = os.path.basename(local_path)

        try:
            with open(local_path, "rb") as f:
                file_bytes = f.read()

            # Playwright multipart 형식: {필드명: {name, mimeType, buffer}}
            resp = self._context.request.post(
                f"{self.blog_url}/manage/post/attach.json",
                multipart={
                    "file": {
                        "name": filename,
                        "mimeType": mime,
                        "buffer": file_bytes,
                    },
                },
                headers={
                    "accept": "application/json, text/plain, */*",
                    "origin": self.blog_url,
                    "referer": f"{self.blog_url}/manage/newpost/?type=post&returnURL=/manage/posts",
                },
                timeout=30000,
            )
            if not resp.ok:
                body = ""
                try:
                    body = resp.text()[:200]
                except Exception:
                    pass
                log(f"이미지 업로드 실패 [{resp.status}]: {body}", "error")
                return {}
            meta = resp.json()
            log(f"이미지 업로드 완료: {meta.get('url', '')}", "ok")
            return meta
        except Exception as e:
            log(f"이미지 업로드 예외: {e}", "error")
            return {}

    # ─── 포스트 발행 ──────────────────────────────────────────────────────────

    def post(self, title: str, content: str,
             tags: list[str] = None, category: str = "",
             image_url: str = "", **kwargs) -> PostResult:
        """티스토리 포스트 발행.

        kwargs:
            visibility: 0=비공개, 15=보호, 20=공개 (기본 20)
            slogan: URL 슬러그 (선택)
            published: 즉시 발행 1 (기본)
        """
        if self._context is None:
            return PostResult(success=False, message="context 없음 — login() 먼저 호출")

        log(f"티스토리 발행: {title[:40]}", "step")

        # 대표 이미지 업로드
        thumbnail_key = ""
        thumbnail_filename = ""
        attachments: list[str] = []
        image_markup = ""
        tmp_path = None
        if image_url:
            try:
                suffix = get_suffix(image_url)
                tmp_path = download_image(image_url, suffix)
                meta = self.upload_image(tmp_path)
                if meta:
                    thumbnail_key = meta.get("key", "")
                    # attach.json 응답: {"filename": "img.png", "name": "원본.png", ...}
                    # 'filename' 은 kage 경로의 마지막 컴포넌트. 'name' 은 원본 파일명.
                    # post.json 에 보낼 attachments / markup 은 server-side 'filename' 을 써야 함.
                    server_fname = meta.get("filename") or meta.get("name") or "img.jpg"
                    thumbnail_filename = meta.get("name", server_fname)
                    if thumbnail_key:
                        attachments.append(f"kage@{thumbnail_key}/{server_fname}")
                        image_markup = (
                            f'<p>[##_Image|kage@{thumbnail_key}/{server_fname}|CDM|1.3|'
                            f'{{"originWidth":800,"originHeight":600,'
                            f'"style":"alignCenter","filename":"{thumbnail_filename}"}}_##]</p>\n'
                        )
            finally:
                if tmp_path:
                    cleanup_image(tmp_path)

        if image_markup:
            content = image_markup + content

        cat_id = self.get_category_id(category) if category else ""

        json_payload = {
            "id": "0",
            "title": title,
            "content": content,
            "slogan": kwargs.get("slogan", ""),
            "visibility": kwargs.get("visibility", 20),
            "category": cat_id or 0,
            "tag": ",".join(tags) if tags else "",
            "published": kwargs.get("published", 1),
            "password": "",
            "uselessMarginForEntry": 1,
            "daumLike": 401,
            "cclCommercial": 0,
            "cclDerive": 0,
            "thumbnail": (attachments[0] if attachments else None),
            "type": "post",
            "attachments": attachments,
            "recaptchaValue": "",
            "draftSequence": None,
        }

        try:
            resp = self._context.request.post(
                f"{self.blog_url}/manage/post.json",
                headers=self._api_headers(),
                data=json.dumps(json_payload, ensure_ascii=False).encode("utf-8"),
                timeout=30000,
            )
        except Exception as e:
            log(f"티스토리 발행 요청 예외: {e}", "error")
            return PostResult(success=False, message=str(e))

        if not resp.ok:
            body = ""
            try:
                body = resp.text()[:300]
            except Exception:
                pass
            log(f"티스토리 발행 실패 [{resp.status}]: {body}", "error")
            return PostResult(
                success=False,
                message=f"HTTP {resp.status}: {body[:200]}",
            )

        try:
            data = resp.json()
        except Exception:
            data = {}

        post_id = str(data.get("id") or data.get("postId") or "")
        post_url = data.get("url") or data.get("permalink") or ""
        if post_id and not post_url:
            post_url = f"{self.blog_url}/{post_id}"

        log(f"티스토리 발행 성공: {post_url}", "ok")
        return PostResult(success=True, url=post_url, post_id=post_id)

    # ─── 세션 관리 ────────────────────────────────────────────────────────────

    def close(self) -> None:
        """context 정리. 이후 post() 호출 불가."""
        try:
            if self._context is not None:
                self._context.close()
        except Exception:
            pass
        self._context = None
        self._page = None
        self._stop_playwright()

    def _stop_playwright(self) -> None:
        try:
            if self._playwright is not None:
                self._playwright.stop()
        except Exception:
            pass
        self._playwright = None

    def logout(self) -> None:
        """persistent profile 완전 삭제 (재로그인 강제용)."""
        self.close()
        import shutil
        if self.profile.user_data_dir.exists():
            shutil.rmtree(self.profile.user_data_dir, ignore_errors=True)
            log(f"[tistory:{self.blog_name}] persistent profile 삭제", "warn")

    def __del__(self):
        # 인스턴스 가비지 수집 시 리소스 누수 방지
        try:
            self.close()
        except Exception:
            pass
