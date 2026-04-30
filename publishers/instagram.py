"""
Instagram 자동 발행 Publisher - Playwright 기반
- 이미지 포스팅 (사진 + 캡션 + 해시태그)
- 릴스 포스팅 (동영상 + 캡션 + 해시태그)
- 쿠키 기반 자동 로그인 (첫 실행 시 수동 로그인)

참조: Instagram_Publishing/
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
IG_COOKIES_FILE = SESSION_DIR / "instagram_cookies.json"
IG_STORAGE_FILE = SESSION_DIR / "instagram_storage.json"


class _IGSessionManager:
    """Instagram Playwright 세션 관리 (내부용)."""

    def __init__(self):
        SESSION_DIR.mkdir(exist_ok=True)

    def save(self, context: BrowserContext) -> None:
        cookies = context.cookies()
        with open(IG_COOKIES_FILE, "w", encoding="utf-8") as f:
            json.dump(cookies, f, ensure_ascii=False, indent=2)
        storage = context.storage_state()
        with open(IG_STORAGE_FILE, "w", encoding="utf-8") as f:
            json.dump(storage, f, ensure_ascii=False, indent=2)
        log("[instagram] 세션 저장 완료", "ok")

    def get_storage_state(self) -> Optional[str]:
        if IG_STORAGE_FILE.exists():
            return str(IG_STORAGE_FILE)
        return None

    def is_logged_in(self, page: Page) -> bool:
        try:
            if "/accounts/login" in page.url:
                return False
            login_btn = page.query_selector('a[href="/accounts/login/"]')
            if login_btn:
                return False
            indicators = [
                'nav',
                'svg[aria-label="홈"]', 'svg[aria-label="Home"]',
                'a[href*="/direct/"]',
                'svg[aria-label="새로운 게시물"]', 'svg[aria-label="New post"]',
            ]
            for sel in indicators:
                if page.query_selector(sel):
                    return True
            return False
        except Exception:
            return False

    def clear(self) -> None:
        for f in [IG_COOKIES_FILE, IG_STORAGE_FILE]:
            if f.exists():
                f.unlink()
        log("[instagram] 세션 파일 삭제", "warn")


# ============================================================================
# Instagram Publisher
# ============================================================================

class InstagramPublisher(Publisher):
    """Instagram 자동 포스팅 발행기 (Playwright 기반)."""

    def __init__(self):
        self.username = os.getenv("INSTAGRAM_USERNAME", "")
        self.password = os.getenv("INSTAGRAM_PASSWORD", "")
        self.headless = os.getenv("INSTAGRAM_HEADLESS", "false").lower() == "true"
        self.debug = os.getenv("INSTAGRAM_DEBUG", "true").lower() == "true"

        self.session_mgr = _IGSessionManager()
        self.playwright = None
        self.browser: Optional[Browser] = None
        self.context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None

        self.screenshot_dir = Path(__file__).parent.parent / "screenshots" / "instagram"
        if self.debug:
            self.screenshot_dir.mkdir(parents=True, exist_ok=True)

    # ========================================================================
    # 초기화/종료
    # ========================================================================

    def _initialize(self) -> bool:
        try:
            self.playwright = sync_playwright().start()
            self.browser = self.playwright.chromium.launch(
                headless=self.headless,
                args=["--disable-blink-features=AutomationControlled", "--start-maximized"],
            )
            storage_state = self.session_mgr.get_storage_state()
            self.context = self.browser.new_context(
                storage_state=storage_state,
                viewport={"width": 1920, "height": 1080},
            )
            self.page = self.context.new_page()
            self.page.set_default_timeout(30000)
            log("[instagram] Playwright 초기화 완료", "ok")
            return True
        except Exception as e:
            log(f"[instagram] 초기화 오류: {e}", "error")
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
            log("[instagram] 브라우저 종료", "info")
        except Exception as e:
            log(f"[instagram] 종료 오류: {e}", "error")

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

    def _retry_click(self, selector: str, max_retries: int = 3, delay: float = 1.0) -> bool:
        for attempt in range(max_retries):
            try:
                el = self.page.query_selector(selector)
                if el:
                    el.scroll_into_view_if_needed()
                    time.sleep(0.5)
                    el.click()
                    return True
            except Exception as e:
                if attempt < max_retries - 1:
                    time.sleep(delay)
        return False

    def _click_by_text(self, text: str, role: str = "button", timeout: int = 10000) -> bool:
        try:
            locator = self.page.get_by_role(role, name=text)
            locator.wait_for(timeout=timeout)
            locator.click()
            return True
        except Exception:
            return False

    def _dismiss_popups(self) -> None:
        for _ in range(3):
            dismissed = False
            for text in ["나중에 하기", "Not Now", "나중에", "취소", "Cancel"]:
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

    def _click_new_post(self) -> bool:
        for sel in [
            'img[alt="새로운 게시물"]', 'svg[aria-label="새로운 게시물"]',
            'img[alt="New post"]', 'svg[aria-label="New post"]',
            'a:has(img[alt="새로운 게시물"])', 'a[href="/create/"]',
        ]:
            if self._retry_click(sel, max_retries=2):
                return True
        return False

    def _auto_login(self) -> bool:
        try:
            self.page.goto("https://www.instagram.com/accounts/login/",
                           wait_until="domcontentloaded", timeout=15000)
            time.sleep(3)

            self.page.wait_for_selector('input[name="username"]', timeout=10000).fill(self.username)
            time.sleep(0.5)
            self.page.wait_for_selector('input[name="password"]', timeout=5000).fill(self.password)
            time.sleep(0.5)
            self.page.wait_for_selector('button[type="submit"]', timeout=5000).click()
            log("[instagram] 로그인 버튼 클릭, 대기 중...", "info")

            for _ in range(15):
                time.sleep(1)
                if "login" not in self.page.url:
                    time.sleep(2)
                    self._dismiss_popups()
                    if self.session_mgr.is_logged_in(self.page):
                        log(f"[instagram] 자동 로그인 성공: {self.username}", "ok")
                        return True
                error_el = self.page.query_selector('[role="alert"]')
                if error_el:
                    log(f"[instagram] 로그인 오류: {error_el.inner_text()}", "error")
                    return False

            log("[instagram] 자동 로그인 타임아웃", "warn")
            return False
        except Exception as e:
            log(f"[instagram] 자동 로그인 예외: {e}", "error")
            return False

    # ========================================================================
    # Publisher 인터페이스
    # ========================================================================

    def login(self) -> bool:
        if not self._initialize():
            return False

        try:
            self.page.goto("https://www.instagram.com/", wait_until="domcontentloaded", timeout=15000)
            time.sleep(2)

            if self.session_mgr.is_logged_in(self.page):
                log("[instagram] 이미 로그인 상태", "ok")
                self._dismiss_popups()
                return True

            if self.username and self.password:
                if self._auto_login():
                    self.session_mgr.save(self.context)
                    self._dismiss_popups()
                    return True

            # 수동 로그인 대기
            log("[instagram] 수동 로그인 대기 (180초)...", "warn")
            start = time.time()
            while time.time() - start < 180:
                time.sleep(2)
                if self.session_mgr.is_logged_in(self.page):
                    log("[instagram] 로그인 완료!", "ok")
                    self.session_mgr.save(self.context)
                    self._dismiss_popups()
                    return True
                elapsed = int(time.time() - start)
                if elapsed % 10 == 0:
                    log(f"[instagram] 대기 중... ({180 - elapsed}초 남음)", "info")

            log("[instagram] 로그인 타임아웃", "error")
            return False

        except Exception as e:
            log(f"[instagram] 로그인 오류: {e}", "error")
            return False

    def post(self, title: str = "", content: str = "", tags: list = None,
             category: str = "", image_url: str = "", **kwargs) -> PostResult:
        """
        Instagram 포스팅.

        kwargs:
            media_type: "image" (기본) 또는 "reel"
            media_path: 미디어 파일 경로 (필수). list/tuple 이면 캐러셀.
        """
        media_type = kwargs.get("media_type", "image")
        media_path = kwargs.get("media_path", "")

        if not media_path:
            return PostResult(success=False, message="media_path 필수")

        caption = content or ""
        tags = tags or []

        if media_type == "reel":
            return self._post_reel(media_path, caption, tags)
        # 캐러셀 (다중 이미지) 자동 분기
        if isinstance(media_path, (list, tuple)):
            paths = [str(p) for p in media_path]
            if len(paths) == 0:
                return PostResult(success=False, message="media_path 비어있음")
            if len(paths) == 1:
                return self._post_image(paths[0], caption, tags)
            return self._post_carousel(paths, caption, tags)
        return self._post_image(media_path, caption, tags)

    # ========================================================================
    # 이미지 포스팅
    # ========================================================================

    def _post_image(self, image_path: str, caption: str = "", tags: list = None) -> PostResult:
        if not self.page:
            return PostResult(success=False, message="초기화 필요")
        if not Path(image_path).exists():
            return PostResult(success=False, message=f"파일 없음: {image_path}")

        try:
            log(f"[instagram] 이미지 포스팅: {image_path}", "step")
            self.page.goto("https://www.instagram.com/", wait_until="domcontentloaded", timeout=15000)
            time.sleep(2)

            if not self._click_new_post():
                return PostResult(success=False, message="새 게시물 버튼 못 찾음")
            time.sleep(2)

            file_input = self.page.query_selector('input[type="file"]')
            if not file_input:
                return PostResult(success=False, message="파일 입력 없음")
            file_input.set_input_files(image_path)
            time.sleep(3)

            if not (self._click_by_text("다음") or self._click_by_text("Next")):
                return PostResult(success=False, message="첫 번째 다음 버튼 못 찾음")
            time.sleep(2)

            if not (self._click_by_text("다음") or self._click_by_text("Next")):
                return PostResult(success=False, message="두 번째 다음 버튼 못 찾음")
            time.sleep(2)

            # 캡션 입력
            caption_text = caption
            if tags:
                hashtags = " ".join(f"#{t}" if not t.startswith("#") else t for t in tags)
                caption_text = f"{caption}\n\n{hashtags}"

            for sel in ['textarea[aria-label*="캡션" i]', 'textarea', 'div[role="textbox"]']:
                el = self.page.query_selector(sel)
                if el:
                    el.click()
                    time.sleep(0.5)
                    el.type(caption_text, delay=50)
                    break
            time.sleep(1)

            if not (self._click_by_text("공유") or self._click_by_text("Share")):
                return PostResult(success=False, message="공유 버튼 못 찾음")
            time.sleep(5)

            log("[instagram] 이미지 포스팅 성공!", "ok")
            self._save_screenshot("post_image_success")
            return PostResult(success=True, message="이미지 포스팅 완료")

        except Exception as e:
            log(f"[instagram] 이미지 포스팅 오류: {e}", "error")
            self._save_screenshot("post_image_error")
            return PostResult(success=False, message=str(e))

    # ========================================================================
    # 캐러셀 (다중 이미지) 포스팅
    # ========================================================================

    def _post_carousel(self, image_paths: list, caption: str = "",
                       tags: list = None) -> PostResult:
        """여러 이미지를 한 게시물로 캐러셀 발행 (최대 10장)."""
        if not self.page:
            return PostResult(success=False, message="초기화 필요")
        # 존재 여부 검증 + 최대 10장 제한 (Instagram 제약)
        valid_paths = [p for p in image_paths if Path(p).exists()]
        if not valid_paths:
            return PostResult(success=False, message="유효한 이미지 없음")
        if len(valid_paths) > 10:
            log(f"[instagram] 캐러셀 10장 초과 → {len(valid_paths)} → 10장으로 제한", "warn")
            valid_paths = valid_paths[:10]

        try:
            log(f"[instagram] 캐러셀 포스팅 시작: {len(valid_paths)}장", "step")
            self.page.goto("https://www.instagram.com/", wait_until="domcontentloaded", timeout=15000)
            time.sleep(2)

            if not self._click_new_post():
                return PostResult(success=False, message="새 게시물 버튼 못 찾음")
            time.sleep(2)

            # 첫 파일 업로드 (input[type="file"])
            file_input = self.page.query_selector('input[type="file"]')
            if not file_input:
                return PostResult(success=False, message="파일 입력 없음")
            # 한 번에 모든 파일을 multiple로 업로드 시도
            file_input.set_input_files(valid_paths)
            time.sleep(4)

            # 다음 (자르기 → 편집 → 캡션) 단계 진행
            if not (self._click_by_text("다음") or self._click_by_text("Next")):
                return PostResult(success=False, message="첫 번째 다음 버튼 못 찾음")
            time.sleep(2)

            if not (self._click_by_text("다음") or self._click_by_text("Next")):
                return PostResult(success=False, message="두 번째 다음 버튼 못 찾음")
            time.sleep(2)

            # 캡션 입력
            caption_text = caption
            if tags:
                hashtags = " ".join(f"#{t}" if not t.startswith("#") else t for t in tags)
                caption_text = f"{caption}\n\n{hashtags}"

            for sel in ['textarea[aria-label*="캡션" i]', 'textarea', 'div[role="textbox"]']:
                el = self.page.query_selector(sel)
                if el:
                    el.click()
                    time.sleep(0.5)
                    el.type(caption_text, delay=50)
                    break
            time.sleep(1)

            if not (self._click_by_text("공유") or self._click_by_text("Share")):
                return PostResult(success=False, message="공유 버튼 못 찾음")
            time.sleep(6)

            log(f"[instagram] 캐러셀 포스팅 성공! ({len(valid_paths)}장)", "ok")
            self._save_screenshot("post_carousel_success")
            return PostResult(success=True,
                              message=f"캐러셀 포스팅 완료 ({len(valid_paths)}장)")

        except Exception as e:
            log(f"[instagram] 캐러셀 포스팅 오류: {e}", "error")
            self._save_screenshot("post_carousel_error")
            return PostResult(success=False, message=str(e))

    # ========================================================================
    # 릴스 포스팅
    # ========================================================================

    def _post_reel(self, video_path: str, caption: str = "", tags: list = None) -> PostResult:
        if not self.page:
            return PostResult(success=False, message="초기화 필요")
        if not Path(video_path).exists():
            return PostResult(success=False, message=f"파일 없음: {video_path}")

        try:
            log(f"[instagram] 릴스 포스팅: {video_path}", "step")
            self.page.goto("https://www.instagram.com/", wait_until="domcontentloaded", timeout=15000)
            time.sleep(2)

            if not self._click_new_post():
                return PostResult(success=False, message="새 게시물 버튼 못 찾음")
            time.sleep(2)

            file_input = self.page.query_selector('input[type="file"]')
            if not file_input:
                return PostResult(success=False, message="파일 입력 없음")
            file_input.set_input_files(video_path)
            time.sleep(5)

            if not (self._click_by_text("다음") or self._click_by_text("Next")):
                return PostResult(success=False, message="다음 버튼 못 찾음")
            time.sleep(2)

            # 캡션 입력
            caption_text = caption
            if tags:
                hashtags = " ".join(f"#{t}" if not t.startswith("#") else t for t in tags)
                caption_text = f"{caption}\n\n{hashtags}"

            for sel in ['textarea[aria-label*="캡션" i]', 'textarea', 'div[role="textbox"]']:
                el = self.page.query_selector(sel)
                if el:
                    el.click()
                    time.sleep(0.5)
                    el.type(caption_text, delay=50)
                    break
            time.sleep(1)

            if not (self._click_by_text("공유") or self._click_by_text("Share")):
                return PostResult(success=False, message="공유 버튼 못 찾음")
            time.sleep(5)

            log("[instagram] 릴스 포스팅 성공!", "ok")
            self._save_screenshot("post_reel_success")
            return PostResult(success=True, message="릴스 포스팅 완료")

        except Exception as e:
            log(f"[instagram] 릴스 포스팅 오류: {e}", "error")
            self._save_screenshot("post_reel_error")
            return PostResult(success=False, message=str(e))

    def close(self) -> None:
        """외부에서 호출 가능한 종료 메서드."""
        self._close()
