"""
영속 브라우저 프로필 공통 모듈.

각 플랫폼이 launch_persistent_context 로 Chromium 을 기동해
.sessions/<name>_profile/ 에 쿠키·로컬스토리지를 영구 보존한다.

흐름:
  1. ensure_session(validator, login_fn, ...) 호출
  2. persistent context launch (headless=True) → validator 로 기존 세션 유효성 검증
  3. 유효하면 즉시 반환
  4. 만료 시 자동 로그인 시도 (login_fn)
  5. 자동 실패 + 대화형 허용 환경이면 headless=False 로 재띄움 → 사용자 수동 로그인
  6. 쿠키는 BrowserContext 가 user_data_dir 에 알아서 기록

대화형 판정:
  .env 의 BROWSER_INTERACTIVE=true 또는 sys.stdin.isatty() 이면 True.
  스케줄러에서 호출되면 stdin 이 pipe 라 False → 자동 경로 실패 시 조용히 False 반환.
"""
from __future__ import annotations

import os
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Iterator, Optional

from common.logger import log

try:
    from playwright.sync_api import BrowserContext, sync_playwright
    HAS_PLAYWRIGHT = True
except ImportError:
    HAS_PLAYWRIGHT = False


PROFILE_DIR_ROOT = Path(__file__).parent.parent / ".sessions"
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


def _is_interactive() -> bool:
    """사용자가 터미널 앞에 있어 수동 로그인 가능한 환경인지 판단."""
    if os.getenv("BROWSER_INTERACTIVE", "").lower() == "true":
        return True
    if os.getenv("BROWSER_INTERACTIVE", "").lower() == "false":
        return False
    try:
        return sys.stdin.isatty()
    except Exception:
        return False


class PersistentBrowserProfile:
    """플랫폼별 영속 브라우저 프로필.

    Args:
        name: 프로필 식별자 (예: 'newspick', 'tistory_<blog_id>')
              .sessions/<name>_profile/ 디렉토리가 user_data_dir 로 사용됨
        user_agent: 기본 UA 오버라이드
        locale: 기본 'ko-KR'
    """

    def __init__(
        self,
        name: str,
        *,
        user_agent: str = DEFAULT_USER_AGENT,
        locale: str = "ko-KR",
    ):
        self.name = name
        self.user_data_dir = PROFILE_DIR_ROOT / f"{name}_profile"
        self.user_agent = user_agent
        self.locale = locale
        PROFILE_DIR_ROOT.mkdir(exist_ok=True)

    @contextmanager
    def launch(self, *, headless: bool = True) -> Iterator[BrowserContext]:
        """persistent context 를 띄운다. 컨텍스트 매니저로 정리까지 보장.

        Chromium 이 user_data_dir 에 쿠키를 sqlite 로 기록하므로
        블록 종료 시 context.close() 로 flush 된다.
        """
        if not HAS_PLAYWRIGHT:
            raise RuntimeError("playwright 미설치: pip install playwright && playwright install chromium")

        self.user_data_dir.mkdir(parents=True, exist_ok=True)
        with sync_playwright() as p:
            context = p.chromium.launch_persistent_context(
                user_data_dir=str(self.user_data_dir),
                headless=headless,
                user_agent=self.user_agent,
                locale=self.locale,
                args=["--disable-blink-features=AutomationControlled"],
            )
            try:
                yield context
            finally:
                try:
                    context.close()
                except Exception:
                    pass

    def ensure_session(
        self,
        *,
        validator: Callable[[BrowserContext], bool],
        login_fn: Optional[Callable[[BrowserContext], bool]] = None,
        login_success_cookie: Optional[str] = None,
        allow_interactive: bool = True,
    ) -> bool:
        """세션 유효성 확보. 만료 시 자동 로그인 → (옵션) 수동 로그인.

        Args:
            validator:           context 받아 세션 유효 여부 반환
            login_fn:            자동 로그인 실행 함수 (context → bool).
                                  성공/실패만 반환; 실제 검증은 이 함수가 다시 validator 돌림
            login_success_cookie: 로그인 성공 감지용 쿠키 이름 (headless=False fallback 에서 사용)
            allow_interactive:   True 면 자동 실패 시 headless=False 로 재시도

        Returns:
            최종적으로 세션이 유효하면 True
        """
        # 1. headless 로 persistent context 띄워서 기존 세션 점검
        log(f"[{self.name}] persistent profile 점검: {self.user_data_dir}", "step")
        with self.launch(headless=True) as context:
            if validator(context):
                log(f"[{self.name}] 기존 세션 유효", "ok")
                return True
            log(f"[{self.name}] 세션 없음/만료 — 자동 로그인 시도", "warn")

            if login_fn and login_fn(context):
                # 자동 로그인 성공 후 재검증
                if validator(context):
                    log(f"[{self.name}] 자동 로그인 성공", "ok")
                    return True
                log(f"[{self.name}] 자동 로그인 후 검증 실패", "warn")

        # 2. 자동 로그인 실패 → 대화형 fallback
        if not allow_interactive or not _is_interactive():
            log(f"[{self.name}] 비대화형 환경 — 수동 로그인 생략, 실패 반환", "error")
            return False

        log(f"[{self.name}] headless=False 로 재띄움 — 브라우저에서 수동 로그인", "step")
        with self.launch(headless=False) as context:
            if login_fn:
                # 자동 로그인 시도 후 폼이 떠 있으면 사용자가 CAPTCHA/2FA 처리
                try:
                    login_fn(context)
                except Exception as e:
                    log(f"[{self.name}] 수동 모드 자동 입력 실패: {e}", "warn")

            # SESSION 쿠키 등장 폴링 (최대 5분)
            deadline = time.time() + 300
            while time.time() < deadline:
                if validator(context):
                    log(f"[{self.name}] 수동 로그인 성공 감지", "ok")
                    return True
                if login_success_cookie:
                    names = {c["name"] for c in context.cookies()}
                    if login_success_cookie in names and validator(context):
                        log(f"[{self.name}] 수동 로그인 성공 감지 (쿠키 기반)", "ok")
                        return True
                time.sleep(2)

            log(f"[{self.name}] 수동 로그인 시간 초과 (5분)", "error")
            return False

    def extract_cookies(self, urls: Optional[list[str]] = None) -> list[dict]:
        """현재 persistent profile 에 저장된 쿠키를 읽어 반환.

        headless 로 순간 기동해서 context.cookies() 로 수집 후 종료.
        requests.Session 에 주입하고 싶은 경우 사용.
        """
        with self.launch(headless=True) as context:
            if urls:
                return context.cookies(urls)
            return context.cookies()

    def inject_into_requests(self, session, urls: Optional[list[str]] = None) -> int:
        """persistent profile 의 쿠키를 requests.Session 에 주입.

        Returns:
            주입된 쿠키 개수
        """
        cookies = self.extract_cookies(urls)
        for c in cookies:
            session.cookies.set(
                c["name"], c["value"],
                domain=c.get("domain", "").lstrip("."),
                path=c.get("path", "/"),
            )
        return len(cookies)
