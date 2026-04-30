"""
추상 Publisher 베이스 클래스
- 모든 플랫폼 publisher가 이 인터페이스를 구현
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional


@dataclass
class PostResult:
    """포스트 발행 결과.

    필드:
        success:  발행 성공 여부
        url:      발행된 글의 URL
        post_id:  플랫폼 내부 포스트 ID
        message:  성공/실패 상세 메시지 (오류 내용 포함)

    주의: 'error' 속성은 존재하지 않습니다. result.message 를 사용하세요.
    """
    success:  bool
    url:      str = ""
    post_id:  str = ""
    message:  str = ""

    def __getattr__(self, name: str):
        if name == "error":
            raise AttributeError(
                "PostResult에 'error' 속성이 없습니다. "
                "result.message 를 사용하세요."
            )
        raise AttributeError(f"PostResult에 '{name}' 속성이 없습니다.")


class Publisher(ABC):
    """플랫폼 발행기 추상 베이스."""

    def login(self) -> bool:
        """로그인. 기본 구현은 항상 True (토큰/쿠키 방식 퍼블리셔용).

        세션 쿠키나 별도 인증이 필요한 퍼블리셔는 이 메서드를 오버라이드한다.
        """
        return True

    @abstractmethod
    def post(self,
             title:     str,
             content:   str,
             tags:      Optional[list[str]] = None,
             category:  str                 = "",
             image_url: str                 = "",
             **kwargs) -> PostResult:
        """글 발행.

        Args:
            title:     제목
            content:   본문 (HTML 또는 텍스트)
            tags:      태그 목록
            category:  카테고리명
            image_url: 대표 이미지 URL (있으면 다운로드 후 업로드)

        Returns:
            PostResult
        """
        ...

    def upload_image(self, local_path: str) -> str:
        """이미지 업로드 후 URL 반환. 플랫폼마다 오버라이드."""
        return ""

    def get_categories(self) -> list[dict]:
        """카테고리 목록 반환. 플랫폼마다 오버라이드."""
        return []
