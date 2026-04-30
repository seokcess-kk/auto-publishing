"""
해시태그 / 태그 생성 유틸리티

다양한 소스에서 실시간 트렌드 키워드를 수집하여 태그를 생성한다.

소스:
- Signal.bz 실시간 뉴스 키워드
- KDX.KR 실시간 순위
- 다음 연예 랭킹 키워드
- 네이트 실시간 검색어
- 네이버 연관검색어 (특정 키워드 기반)
"""
import requests
from bs4 import BeautifulSoup

from common.logger import log


_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)
_HEADERS = {"User-Agent": _USER_AGENT}


def from_signalbz(limit: int = 10) -> list[str]:
    """Signal.bz 실시간 뉴스 키워드에서 해시태그 생성.

    Args:
        limit: 최대 태그 수

    Returns:
        해시태그 목록 (예: ['#키워드1', '#키워드2'])
    """
    url = "https://api.signal.bz/news/realtime"
    headers = {
        "Accept": "application/json, text/plain, */*",
        "Origin": "https://signal.bz",
        "Referer": "https://signal.bz/",
        "User-Agent": _USER_AGENT,
    }
    try:
        resp = requests.get(url, headers=headers, timeout=10)
        data = resp.json()
        tags = []
        for item in data.get("top10", [])[:limit]:
            keyword = item.get("keyword", "")
            if keyword:
                tags.append(f"#{keyword.replace(' ', '')}")
        log(f"Signal.bz 태그 {len(tags)}개 수집", "ok")
        return tags
    except Exception as e:
        log(f"Signal.bz 태그 수집 실패: {e}", "warn")
        return []


def from_kdx(limit: int = 10) -> list[str]:
    """KDX.KR 실시간 순위에서 해시태그 생성.

    Args:
        limit: 최대 태그 수

    Returns:
        해시태그 목록
    """
    url = "https://dable-public.s3-ap-northeast-1.amazonaws.com/static/production/tmp/media-index/search_word.json"
    headers = {
        "Origin": "https://lab.kdx.kr",
        "Referer": "https://lab.kdx.kr/",
        "User-Agent": _USER_AGENT,
    }
    try:
        resp = requests.get(url, headers=headers, timeout=10)
        data = resp.json()
        tags = []
        for item in data.get("data_rising", [])[:limit]:
            keyword = item.get("keyword", "")
            if keyword:
                tags.append(f"#{keyword.replace(' ', '')}")
        log(f"KDX 태그 {len(tags)}개 수집", "ok")
        return tags
    except Exception as e:
        log(f"KDX 태그 수집 실패: {e}", "warn")
        return []


def from_daum_entertain(limit: int = 10) -> list[str]:
    """다음 연예 랭킹 키워드에서 해시태그 생성.

    Args:
        limit: 최대 태그 수

    Returns:
        해시태그 목록
    """
    url = "https://entertain.daum.net/ranking/keyword"
    try:
        resp = requests.get(url, headers=_HEADERS, timeout=10)
        soup = BeautifulSoup(resp.text, "lxml")
        keywords = soup.find_all("span", class_="txt_g")
        tags = []
        for kw in keywords[:limit]:
            text = kw.get_text().strip()
            if text:
                tags.append(f"#{text.replace(' ', '')}")
        log(f"다음 연예 태그 {len(tags)}개 수집", "ok")
        return tags
    except Exception as e:
        log(f"다음 연예 태그 수집 실패: {e}", "warn")
        return []


def from_nate(limit: int = 10) -> list[str]:
    """네이트 실시간 검색어에서 해시태그 생성.

    Args:
        limit: 최대 태그 수

    Returns:
        해시태그 목록
    """
    url = "https://www.nate.com/js/data/jsonLiveKeywordDataV1.js"
    try:
        resp = requests.get(url, headers=_HEADERS, timeout=10)
        data = resp.json()
        tags = []
        for item in data[:limit]:
            keyword = item[1] if len(item) > 1 else ""
            if keyword:
                tags.append(f"#{keyword.replace(' ', '')}")
        log(f"네이트 태그 {len(tags)}개 수집", "ok")
        return tags
    except Exception as e:
        log(f"네이트 태그 수집 실패: {e}", "warn")
        return []


def from_naver_related(keyword: str, limit: int = 10) -> list[str]:
    """네이버 연관검색어에서 해시태그 생성.

    Args:
        keyword: 검색할 키워드
        limit: 최대 태그 수

    Returns:
        해시태그 목록
    """
    url = f"https://search.naver.com/search.naver?where=nexearch&sm=top_hty&fbm=0&ie=utf8&query={keyword}"
    try:
        resp = requests.get(url, headers=_HEADERS, timeout=10)
        soup = BeautifulSoup(resp.text, "html.parser")
        results = soup.select("a.keyword > div.tit")
        tags = []
        for el in results[:limit]:
            text = el.get_text().strip()
            if text:
                tags.append(f"#{text.replace(' ', '')}")
        log(f"네이버 연관검색어 '{keyword}' → {len(tags)}개 수집", "ok")
        return tags
    except Exception as e:
        log(f"네이버 연관검색어 수집 실패: {e}", "warn")
        return []


def from_title(title: str) -> list[str]:
    """제목에서 해시태그 추출 (공백 기준 분리, 2자 이상).

    Args:
        title: 콘텐츠 제목

    Returns:
        해시태그 목록
    """
    words = title.replace(",", "").split()
    return [f"#{w}" for w in words if len(w) >= 2]


def collect_trending(sources: list[str] = None,
                     keyword: str = None,
                     limit_per_source: int = 10) -> list[str]:
    """여러 소스에서 트렌딩 태그를 수집하여 통합.

    Args:
        sources: 사용할 소스 목록.
                 None이면 ['signalbz'] 사용.
                 선택 가능: 'signalbz', 'kdx', 'daum', 'nate', 'naver'
        keyword: 네이버 연관검색어용 키워드 (sources에 'naver' 포함 시 필요)
        limit_per_source: 소스당 최대 태그 수

    Returns:
        중복 제거된 해시태그 목록
    """
    if sources is None:
        sources = ["signalbz"]

    source_funcs = {
        "signalbz": lambda: from_signalbz(limit_per_source),
        "kdx": lambda: from_kdx(limit_per_source),
        "daum": lambda: from_daum_entertain(limit_per_source),
        "nate": lambda: from_nate(limit_per_source),
        "naver": lambda: from_naver_related(keyword, limit_per_source) if keyword else [],
    }

    all_tags = []
    seen = set()
    for src in sources:
        func = source_funcs.get(src)
        if not func:
            log(f"알 수 없는 태그 소스: {src}", "warn")
            continue
        for tag in func():
            if tag not in seen:
                seen.add(tag)
                all_tags.append(tag)

    return all_tags


def filter_forbidden(tags: list[str],
                     forbidden: list[str] = None) -> list[str]:
    """금지 키워드를 포함하는 태그를 필터링.

    Args:
        tags: 해시태그 목록
        forbidden: 금지 키워드 목록. None이면 내장 목록 사용.

    Returns:
        필터링된 태그 목록
    """
    if forbidden is None:
        from common.forbidden_keywords import FORBIDDEN_KEYWORDS
        forbidden = FORBIDDEN_KEYWORDS

    forbidden_set = set(forbidden)
    filtered = []
    for tag in tags:
        clean = tag.lstrip("#").strip()
        if clean not in forbidden_set:
            filtered.append(tag)

    removed = len(tags) - len(filtered)
    if removed > 0:
        log(f"금지 키워드 {removed}개 필터링", "step")
    return filtered


def tags_to_string(tags: list[str], separator: str = " ") -> str:
    """태그 목록을 문자열로 변환.

    Args:
        tags: 해시태그 목록
        separator: 구분자

    Returns:
        태그 문자열 (예: '#키워드1 #키워드2')
    """
    return separator.join(tags)


def tags_to_plain(tags: list[str]) -> list[str]:
    """해시태그에서 '#' 제거하여 일반 태그 목록 반환.

    Args:
        tags: 해시태그 목록 (예: ['#키워드1', '#키워드2'])

    Returns:
        일반 태그 목록 (예: ['키워드1', '키워드2'])
    """
    return [t.lstrip("#").strip() for t in tags if t.lstrip("#").strip()]
