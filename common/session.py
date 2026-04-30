"""
세션/쿠키 저장·로드 공통 모듈
- 기존 스크립트들의 pickle 기반 쿠키 관리 패턴 통합
- BaseSessionManager: requests/Playwright 공통 인터페이스 ABC
"""
import os
import pickle
import requests
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional
from .logger import log


COOKIE_DIR = Path(__file__).parent.parent / ".sessions"


class BaseSessionManager(ABC):
    """세션 관리자 공통 인터페이스.

    requests 기반(SessionManager)과 Playwright 기반(_IGSessionManager,
    _PinterestSessionManager) 모두 이 인터페이스를 구현한다.
    """

    @abstractmethod
    def save(self, *args, **kwargs) -> None:
        """현재 세션 상태를 영속 저장."""
        ...

    @abstractmethod
    def is_logged_in(self, *args, **kwargs) -> bool:
        """현재 세션이 유효한 로그인 상태인지 확인."""
        ...

    def get_storage_state(self) -> Optional[str]:
        """Playwright storage_state 경로 반환. requests 기반은 None."""
        return None


class SessionManager(BaseSessionManager):
    """requests.Session 래퍼 — 쿠키를 파일로 저장·복원."""

    def __init__(self, name: str):
        """
        Args:
            name: 플랫폼 식별자 (예: 'tistory', 'naver', 'twitter')
                  쿠키 파일명으로 사용됨
        """
        self.name = name
        self.cookie_path = COOKIE_DIR / f"{name}.pkl"
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            )
        })
        COOKIE_DIR.mkdir(exist_ok=True)

    def load(self) -> bool:
        """저장된 쿠키를 로드. 파일이 없으면 False 반환.

        저장 형식:
          - dict {name: value}: domain을 추론하여 설정
          - RequestsCookieJar: 그대로 update
        """
        if not self.cookie_path.exists():
            log(f"[{self.name}] 저장된 세션 없음", "warn")
            return False
        with open(self.cookie_path, "rb") as f:
            cookies = pickle.load(f)

        if isinstance(cookies, dict):
            # dict 형식: domain 정보가 없으므로 플랫폼별로 추론
            domains = self._guess_domains()
            for name, value in cookies.items():
                for domain in domains:
                    self.session.cookies.set(name, value, domain=domain)
        else:
            self.session.cookies.update(cookies)

        log(f"[{self.name}] 세션 로드 완료", "ok")
        return True

    def _guess_domains(self) -> list[str]:
        """플랫폼 이름으로부터 쿠키 domain 목록 추론."""
        if "naver" in self.name:
            return [".naver.com", "blog.naver.com", "cafe.naver.com"]
        if "tistory" in self.name:
            return [".tistory.com"]
        if "twitter" in self.name:
            return [".twitter.com", ".x.com"]
        return [""]

    def save(self) -> None:
        """현재 세션 쿠키를 파일에 저장. 중복 쿠키는 마지막 값으로 병합."""
        cookies = {}
        for cookie in self.session.cookies:
            cookies[cookie.name] = cookie.value
        with open(self.cookie_path, "wb") as f:
            pickle.dump(cookies, f)
        log(f"[{self.name}] 세션 저장 완료", "ok")

    def import_from_driver(self, driver) -> None:
        """Selenium WebDriver의 쿠키를 requests.Session에 임포트 후 저장."""
        for cookie in driver.get_cookies():
            self.session.cookies.set(cookie["name"], cookie["value"])
        self.save()
        log(f"[{self.name}] 브라우저 쿠키 임포트 완료", "ok")

    def delete(self) -> None:
        """저장된 쿠키 파일 삭제."""
        if self.cookie_path.exists():
            self.cookie_path.unlink()
            log(f"[{self.name}] 세션 파일 삭제", "warn")

    def is_logged_in(self, *args, **kwargs) -> bool:
        """저장된 쿠키 파일 존재 여부로 로그인 상태 판단."""
        return self.cookie_path.exists()

    def get(self, url: str, **kwargs):
        return self.session.get(url, **kwargs)

    def post(self, url: str, **kwargs):
        return self.session.post(url, **kwargs)
