"""
Meta Threads 자동 발행 Publisher

Meta 공식 Threads API (graph.threads.net/v1.0) 사용.
- 장기 액세스 토큰 기반 (만료 60일, 갱신 가능)
- TEXT / IMAGE 포스트 지원
- 2단계: 컨테이너 생성 → 발행

환경변수:
    THREADS_USER_ID       : Threads 사용자 ID (숫자)
    THREADS_ACCESS_TOKEN  : 장기 액세스 토큰
"""
import os
import time
import urllib.parse
from typing import Optional

import requests

from common.logger import log
from .base import Publisher, PostResult


GRAPH_BASE = "https://graph.threads.net/v1.0"


class ThreadsPublisher(Publisher):
    """Meta Threads 공식 API 발행기."""

    def __init__(self,
                 user_id: Optional[str] = None,
                 access_token: Optional[str] = None):
        self.user_id      = user_id      or os.getenv("THREADS_USER_ID", "")
        self.access_token = access_token or os.getenv("THREADS_ACCESS_TOKEN", "")

    # ------------------------------------------------------------------
    # Publisher interface
    # ------------------------------------------------------------------

    def login(self) -> bool:
        """환경변수에서 자격증명 로드 확인."""
        if not self.user_id or not self.access_token:
            log("THREADS_USER_ID 또는 THREADS_ACCESS_TOKEN 미설정", "error")
            return False
        log(f"Threads API 준비 (user_id={self.user_id[:6]}...)", "ok")
        return True

    def post(self, title: str, content: str,
             tags: list[str] = None, category: str = "",
             image_url: str = "", **kwargs) -> PostResult:
        """Threads 포스트 발행.

        Args:
            title    : 제목 (content 앞에 붙임)
            content  : 본문
            tags     : 해시태그 목록
            image_url: 이미지 URL (있으면 IMAGE 타입으로 발행)
        """
        # 본문 조합
        text = f"{title}\n\n{content}" if title else content

        # 해시태그 추가
        if tags:
            hashtags = " ".join(
                f"#{t.lstrip('#')}" for t in tags[:5]
            )
            text = f"{text}\n\n{hashtags}"

        # Threads 글자 수 제한 500자
        if len(text) > 500:
            text = text[:497] + "..."

        log(f"Threads 발행 준비: {text[:60]}...", "step")

        # 미디어 타입
        media_type = "IMAGE" if image_url else "TEXT"

        # Step 1: 컨테이너 생성
        container_id = self._create_container(text, media_type, image_url)
        if not container_id:
            return PostResult(success=False, message="컨테이너 생성 실패")

        # Step 2: 잠시 대기 (API 권장)
        time.sleep(2)

        # Step 3: 발행
        return self._publish_container(container_id)

    def upload_image(self, local_path: str) -> str:
        """로컬 이미지 업로드 미지원 — 공개 URL 직접 전달 방식 사용."""
        log("Threads는 공개 이미지 URL 직접 전달 방식 사용", "warn")
        return ""

    def get_categories(self) -> list[str]:
        return []

    # ------------------------------------------------------------------
    # Threads API 내부 메서드
    # ------------------------------------------------------------------

    def _create_container(self, text: str, media_type: str,
                           image_url: str = "") -> Optional[str]:
        """Step 1: 미디어 컨테이너 생성.

        POST /v1.0/{user_id}/threads
        """
        url = f"{GRAPH_BASE}/{self.user_id}/threads"
        params: dict = {
            "media_type":   media_type,
            "text":         text,
            "access_token": self.access_token,
        }
        if media_type == "IMAGE" and image_url:
            params["image_url"] = image_url

        try:
            resp = requests.post(url, params=params, timeout=15)
            if resp.ok:
                container_id = resp.json().get("id", "")
                log(f"Threads 컨테이너 생성 완료: {container_id}", "ok")
                return container_id
            log(f"Threads 컨테이너 생성 실패 ({resp.status_code}): {resp.text[:200]}", "error")
            return None
        except Exception as e:
            log(f"Threads 컨테이너 생성 예외: {e}", "error")
            return None

    def _publish_container(self, container_id: str) -> PostResult:
        """Step 2: 컨테이너 발행.

        POST /v1.0/{user_id}/threads_publish
        """
        url = f"{GRAPH_BASE}/{self.user_id}/threads_publish"
        params = {
            "creation_id":  container_id,
            "access_token": self.access_token,
        }
        try:
            resp = requests.post(url, params=params, timeout=15)
            if resp.ok:
                post_id  = resp.json().get("id", "")
                post_url = f"https://www.threads.net/t/{post_id}" if post_id else ""
                log(f"Threads 발행 성공: {post_url}", "ok")
                return PostResult(success=True, url=post_url, post_id=post_id)
            log(f"Threads 발행 실패 ({resp.status_code}): {resp.text[:200]}", "error")
            return PostResult(success=False, message=resp.text[:200])
        except Exception as e:
            log(f"Threads 발행 예외: {e}", "error")
            return PostResult(success=False, message=str(e))

    # ------------------------------------------------------------------
    # 토큰 갱신 (장기 토큰은 60일 유효, 매달 갱신 필요)
    # ------------------------------------------------------------------

    def refresh_token(self) -> Optional[str]:
        """장기 액세스 토큰 갱신 후 .env 자동 저장.

        common.threads_token 모듈에 위임.
        Returns:
            새 토큰 문자열, 실패 시 None
        """
        from common.threads_token import refresh_long_lived_token
        new_token = refresh_long_lived_token(save=True)
        if new_token:
            self.access_token = new_token
            log(f"Threads 토큰 갱신 완료 (.env 저장됨)", "ok")
        return new_token

    def get_profile(self) -> dict:
        """내 프로필 조회 (연결 테스트용).

        Returns:
            {'id': ..., 'name': ..., 'threads_profile_picture_url': ..., ...}
        """
        url = f"{GRAPH_BASE}/me"
        params = {
            "fields":       "id,name,threads_profile_picture_url,threads_biography",
            "access_token": self.access_token,
        }
        try:
            resp = requests.get(url, params=params, timeout=10)
            if resp.ok:
                data = resp.json()
                log(f"Threads 프로필: {data.get('name')} (id={data.get('id')})", "ok")
                return data
            log(f"Threads 프로필 조회 실패: {resp.text[:200]}", "error")
            return {}
        except Exception as e:
            log(f"Threads 프로필 조회 예외: {e}", "error")
            return {}
