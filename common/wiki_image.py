"""
Wikipedia 페이지 대표 이미지(thumbnail) 검색 헬퍼.

역사 사건/인물 텍스트에서 키워드를 뽑아 한국어 위키 → 영문 위키 순으로
페이지 thumbnail 을 찾아 URL 반환. 카드 인포그래픽에 활용.

사용 예:
    from common.wiki_image import find_event_image_url

    url = find_event_image_url("미국 대통령 에이브러햄 링컨을 암살한 저격범 존 윌크스 부스가 ...")
    # → 'https://upload.wikimedia.org/.../Abraham_Lincoln_O-77_matte_collodion_print.jpg'
"""
from __future__ import annotations

import re
from typing import Optional

import requests

from .logger import log


_HEADERS = {
    "User-Agent": "AutoPublishing/1.0 (https://github.com/MoonbirdThinker)",
    "Accept": "application/json",
}

_TIMEOUT = 8


# 무시할 일반어 (검색 키워드로 부적합)
_STOPWORDS = {
    "사망", "출생", "탄생", "발생", "발표", "선포", "성립", "체결", "개최",
    "시작", "완료", "참여", "통과", "오늘", "최초", "당시", "이날", "이상",
    "대통령", "총리", "장관", "대표", "회장", "교수", "박사", "감독",
    "사건", "사고", "행사", "회의", "회담",
    "에이", "비를", "어진", "이루", "여러", "연합", "공화", "국가",
}


def _extract_keywords(text: str, max_n: int = 5) -> list[str]:
    """이벤트 텍스트에서 검색 후보 키워드 추출 (긴 한글 명사 우선).

    위키 응답 텍스트는 띄어쓰기가 깨진 경우가 많아, 길이 3-10자 한글 덩어리를
    추출 후 stopword 필터. 가나다 외(영문/숫자) 토큰도 후보로 포함.
    """
    # 1) 영문 대문자 단어 (Lincoln, NASA 등) — 가장 우선
    eng_caps = re.findall(r"\b[A-Z][a-zA-Z]{2,}\b", text)
    # 2) 한글 3-10자 토큰
    kor_tokens = re.findall(r"[가-힣]{3,10}", text)
    # 3) 숫자+한글 조합 (예: 6.25, 4.19)
    nums = re.findall(r"\d+\.\d+|\d+년", text)

    candidates: list[str] = []
    seen: set[str] = set()
    for tok in eng_caps + kor_tokens + nums:
        t = tok.strip()
        if not t or t in seen or t in _STOPWORDS:
            continue
        # 한글 토큰이 너무 일반적이거나 stopword 부분일치면 제외
        if t in _STOPWORDS:
            continue
        candidates.append(t)
        seen.add(t)
        if len(candidates) >= max_n:
            break
    return candidates


def _wiki_thumbnail(query: str, lang: str = "ko",
                    min_size: int = 300) -> Optional[str]:
    """위키피디아 REST API summary 엔드포인트로 thumbnail URL 조회."""
    try:
        # 1) 검색 API 로 가장 유사한 페이지 제목 찾기
        search_url = f"https://{lang}.wikipedia.org/w/rest.php/v1/search/page"
        sr = requests.get(
            search_url, params={"q": query, "limit": 1},
            headers=_HEADERS, timeout=_TIMEOUT,
        )
        if sr.status_code != 200:
            return None
        sj = sr.json()
        pages = sj.get("pages") or []
        if not pages:
            return None
        title = pages[0].get("key") or pages[0].get("title")
        if not title:
            return None

        # 2) summary API 로 대표 thumbnail
        sum_url = f"https://{lang}.wikipedia.org/api/rest_v1/page/summary/{title}"
        rr = requests.get(sum_url, headers=_HEADERS, timeout=_TIMEOUT)
        if rr.status_code != 200:
            return None
        rj = rr.json()
        # originalimage 가 있으면 우선, 없으면 thumbnail
        orig = rj.get("originalimage") or {}
        thumb = rj.get("thumbnail") or {}
        for src in (orig, thumb):
            url = src.get("source")
            w = src.get("width", 0) or 0
            if url and w >= min_size:
                return url
        # 작은 thumbnail 이라도 반환 (없는 것보단 낫다)
        if thumb.get("source"):
            return thumb["source"]
        return None
    except Exception as e:
        log(f"[wiki_image] {lang}/{query} 조회 실패: {e}", "warn")
        return None


def find_event_image_url(event_text: str) -> Optional[str]:
    """이벤트 텍스트에서 키워드를 뽑아 위키 thumbnail URL 반환.

    한국어 위키 → 영문 위키 순으로 시도. 매칭 없으면 None.
    """
    keywords = _extract_keywords(event_text)
    if not keywords:
        return None

    # 길이 4자 이상 한글 키워드 우선, 그다음 영문 대문자, 그다음 짧은 한글
    def _sort_key(k: str) -> tuple:
        is_kor = bool(re.match(r"^[가-힣]+$", k))
        is_eng_cap = bool(re.match(r"^[A-Z]", k))
        return (
            0 if is_eng_cap else (1 if is_kor and len(k) >= 4 else 2),
            -len(k),
        )

    keywords.sort(key=_sort_key)

    for kw in keywords[:5]:
        for lang in ("ko", "en"):
            url = _wiki_thumbnail(kw, lang=lang)
            if url:
                log(f"[wiki_image] '{kw}' ({lang}) → {url}", "info")
                return url
    return None


__all__ = ["find_event_image_url"]
