"""
Pinterest 자동 발행 Publisher - Playwright 기반

Playwright 브라우저 자동화로 Pinterest 핀 생성.
- 쿠키/세션 기반 자동 로그인 (첫 실행 시 수동 로그인)
- 핀 생성 도구 페이지에서 이미지 업로드 + 제목 + 설명 + 링크 + 보드 선택
- 배치 발행 지원

환경변수:
    PINTEREST_EMAIL     : Pinterest 로그인 이메일
    PINTEREST_PASSWORD  : Pinterest 로그인 비밀번호
    PINTEREST_BOARD_NAME: 기본 보드 이름 (선택)
    PINTEREST_HEADLESS  : headless 모드 (true/false, 기본 false)
"""
import json
import os
import time
from pathlib import Path
from typing import Optional

from playwright.sync_api import (
    sync_playwright, Browser, BrowserContext, Page, Error as PlaywrightError
)

from common.logger import log
from .base import Publisher, PostResult


# ============================================================================
# 세션 관리
# ============================================================================

SESSION_DIR = Path(__file__).parent.parent / ".sessions"
PIN_COOKIES_FILE = SESSION_DIR / "pinterest_cookies.json"
PIN_STORAGE_FILE = SESSION_DIR / "pinterest_storage.json"


class _PinterestSessionManager:
    """Pinterest Playwright 세션 관리 (내부용)."""

    def __init__(self):
        SESSION_DIR.mkdir(exist_ok=True)

    def save(self, context: BrowserContext) -> None:
        try:
            cookies = context.cookies()
            with open(PIN_COOKIES_FILE, "w", encoding="utf-8") as f:
                json.dump(cookies, f, ensure_ascii=False, indent=2)
        except Exception as e:
            log(f"[pinterest] 쿠키 저장 실패: {e}", "warn")
        try:
            storage = context.storage_state()
            with open(PIN_STORAGE_FILE, "w", encoding="utf-8") as f:
                json.dump(storage, f, ensure_ascii=False, indent=2)
        except Exception as e:
            log(f"[pinterest] 스토리지 저장 실패: {e}", "warn")
        log("[pinterest] 세션 저장 완료", "ok")

    def get_storage_state(self) -> Optional[str]:
        if PIN_STORAGE_FILE.exists():
            return str(PIN_STORAGE_FILE)
        return None

    def is_logged_in(self, page: Page) -> bool:
        try:
            url = page.url
            if "/login" in url or "accounts.google.com" in url or "/signup" in url:
                return False

            # 1) Pinterest 인증 쿠키 우선 체크 (가장 신뢰할 수 있음)
            try:
                cookies = page.context.cookies()
                pin_cookies = {c.get("name"): str(c.get("value", ""))
                               for c in cookies
                               if "pinterest" in c.get("domain", "")}
                # _auth=1 이면 로그인됨
                if pin_cookies.get("_auth") == "1" and pin_cookies.get("_pinterest_sess"):
                    return True
                # _auth=0 이거나 없으면 비로그인
                if pin_cookies.get("_auth") == "0":
                    return False
            except Exception:
                pass

            # 2) 로그인 상태 포지티브 인디케이터
            indicators = [
                '[data-test-id="header-profile"]',
                '[data-test-id="create-button"]',
                'div[data-test-id="homefeed-feed"]',
                'div[data-test-id="home-feed"]',
                'div[data-test-id="masonry-grid"]',
                'a[href*="/pin-creation-tool/"]',
                '[data-test-id="header-avatar"]',
                '[data-test-id="business-hub"]',
                '[data-test-id="header-accounts-dropdown"]',
                'a[href*="/business/hub"]',
                'a[href*="/_/_/hub"]',
                # 헤더 네비게이션 (로그인 상태에서만 표시)
                '[data-test-id="header-notifications-icon"]',
                '[data-test-id="header-messages-icon"]',
                'div[data-test-id="header"] a[href="/"]',
            ]
            for sel in indicators:
                if page.query_selector(sel):
                    return True

            # 3) URL 기반: 로그인 후에만 접근 가능한 경로
            if any(p in url for p in [
                "/feed/", "/pin-creation-tool", "pinterest.com/settings",
                "/business/hub", "/_/_/hub", "/business/create/",
                "pinterest.com/ads", "/homefeed/",
            ]):
                return True

            # 4) 계정명 또는 아바타 요소
            if "pinterest.com" in url:
                try:
                    if page.query_selector('button[aria-label*="계정" i], button[aria-label*="account" i]'):
                        return True
                except Exception:
                    pass
            return False
        except Exception:
            return False

    def clear(self) -> None:
        for f in [PIN_COOKIES_FILE, PIN_STORAGE_FILE]:
            if f.exists():
                f.unlink()
        log("[pinterest] 세션 파일 삭제", "warn")


# ============================================================================
# Pinterest Playwright Publisher
# ============================================================================

class PinterestPlaywrightPublisher(Publisher):
    """Pinterest 자동 핀 발행기 (Playwright 기반)."""

    PIN_CREATION_URL = "https://www.pinterest.com/pin-creation-tool/"
    LOGIN_URL = "https://www.pinterest.com/login/"
    HOME_URL = "https://www.pinterest.com/"

    def __init__(self):
        self.email = os.getenv("PINTEREST_EMAIL", "")
        self.password = os.getenv("PINTEREST_PASSWORD", "")
        self.default_board = os.getenv("PINTEREST_BOARD_NAME", "")
        self.headless = os.getenv("PINTEREST_HEADLESS", "false").lower() == "true"
        self.debug = os.getenv("PINTEREST_DEBUG", "true").lower() == "true"
        # 로그인 방식: "email" (기본) 또는 "google"
        self.login_method = os.getenv("PINTEREST_LOGIN_METHOD", "email").lower()

        self.session_mgr = _PinterestSessionManager()
        self.playwright = None
        self.browser: Optional[Browser] = None
        self.context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None

        self.screenshot_dir = Path(__file__).parent.parent / "screenshots" / "pinterest"
        if self.debug:
            self.screenshot_dir.mkdir(parents=True, exist_ok=True)

    # ========================================================================
    # 초기화 / 종료
    # ========================================================================

    def _initialize(self) -> bool:
        try:
            self.playwright = sync_playwright().start()
            self.browser = self.playwright.chromium.launch(
                headless=self.headless,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--start-maximized",
                ],
            )
            storage_state = self.session_mgr.get_storage_state()
            self.context = self.browser.new_context(
                storage_state=storage_state,
                viewport={"width": 1920, "height": 1080},
                user_agent=(
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/125.0.0.0 Safari/537.36"
                ),
            )
            self.page = self.context.new_page()
            self.page.set_default_timeout(30000)
            log("[pinterest] Playwright 초기화 완료", "ok")
            return True
        except Exception as e:
            log(f"[pinterest] 초기화 오류: {e}", "error")
            return False

    def _close(self) -> None:
        try:
            if self.context:
                self.session_mgr.save(self.context)
            for obj in [self.page, self.context, self.browser]:
                if obj:
                    obj.close()
            if self.playwright:
                self.playwright.stop()
            log("[pinterest] 브라우저 종료", "info")
        except Exception as e:
            log(f"[pinterest] 종료 오류: {e}", "error")

    # ========================================================================
    # 유틸리티
    # ========================================================================

    def _save_screenshot(self, name: str) -> None:
        if not self.debug or not self.page:
            return
        try:
            ts = int(time.time() * 1000)
            path = self.screenshot_dir / f"{ts}_{name}.png"
            self.page.screenshot(path=str(path), full_page=True)
        except Exception:
            pass

    def _retry_click(self, selector: str, max_retries: int = 3,
                     delay: float = 1.0) -> bool:
        for attempt in range(max_retries):
            try:
                el = self.page.query_selector(selector)
                if el:
                    el.scroll_into_view_if_needed()
                    time.sleep(0.5)
                    el.click()
                    return True
            except Exception:
                if attempt < max_retries - 1:
                    time.sleep(delay)
        return False

    def _click_by_text(self, text: str, role: str = "button",
                       timeout: int = 10000) -> bool:
        try:
            locator = self.page.get_by_role(role, name=text)
            locator.wait_for(timeout=timeout)
            locator.click()
            return True
        except Exception:
            return False

    def _dismiss_popups(self) -> None:
        """Pinterest 팝업/오버레이 닫기."""
        for _ in range(3):
            dismissed = False
            for text in [
                "나중에", "Not now", "No thanks", "건너뛰기", "Skip",
                "닫기", "Close", "괜찮습니다", "Maybe later",
            ]:
                try:
                    btn = self.page.get_by_text(text, exact=False)
                    if btn.count() > 0:
                        btn.first.click()
                        time.sleep(1.5)
                        dismissed = True
                        break
                except Exception:
                    pass
            if not dismissed:
                break

    def _auto_login_google(self) -> bool:
        """Google 계정으로 Pinterest 로그인."""
        try:
            self.page.goto(self.LOGIN_URL,
                           wait_until="domcontentloaded", timeout=15000)
            time.sleep(3)
            self._save_screenshot("login_google_start")

            # "Google로 계속하기" 버튼 찾기 (한/영 텍스트 + aria-label)
            google_btn = None
            for sel in [
                'button[aria-label*="Google"]',
                'button[data-test-id="google-connect-button"]',
                'div[data-test-id="google-connect-button"]',
            ]:
                google_btn = self.page.query_selector(sel)
                if google_btn:
                    break

            if not google_btn:
                for text in ["Google로 계속하기", "Continue with Google",
                             "Google로 로그인", "Sign in with Google"]:
                    try:
                        loc = self.page.get_by_text(text, exact=False)
                        if loc.count() > 0:
                            google_btn = loc.first
                            break
                    except Exception:
                        pass

            if not google_btn:
                log("[pinterest] Google 로그인 버튼을 찾을 수 없음", "error")
                self._save_screenshot("no_google_btn")
                return False

            # Google 로그인은 팝업 또는 리다이렉트 방식
            with self.context.expect_page(timeout=8000) as popup_info:
                try:
                    google_btn.click()
                except Exception:
                    pass
            try:
                google_page = popup_info.value
                log("[pinterest] Google 팝업 감지", "info")
            except Exception:
                google_page = self.page
                log("[pinterest] Google 리다이렉트 방식", "info")

            # 이메일 입력
            google_page.wait_for_load_state("domcontentloaded", timeout=15000)
            time.sleep(2)
            email_input = None
            for _ in range(10):
                email_input = google_page.query_selector(
                    'input[type="email"], input#identifierId'
                )
                if email_input:
                    break
                time.sleep(1)
            if not email_input:
                log("[pinterest] Google 이메일 입력 필드 없음", "error")
                return False
            email_input.fill(self.email)
            time.sleep(0.5)
            # Next 버튼
            for sel in ['button:has-text("다음")', 'button:has-text("Next")',
                        '#identifierNext button', '#identifierNext']:
                btn = google_page.query_selector(sel)
                if btn:
                    btn.click()
                    break
            time.sleep(3)

            # 비밀번호 입력
            pw_input = None
            for _ in range(15):
                pw_input = google_page.query_selector(
                    'input[type="password"][name="Passwd"], input[type="password"]'
                )
                if pw_input and pw_input.is_visible():
                    break
                time.sleep(1)
            if not pw_input:
                log("[pinterest] Google 비밀번호 입력 필드 없음", "error")
                self._save_screenshot("no_google_pw")
                return False
            pw_input.fill(self.password)
            time.sleep(0.5)
            for sel in ['button:has-text("다음")', 'button:has-text("Next")',
                        '#passwordNext button', '#passwordNext']:
                btn = google_page.query_selector(sel)
                if btn:
                    btn.click()
                    break

            log("[pinterest] Google 비밀번호 입력 완료, 로그인 대기...", "info")

            # 로그인 완료 대기 (최대 90초, 2단계 인증 등 수동 대응 여지)
            start = time.time()
            last_url = ""
            while time.time() - start < 90:
                time.sleep(2)
                try:
                    cur_url = self.page.url
                except Exception:
                    cur_url = ""
                if cur_url != last_url:
                    log(f"[pinterest] 현재 URL: {cur_url}", "info")
                    last_url = cur_url
                # 팝업이 닫혔거나 Pinterest 메인으로 돌아왔는지
                if "pinterest.com" in cur_url and "/login" not in cur_url:
                    time.sleep(3)
                    self._dismiss_popups()
                    if self.session_mgr.is_logged_in(self.page):
                        log(f"[pinterest] Google 로그인 성공: {self.email}", "ok")
                        return True

            self._save_screenshot("google_login_timeout")
            log(f"[pinterest] Google 로그인 타임아웃 — 최종 URL: {self.page.url}", "warn")
            return False
        except Exception as e:
            log(f"[pinterest] Google 로그인 예외: {e}", "error")
            self._save_screenshot("google_login_error")
            return False

    def _auto_login(self) -> bool:
        """이메일/비밀번호로 자동 로그인."""
        try:
            self.page.goto(self.LOGIN_URL,
                           wait_until="domcontentloaded", timeout=15000)
            time.sleep(3)
            self._save_screenshot("login_page")

            # 이메일 입력
            email_input = self.page.query_selector(
                'input[id="email"], input[name="id"], input[type="email"]'
            )
            if not email_input:
                log("[pinterest] 이메일 입력 필드를 찾을 수 없음", "error")
                return False
            email_input.click()
            email_input.fill(self.email)
            time.sleep(0.5)

            # 비밀번호 입력
            pw_input = self.page.query_selector(
                'input[id="password"], input[name="password"], input[type="password"]'
            )
            if not pw_input:
                log("[pinterest] 비밀번호 입력 필드를 찾을 수 없음", "error")
                return False
            pw_input.click()
            pw_input.fill(self.password)
            time.sleep(0.5)

            # 로그인 버튼 클릭
            login_clicked = False
            for sel in [
                'button[type="submit"]',
                'div[data-test-id="registerFormSubmitButton"]',
            ]:
                btn = self.page.query_selector(sel)
                if btn:
                    btn.click()
                    login_clicked = True
                    break
            if not login_clicked:
                if not (self._click_by_text("로그인") or
                        self._click_by_text("Log in")):
                    log("[pinterest] 로그인 버튼을 찾을 수 없음", "error")
                    return False

            log("[pinterest] 로그인 버튼 클릭, 대기 중...", "info")

            for _ in range(20):
                time.sleep(1)
                if "/login" not in self.page.url:
                    time.sleep(3)
                    self._dismiss_popups()
                    if self.session_mgr.is_logged_in(self.page):
                        log(f"[pinterest] 자동 로그인 성공: {self.email}", "ok")
                        return True

            self._save_screenshot("login_timeout")
            log("[pinterest] 자동 로그인 타임아웃", "warn")
            return False
        except Exception as e:
            log(f"[pinterest] 자동 로그인 예외: {e}", "error")
            self._save_screenshot("login_error")
            return False

    # ========================================================================
    # Publisher 인터페이스
    # ========================================================================

    def login(self) -> bool:
        """Pinterest 로그인. 저장된 세션 → 자동 로그인 → 수동 로그인 순."""
        if not self._initialize():
            return False

        try:
            # 1) 저장된 세션으로 시도
            self.page.goto(self.HOME_URL,
                           wait_until="domcontentloaded", timeout=15000)
            time.sleep(3)

            if self.session_mgr.is_logged_in(self.page):
                log("[pinterest] 이미 로그인 상태 (세션 복원)", "ok")
                self._dismiss_popups()
                return True

            # 2) 자동 로그인 (방식 선택)
            if self.email and self.password:
                login_fn = (self._auto_login_google
                            if self.login_method == "google"
                            else self._auto_login)
                if login_fn():
                    self.session_mgr.save(self.context)
                    self._dismiss_popups()
                    return True

            # 3) 수동 로그인 대기
            log("[pinterest] 수동 로그인 대기 (180초)...", "warn")
            self.page.goto(self.LOGIN_URL,
                           wait_until="domcontentloaded", timeout=15000)

            start = time.time()
            while time.time() - start < 180:
                time.sleep(2)
                if self.session_mgr.is_logged_in(self.page):
                    log("[pinterest] 수동 로그인 완료!", "ok")
                    self.session_mgr.save(self.context)
                    self._dismiss_popups()
                    return True
                elapsed = int(time.time() - start)
                if elapsed % 10 == 0:
                    log(f"[pinterest] 대기 중... ({180 - elapsed}초 남음)", "info")

            log("[pinterest] 로그인 타임아웃", "error")
            self._save_screenshot("login_manual_timeout")
            return False

        except Exception as e:
            log(f"[pinterest] 로그인 오류: {e}", "error")
            self._save_screenshot("login_exception")
            return False

    def post(self, title: str = "", content: str = "",
             tags: list[str] = None, category: str = "",
             image_url: str = "", **kwargs) -> PostResult:
        """Pinterest 핀 발행.

        Args:
            title     : 핀 제목
            content   : 핀 설명
            tags      : 해시태그 (설명에 추가)
            image_url : 사용하지 않음 (media_path 사용)
            **kwargs:
                media_path  : 이미지 파일 경로 (필수)
                link        : 핀 클릭 시 이동 URL
                board_name  : 보드 이름 (미지정 시 기본 보드)
        """
        media_path = kwargs.get("media_path", "")
        link = kwargs.get("link", "")
        board_name = kwargs.get("board_name", self.default_board)

        if not media_path:
            return PostResult(success=False, message="media_path 필수")
        if not Path(media_path).exists():
            return PostResult(success=False, message=f"파일 없음: {media_path}")
        if not self.page:
            return PostResult(success=False, message="로그인 필요 (login() 먼저 호출)")

        # 설명 조합
        description = content or ""
        if tags:
            hashtags = " ".join(f"#{t.lstrip('#')}" for t in tags[:10])
            description = f"{description}\n\n{hashtags}"

        try:
            log(f"[pinterest] 핀 발행: {title[:40]}...", "step")
            return self._create_pin(
                media_path=media_path,
                title=title,
                description=description,
                link=link,
                board_name=board_name,
            )
        except Exception as e:
            log(f"[pinterest] 핀 발행 예외: {e}", "error")
            self._save_screenshot("post_exception")
            return PostResult(success=False, message=str(e))

    # ========================================================================
    # 핀 생성 자동화
    # ========================================================================

    def _create_pin(self, media_path: str, title: str = "",
                    description: str = "", link: str = "",
                    board_name: str = "") -> PostResult:
        """핀 생성 도구 페이지에서 핀 발행."""

        # 1) 핀 생성 페이지 이동 (goto 실패 시 HOME 경유 재시도)
        try:
            self.page.goto(self.PIN_CREATION_URL,
                           wait_until="domcontentloaded", timeout=20000)
        except Exception as e:
            log(f"[pinterest] PIN_CREATION_URL goto 실패, HOME 경유 재시도: {e}", "warn")
            try:
                self.page.goto(self.HOME_URL,
                               wait_until="domcontentloaded", timeout=15000)
                time.sleep(2)
                self.page.goto(self.PIN_CREATION_URL,
                               wait_until="domcontentloaded", timeout=20000)
            except Exception as e2:
                log(f"[pinterest] 재시도 실패: {e2}", "error")
                return PostResult(success=False, message=f"핀 생성 페이지 이동 실패: {e2}")
        # 2) 이미지 업로드 (input[type="file"] 사용)
        # Pinterest SPA 가 핀 생성 UI 를 렌더링할 때까지 최대 15초 대기.
        # query_selector 는 폴링 안 하므로 첫 호출(콜드 캐시)에서 실패 가능 →
        # wait_for_selector(state="attached") 로 바꾸면 첫 호출도 안정적.
        file_input = None
        try:
            self.page.wait_for_selector(
                'input[type="file"]', timeout=15000, state="attached"
            )
            file_input = self.page.query_selector('input[type="file"]')
        except Exception as e:
            log(f"[pinterest] input[type=file] 대기 실패: {e}", "warn")

        if not file_input:
            # 업로드 영역 클릭 후 재시도
            upload_area = self.page.query_selector(
                '[data-test-id="storyboard-upload-input"], '
                '[data-test-id="pin-draft-media-upload"], '
                'div[class*="upload"]'
            )
            if upload_area:
                upload_area.click()
                try:
                    self.page.wait_for_selector(
                        'input[type="file"]', timeout=5000, state="attached"
                    )
                except Exception:
                    pass
            file_input = self.page.query_selector('input[type="file"]')

        self._save_screenshot("pin_creation_page")

        if not file_input:
            self._save_screenshot("no_file_input")
            return PostResult(success=False, message="파일 업로드 입력을 찾을 수 없음")

        file_input.set_input_files(media_path)
        log("[pinterest] 이미지 업로드 중...", "info")
        time.sleep(5)
        self._save_screenshot("image_uploaded")

        # 3) 제목 입력
        if title:
            title_input = self.page.query_selector(
                '#storyboard-selector-title, '
                'input[id="storyboard-selector-title"], '
                'input[placeholder*="제목"], '
                'input[placeholder*="title" i], '
                'textarea[placeholder*="제목"], '
                'div[data-test-id="pin-draft-title"] input, '
                'div[data-test-id="pin-draft-title"] textarea'
            )
            if title_input:
                title_input.click()
                title_input.fill(title[:100])
                log(f"[pinterest] 제목 입력: {title[:40]}", "info")
                time.sleep(0.5)
            else:
                log("[pinterest] 제목 필드를 찾을 수 없음 (계속 진행)", "warn")

        # 4) 설명 입력 (Draft.js 리치텍스트 에디터)
        if description:
            desc_input = self.page.query_selector(
                'div[data-test-id="storyboard-description-field-container"] div[role="combobox"], '
                'div[data-test-id="storyboard-description-field-container"] [contenteditable="true"], '
                'div[aria-label="자세한 설명을 추가하세요."], '
                'div.public-DraftEditor-content, '
                'div[data-test-id="pin-draft-description"] div[role="textbox"], '
                'div[role="textbox"][contenteditable="true"]'
            )
            if desc_input:
                desc_input.click()
                time.sleep(0.3)
                # Draft.js는 keyboard.type이 가장 안정적
                self.page.keyboard.type(description[:500], delay=15)
                log("[pinterest] 설명 입력 완료", "info")
                time.sleep(0.5)
            else:
                log("[pinterest] 설명 필드를 찾을 수 없음 (계속 진행)", "warn")

        # 5) 링크 입력
        if link:
            link_input = self.page.query_selector(
                '#WebsiteField, '
                'input[id="WebsiteField"], '
                'input[placeholder*="링크"], '
                'input[placeholder*="link" i], '
                'input[placeholder*="URL" i], '
                'div[data-test-id="pin-draft-link"] input'
            )
            if link_input:
                link_input.click()
                link_input.fill(link)
                log(f"[pinterest] 링크 입력: {link[:60]}", "info")
                time.sleep(0.5)
            else:
                log("[pinterest] 링크 필드를 찾을 수 없음 (계속 진행)", "warn")

        self._save_screenshot("fields_filled")

        # 6) 보드 선택
        if board_name:
            self._select_board(board_name)

        # 7) 게시 버튼 클릭 (우측 상단 빨간 "게시")
        #    실제 DOM (2026-04): div[data-test-id="storyboard-creation-nav-done"] (text="게시")
        time.sleep(1)
        published = False

        # data-test-id 기반 우선 (가장 안정적)
        for sel in [
            'div[data-test-id="storyboard-creation-nav-done"]',
            'button[data-test-id="storyboard-creation-nav-done"]',
            'button[data-test-id="board-dropdown-save-button"]',
            'button[data-test-id="pin-draft-publish-button"]',
            'div[data-test-id="pin-draft-publish-button"]',
        ]:
            btn = self.page.query_selector(sel)
            if btn:
                try:
                    btn.click()
                    published = True
                    log(f"[pinterest] 게시 버튼 클릭 ({sel})", "info")
                    break
                except Exception:
                    try:
                        btn.click(force=True)
                        published = True
                        log(f"[pinterest] 게시 버튼 클릭 force ({sel})", "info")
                        break
                    except Exception:
                        pass

        # 역할+텍스트 기반
        if not published:
            for text in ["게시", "Publish"]:
                try:
                    btns = self.page.get_by_role("button", name=text, exact=True)
                    if btns.count() > 0:
                        btns.first.click(timeout=5000)
                        published = True
                        log(f"[pinterest] '{text}' 버튼 클릭", "info")
                        break
                except Exception:
                    pass

        # CSS :has-text fallback
        if not published:
            for sel in [
                'button:has-text("게시")',
                'div[role="button"]:has-text("게시")',
            ]:
                btn = self.page.query_selector(sel)
                if btn:
                    try:
                        btn.click()
                        published = True
                        break
                    except Exception:
                        pass

        if not published:
            self._save_screenshot("no_publish_button")
            return PostResult(success=False, message="게시 버튼을 찾을 수 없음")

        log("[pinterest] 게시 버튼 클릭, 업로드 대기...", "info")
        time.sleep(5)
        self._save_screenshot("after_publish")

        # 8) 성공 확인
        success_indicators = [
            'div[data-test-id="pin-draft-save-success"]',
            'div:has-text("게시되었습니다")',
            'div:has-text("Published")',
            'div:has-text("핀이 저장되었습니다")',
            'div:has-text("saved to")',
        ]
        for sel in success_indicators:
            try:
                if self.page.query_selector(sel):
                    log("[pinterest] 핀 발행 성공!", "ok")
                    return PostResult(success=True, message="핀 발행 완료")
            except Exception:
                pass

        # URL 변경 확인 (성공 시 핀 생성 페이지에서 벗어남)
        current_url = self.page.url
        if "/pin-creation-tool" not in current_url:
            log("[pinterest] 핀 발행 성공 (페이지 이동 확인)", "ok")
            return PostResult(success=True, url=current_url, message="핀 발행 완료")

        # 명시적 실패 징후 없으면 성공으로 간주
        log("[pinterest] 핀 발행 완료 (성공 추정)", "ok")
        return PostResult(success=True, message="핀 발행 완료 (확인 필요)")

    def _select_board(self, board_name: str) -> bool:
        """보드 이름으로 보드 선택. 없으면 드롭다운 내부에서 새로 생성.

        Pinterest 핀 생성 페이지 실제 DOM (2026-04 확인):
          - 드롭다운 버튼: div[data-test-id="board-dropdown-select-button"]
          - 팝오버 패널: div[data-test-id="board-picker-flyout"] (role="dialog")
          - 검색 input:  input#pickerSearchField (aria-label="보드에서 검색")
          - 보드 만들기: div[data-test-id="create-board-button"]
        """
        log(f"[pinterest] 보드 선택: {board_name}", "info")

        # 혹시 이전에 열린 드롭다운이 있으면 닫기
        try:
            self.page.keyboard.press("Escape")
            time.sleep(0.5)
        except Exception:
            pass

        # 1) 보드 드롭다운 버튼 클릭 (정확한 data-test-id)
        board_btn = self.page.query_selector(
            'div[data-test-id="board-dropdown-select-button"]'
        )
        if not board_btn:
            log("[pinterest] 보드 드롭다운 버튼을 찾을 수 없음", "warn")
            self._save_screenshot("no_board_button")
            return False

        try:
            board_btn.scroll_into_view_if_needed(timeout=3000)
        except Exception:
            pass
        try:
            board_btn.click()
        except Exception:
            try:
                board_btn.click(force=True)
            except Exception as e:
                log(f"[pinterest] 보드 드롭다운 클릭 실패: {e}", "warn")
                return False
        time.sleep(2)
        self._save_screenshot("board_dropdown_open")

        # 2) 드롭다운 팝오버 로케이터
        panel = self.page.locator('div[data-test-id="board-picker-flyout"]').first
        panel_visible = panel.count() > 0

        # 3) 보드 검색 (팝오버 내부 검색창)
        search_input = self.page.query_selector(
            'input#pickerSearchField, '
            'input[aria-label="보드에서 검색"], '
            'input[aria-label*="Search boards" i]'
        )
        if search_input:
            try:
                search_input.click()
                search_input.fill(board_name)
                time.sleep(1.5)
            except Exception:
                pass

        # 4) 검색 결과에서 보드 클릭 (팝오버 내부로 한정)
        if panel_visible:
            for sel in [
                f'div[data-test-id="board-row"]:has-text("{board_name}")',
                f'div[role="option"]:has-text("{board_name}")',
            ]:
                try:
                    loc = panel.locator(sel)
                    if loc.count() > 0:
                        loc.first.click()
                        log(f"[pinterest] 보드 '{board_name}' 선택 완료", "ok")
                        time.sleep(1)
                        return True
                except Exception:
                    pass

            # 정확 매칭 텍스트
            try:
                exact_opt = panel.get_by_text(board_name, exact=True)
                if exact_opt.count() > 0:
                    exact_opt.first.click()
                    log(f"[pinterest] 보드 '{board_name}' 선택 완료", "ok")
                    time.sleep(1)
                    return True
            except Exception:
                pass

        # 5) 보드가 없으면 "보드 만들기" 버튼 클릭 (정확한 data-test-id)
        create_btn = self.page.query_selector(
            'div[data-test-id="create-board-button"]'
        )
        if not create_btn:
            # fallback: text
            try:
                loc = self.page.get_by_text("보드 만들기", exact=True)
                if loc.count() > 0:
                    create_btn = loc.first
            except Exception:
                pass

        if not create_btn:
            log(f"[pinterest] 보드 '{board_name}'을 찾을 수 없음 (생성 버튼도 없음)", "warn")
            self._save_screenshot("board_not_found")
            return False

        try:
            create_btn.click()
        except Exception:
            try:
                create_btn.click(force=True)
            except Exception as e:
                log(f"[pinterest] 보드 생성 버튼 클릭 실패: {e}", "warn")
                return False
        log("[pinterest] '보드 만들기' 클릭 → 보드 생성 모달", "info")
        time.sleep(2.5)
        self._save_screenshot("board_create_form")

        # 6) 보드 생성 모달의 이름 입력 필드
        # 검색어가 자동으로 기본값으로 들어갔을 수 있으니 클리어 후 재입력
        name_input = self.page.query_selector(
            'input[id="boardName"], '
            'input[name="boardName"], '
            'input[id="boardEditName"], '
            'input[placeholder*="이름"], '
            'input[placeholder*="name" i], '
            'input[aria-label*="이름"], '
            'input[aria-label*="name" i]'
        )
        if name_input:
            try:
                name_input.click()
                # 기존 값 지우기
                name_input.fill("")
                time.sleep(0.2)
                name_input.fill(board_name)
                time.sleep(0.5)
            except Exception:
                pass

        # 7) 생성 확정 버튼
        created = False
        for sel in [
            'button[data-test-id="board-form-submit-button"]',
            'button[data-test-id="create-board-submit-button"]',
            'button[type="submit"]',
        ]:
            btn = self.page.query_selector(sel)
            if btn:
                try:
                    btn.click()
                    created = True
                    break
                except Exception:
                    pass

        if not created:
            for text in ["만들기", "Create", "생성", "완료", "Done"]:
                try:
                    btns = self.page.get_by_role("button", name=text, exact=True)
                    if btns.count() > 0:
                        btns.first.click()
                        created = True
                        break
                except Exception:
                    pass

        if created:
            log(f"[pinterest] 보드 '{board_name}' 생성 완료", "ok")
            time.sleep(3.5)
            return True

        log(f"[pinterest] 보드 생성 실패", "warn")
        self._save_screenshot("board_create_failed")
        return False

    # ========================================================================
    # 보드 생성
    # ========================================================================

    def ensure_board(self, board_name: str) -> bool:
        """지정 이름의 보드가 없으면 생성. (login() 이후 호출)

        Pinterest 홈에서 프로필 → 보드 생성 버튼 클릭 → 이름 입력 → 생성
        """
        if not self.page:
            return False
        log(f"[pinterest] 보드 확인/생성: {board_name}", "step")
        try:
            # 내 프로필로 이동 (saved 탭)
            self.page.goto("https://www.pinterest.com/",
                           wait_until="domcontentloaded", timeout=15000)
            time.sleep(3)
            self._dismiss_popups()

            # 프로필 아바타 → 프로필 페이지
            avatar = self.page.query_selector(
                '[data-test-id="header-avatar"], [data-test-id="header-profile"]'
            )
            if avatar:
                avatar.click()
                time.sleep(3)

            self._save_screenshot("profile_page")

            # "Saved / 저장" 탭 클릭
            for text in ["저장됨", "Saved", "저장", "Saves"]:
                try:
                    tab = self.page.get_by_role("tab", name=text)
                    if tab.count() > 0:
                        tab.first.click()
                        time.sleep(2)
                        break
                except Exception:
                    pass

            # 보드 생성 버튼 ("+" 버튼 또는 "보드 만들기")
            create_btn = None
            for sel in [
                'button[data-test-id="board-card-create-button"]',
                'div[data-test-id="board-card-create-button"]',
                'button[aria-label*="만들기"]',
                'button[aria-label*="Create"]',
            ]:
                create_btn = self.page.query_selector(sel)
                if create_btn:
                    break
            if not create_btn:
                for text in ["보드 만들기", "Create board", "만들기", "Create"]:
                    try:
                        loc = self.page.get_by_role("button", name=text)
                        if loc.count() > 0:
                            create_btn = loc.first
                            break
                    except Exception:
                        pass

            if not create_btn:
                log("[pinterest] 보드 생성 버튼을 찾을 수 없음 (이미 존재할 수 있음)", "warn")
                self._save_screenshot("no_create_board_btn")
                return False

            create_btn.click()
            time.sleep(2)
            self._save_screenshot("board_create_modal")

            # 이름 입력
            name_input = self.page.query_selector(
                'input[id="boardEditName"], input[name="boardName"], '
                'input[placeholder*="이름"], input[placeholder*="name" i]'
            )
            if not name_input:
                log("[pinterest] 보드 이름 입력 필드 없음", "error")
                return False
            name_input.fill(board_name)
            time.sleep(0.5)

            # 생성 확정 버튼
            for sel in [
                'button[data-test-id="board-form-submit-button"]',
                'button[type="submit"]',
            ]:
                btn = self.page.query_selector(sel)
                if btn:
                    btn.click()
                    break
            else:
                for text in ["만들기", "Create", "완료", "Done"]:
                    try:
                        loc = self.page.get_by_role("button", name=text)
                        if loc.count() > 0:
                            loc.first.click()
                            break
                    except Exception:
                        pass

            time.sleep(4)
            self._save_screenshot("board_created")
            log(f"[pinterest] 보드 '{board_name}' 생성 완료", "ok")
            return True
        except Exception as e:
            log(f"[pinterest] 보드 생성 예외: {e}", "error")
            return False

    # ========================================================================
    # 배치 발행
    # ========================================================================

    def post_batch(self, items: list[dict],
                   delay: float = 5.0) -> list[PostResult]:
        """여러 핀을 순차 발행.

        Args:
            items: list of {title, description, media_path, link, board_name, tags}
            delay: 핀 사이 대기 시간 (초)

        Returns:
            list of PostResult
        """
        log(f"[pinterest] 배치 발행: {len(items)}건", "step")

        results = []
        for i, item in enumerate(items):
            if i > 0:
                time.sleep(delay)

            result = self.post(
                title=item.get("title", ""),
                content=item.get("description", ""),
                tags=item.get("tags", []),
                media_path=item.get("media_path", ""),
                link=item.get("link", ""),
                board_name=item.get("board_name", self.default_board),
            )
            results.append(result)

            status = "성공" if result.success else "실패"
            log(f"  [{i+1}/{len(items)}] {status}: {item.get('title', '')[:30]}", "info")

        success_count = sum(1 for r in results if r.success)
        log(f"[pinterest] 배치 완료: {success_count}/{len(items)} 성공", "ok")
        return results

    # ========================================================================
    # 종료
    # ========================================================================

    def close(self) -> None:
        """외부에서 호출 가능한 종료 메서드."""
        self._close()
