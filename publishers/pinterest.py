"""
Pinterest 자동 발행 Publisher

Pinterest API v5 (api.pinterest.com/v5) 사용 — requests HTTP 방식.
- OAuth 2.0 액세스 토큰 기반
- 핀(Pin) 생성: 이미지 URL + 제목 + 설명 + 링크
- 보드 목록 조회
- 기존 old_source의 pinterest(api) 코드를 현행 Publisher 구조로 리팩토링

환경변수:
    PINTEREST_ACCESS_TOKEN : OAuth 액세스 토큰
    PINTEREST_BOARD_ID     : 발행할 보드 ID

토큰 발급 방법:
    1. https://developers.pinterest.com/apps/ 에서 앱 생성
    2. OAuth 2.0 flow로 access_token 발급
    3. .env 파일에 PINTEREST_ACCESS_TOKEN 저장
"""
import os
import time
from typing import Optional

import requests

from common.logger import log
from .base import Publisher, PostResult


API_BASE = "https://api.pinterest.com/v5"


class PinterestPublisher(Publisher):
    """Pinterest API v5 발행기 (requests HTTP 방식)."""

    def __init__(self,
                 access_token: Optional[str] = None,
                 board_id: Optional[str] = None):
        self.access_token = access_token or os.getenv("PINTEREST_ACCESS_TOKEN", "")
        self.board_id = board_id or os.getenv("PINTEREST_BOARD_ID", "")

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
        }

    # ------------------------------------------------------------------
    # Publisher interface
    # ------------------------------------------------------------------

    def login(self) -> bool:
        """토큰 유효성 확인."""
        if not self.access_token:
            log("PINTEREST_ACCESS_TOKEN 미설정", "error")
            return False

        try:
            resp = requests.get(
                f"{API_BASE}/user_account",
                headers=self._headers(),
                timeout=10,
            )
            if resp.ok:
                data = resp.json()
                username = data.get("username", "unknown")
                log(f"Pinterest 로그인 성공: @{username}", "ok")
                return True
            log(f"Pinterest 토큰 검증 실패 ({resp.status_code}): {resp.text[:200]}", "error")
            return False
        except Exception as e:
            log(f"Pinterest 로그인 예외: {e}", "error")
            return False

    def post(self, title: str, content: str,
             tags: list[str] = None, category: str = "",
             image_url: str = "", **kwargs) -> PostResult:
        """Pinterest 핀 발행.

        Args:
            title     : 핀 제목 (최대 100자)
            content   : 핀 설명 (최대 500자)
            tags      : 해시태그 목록 (설명에 추가)
            image_url : 이미지 URL (필수)
            **kwargs:
                board_id : 보드 ID (미지정 시 기본 보드)
                link     : 핀 클릭 시 이동 URL
                alt_text : 이미지 대체 텍스트
        """
        board_id = kwargs.get("board_id", self.board_id)
        link = kwargs.get("link", "")
        alt_text = kwargs.get("alt_text", "")

        if not board_id:
            log("Pinterest board_id 미설정", "error")
            return PostResult(success=False, message="board_id 필요")

        if not image_url:
            log("Pinterest 핀 발행에는 image_url 필수", "error")
            return PostResult(success=False, message="image_url 필요")

        # 설명 조합
        description = content or ""
        if tags:
            hashtags = " ".join(f"#{t.lstrip('#')}" for t in tags[:10])
            description = f"{description}\n\n{hashtags}"
        if len(description) > 500:
            description = description[:497] + "..."

        # 제목 제한
        pin_title = title[:100] if title else ""

        log(f"Pinterest 핀 발행: {pin_title[:40]}...", "step")

        payload = {
            "title": pin_title,
            "description": description,
            "board_id": board_id,
            "media_source": {
                "source_type": "image_url",
                "url": image_url,
            },
        }

        if link:
            payload["link"] = link
        if alt_text:
            payload["alt_text"] = alt_text

        try:
            resp = requests.post(
                f"{API_BASE}/pins",
                json=payload,
                headers=self._headers(),
                timeout=15,
            )

            if resp.ok:
                data = resp.json()
                pin_id = data.get("id", "")
                pin_url = f"https://www.pinterest.com/pin/{pin_id}/" if pin_id else ""
                log(f"Pinterest 핀 발행 성공: {pin_url}", "ok")
                return PostResult(success=True, url=pin_url, post_id=pin_id)

            log(f"Pinterest 핀 발행 실패 ({resp.status_code}): {resp.text[:300]}", "error")
            return PostResult(success=False, message=resp.text[:300])
        except Exception as e:
            log(f"Pinterest 핀 발행 예외: {e}", "error")
            return PostResult(success=False, message=str(e))

    # ------------------------------------------------------------------
    # 보드 관리
    # ------------------------------------------------------------------

    def get_boards(self) -> list[dict]:
        """내 보드 목록 조회.

        Returns:
            list of {id, name, description, pin_count, url}
        """
        log("Pinterest 보드 목록 조회", "step")
        try:
            resp = requests.get(
                f"{API_BASE}/boards",
                headers=self._headers(),
                timeout=10,
            )
            if not resp.ok:
                log(f"보드 조회 실패 ({resp.status_code}): {resp.text[:200]}", "error")
                return []

            data = resp.json()
            boards = []
            for item in data.get("items", []):
                boards.append({
                    "id": item.get("id", ""),
                    "name": item.get("name", ""),
                    "description": item.get("description", ""),
                    "pin_count": item.get("pin_count", 0),
                    "url": f"https://www.pinterest.com/board/{item.get('id', '')}/",
                })

            log(f"보드 {len(boards)}개 조회 완료", "ok")
            return boards
        except Exception as e:
            log(f"보드 조회 예외: {e}", "error")
            return []

    def get_categories(self) -> list[dict]:
        """보드 목록을 카테고리로 반환."""
        return self.get_boards()

    # ------------------------------------------------------------------
    # 배치 발행 (쿠팡/핫딜 등 여러 상품 한번에)
    # ------------------------------------------------------------------

    def post_batch(self, items: list[dict],
                   board_id: str = "",
                   delay: float = 3.0) -> list[PostResult]:
        """여러 핀을 순차 발행.

        Args:
            items: list of {title, description, image_url, link}
            board_id: 보드 ID (미지정 시 기본 보드)
            delay: 핀 사이 대기 시간 (초)

        Returns:
            list of PostResult
        """
        bid = board_id or self.board_id
        log(f"Pinterest 배치 발행: {len(items)}건 → 보드 {bid}", "step")

        results = []
        for i, item in enumerate(items):
            if i > 0:
                time.sleep(delay)

            result = self.post(
                title=item.get("title", ""),
                content=item.get("description", ""),
                image_url=item.get("image_url", ""),
                link=item.get("link", ""),
                board_id=bid,
                tags=item.get("tags", []),
            )
            results.append(result)

            status = "성공" if result.success else "실패"
            log(f"  [{i+1}/{len(items)}] {status}: {item.get('title', '')[:30]}", "info")

        success_count = sum(1 for r in results if r.success)
        log(f"Pinterest 배치 완료: {success_count}/{len(items)} 성공", "ok")
        return results

    # ------------------------------------------------------------------
    # 토큰 갱신
    # ------------------------------------------------------------------

    def refresh_token(self, client_id: str = "",
                      client_secret: str = "",
                      refresh_token: str = "") -> Optional[str]:
        """리프레시 토큰으로 액세스 토큰 갱신.

        Args:
            client_id: 앱 ID (미지정 시 환경변수)
            client_secret: 앱 시크릿 (미지정 시 환경변수)
            refresh_token: 리프레시 토큰 (미지정 시 환경변수)

        Returns:
            새 액세스 토큰, 실패 시 None
        """
        cid = client_id or os.getenv("PINTEREST_CLIENT_ID", "")
        csecret = client_secret or os.getenv("PINTEREST_CLIENT_SECRET", "")
        rtoken = refresh_token or os.getenv("PINTEREST_REFRESH_TOKEN", "")

        if not all([cid, csecret, rtoken]):
            log("Pinterest 토큰 갱신 정보 부족", "error")
            return None

        try:
            resp = requests.post(
                f"{API_BASE}/oauth/token",
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": rtoken,
                    "client_id": cid,
                    "client_secret": csecret,
                },
                timeout=15,
            )

            if resp.ok:
                data = resp.json()
                new_token = data.get("access_token", "")
                self.access_token = new_token
                log("Pinterest 토큰 갱신 성공", "ok")
                return new_token

            log(f"Pinterest 토큰 갱신 실패: {resp.text[:200]}", "error")
            return None
        except Exception as e:
            log(f"Pinterest 토큰 갱신 예외: {e}", "error")
            return None
