"""
카카오맵 로컬 검색 소스

카카오 로컬 REST API 를 이용해 키워드/카테고리로 장소를 검색한다.
블로그 포스트 소재 (지역 맛집/카페/관광지 TOP10 등) 로 활용.

API 문서: https://developers.kakao.com/docs/latest/ko/local/dev-guide

환경변수:
    KAKAO_REST_API_KEY   카카오 Developers 앱의 REST API 키 (필수)
"""
import os
from typing import Optional

import requests

from common.logger import log


_API_KEY = os.getenv("KAKAO_REST_API_KEY", "")
_SEARCH_URL   = "https://dapi.kakao.com/v2/local/search/keyword.json"
_CATEGORY_URL = "https://dapi.kakao.com/v2/local/search/category.json"


# 카테고리 그룹 코드
CATEGORY = {
    "음식점":   "FD6",
    "카페":     "CE7",
    "편의점":   "CS2",
    "주유소":   "OL7",
    "주차장":   "PK6",
    "숙박":     "AD5",
    "관광명소": "AT4",
    "문화시설": "CT1",
    "병원":     "HP8",
    "약국":     "PM9",
    "마트":     "MT1",
    "학교":     "SC4",
}


class KakaoMapSource:
    """카카오맵 로컬 검색."""

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or _API_KEY
        if not self.api_key:
            raise ValueError("KAKAO_REST_API_KEY 환경변수가 필요합니다.")
        self._headers = {"Authorization": f"KakaoAK {self.api_key}"}

    # ── 키워드 검색 ──────────────────────────────────────────────────────────

    def search_keyword(self, query: str, x: str = "", y: str = "",
                       radius: int = 0, size: int = 15,
                       page: int = 1) -> list[dict]:
        """키워드로 장소 검색.

        Args:
            query:  검색어 (예: "강남역 맛집")
            x:      중심 경도 (반경 검색 시)
            y:      중심 위도 (반경 검색 시)
            radius: 반경 (미터, 0이면 무제한)
            size:   결과 수 (최대 15)
            page:   페이지 (최대 45)

        Returns:
            장소 dict 리스트
        """
        params = {"query": query, "size": size, "page": page}
        if x and y:
            params["x"] = x
            params["y"] = y
        if radius:
            params["radius"] = radius

        try:
            resp = requests.get(_SEARCH_URL, params=params,
                                headers=self._headers, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            places = data.get("documents", [])
            log(f"카카오맵 검색 '{query}': {len(places)}개 (총 {data['meta']['total_count']}개)", "ok")
            return [self._normalize(p) for p in places]
        except Exception as e:
            log(f"카카오맵 검색 실패 ({query}): {e}", "error")
            return []

    # ── 카테고리 검색 ────────────────────────────────────────────────────────

    def search_category(self, category: str, x: str, y: str,
                        radius: int = 1000, size: int = 15) -> list[dict]:
        """특정 좌표 반경 내 카테고리 검색.

        Args:
            category: 카테고리명 또는 코드 (CATEGORY dict 참조)
            x:        중심 경도
            y:        중심 위도
            radius:   반경 (미터, 기본 1km)
            size:     결과 수

        Returns:
            장소 dict 리스트
        """
        code = CATEGORY.get(category, category)  # 이름→코드 또는 코드 직접 사용
        params = {
            "category_group_code": code,
            "x": x, "y": y,
            "radius": radius,
            "size": size,
            "sort": "distance",
        }
        try:
            resp = requests.get(_CATEGORY_URL, params=params,
                                headers=self._headers, timeout=10)
            resp.raise_for_status()
            places = resp.json().get("documents", [])
            log(f"카카오맵 카테고리 '{category}' 반경{radius}m: {len(places)}개", "ok")
            return [self._normalize(p) for p in places]
        except Exception as e:
            log(f"카카오맵 카테고리 검색 실패: {e}", "error")
            return []

    # ── 지역 TOP 검색 (블로그 포스트 특화) ──────────────────────────────────

    def get_top_places(self, region: str, category: str = "음식점",
                       count: int = 10) -> list[dict]:
        """'{지역} {카테고리}' 키워드로 상위 장소 반환.

        블로그 '지역 맛집 TOP10' 포스트 소재 생성용.

        Args:
            region:   지역명 (예: "강남", "홍대", "제주")
            category: 카테고리명 (CATEGORY dict 키 또는 검색어)
            count:    반환할 장소 수

        Returns:
            장소 dict 리스트 (count 개)
        """
        query = f"{region} {category}"
        return self.search_keyword(query, size=min(count, 15))[:count]

    # ── 정규화 ───────────────────────────────────────────────────────────────

    @staticmethod
    def _normalize(p: dict) -> dict:
        """카카오 API 응답 → 내부 표준 포맷."""
        return {
            "name":          p.get("place_name", ""),
            "category":      p.get("category_group_name", ""),
            "category_detail": p.get("category_name", ""),
            "address":       p.get("road_address_name") or p.get("address_name", ""),
            "phone":         p.get("phone", ""),
            "url":           p.get("place_url", ""),
            "x":             p.get("x", ""),
            "y":             p.get("y", ""),
            "distance":      p.get("distance", ""),  # 반경 검색 시 미터 단위
        }
