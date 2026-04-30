"""
네이버 DataLab 쇼핑인사이트 키워드 수집 모듈

크롤링 전략:
  - 네이버 DataLab 쇼핑인사이트 내부 API (API 키 불필요)
    POST https://datalab.naver.com/shoppingInsight/getCategoryKeywordRank.naver
  - 카테고리별 인기 키워드 순위 크롤링
  - 중복 검색 방지: used_keywords.json 파일로 이미 사용한 키워드 추적

참조: 00.Old_Source/상품키워드추출(datalab_itemscout)/
      itemscoute(requests)_naverdatalab(requests)_ver3.py
"""
import os
import json
import random
from datetime import datetime, timedelta
from typing import Optional

import requests

from common.logger import log


# ─── 네이버 DataLab 쇼핑 카테고리 코드 ─────────────────────────────────────────

DATALAB_CATEGORIES = {
    "패션의류":    "50000000",
    "패션잡화":    "50000001",
    "화장품/미용": "50000002",
    "디지털/가전": "50000003",
    "가구/인테리어": "50000004",
    "출산/육아":   "50000005",
    "식품":        "50000006",
    "스포츠/레저": "50000007",
    "생활/건강":   "50000008",
    "여가/생활편의": "50000009",
}

DATALAB_URL   = "https://datalab.naver.com/shoppingInsight/getCategoryKeywordRank.naver"
DATALAB_REFER = "https://datalab.naver.com/"
FIXED_UA      = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/110.0.0.0 Whale/3.19.166.16 Safari/537.36"
)

# 중복 키워드 추적 파일 경로
USED_KEYWORDS_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data", "used_keywords.json"
)


# ─── 중복 키워드 관리 ──────────────────────────────────────────────────────────

def _load_used_keywords() -> dict:
    """사용한 키워드 목록 로드. 파일 없으면 빈 dict 반환."""
    if os.path.exists(USED_KEYWORDS_PATH):
        try:
            with open(USED_KEYWORDS_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def _save_used_keywords(data: dict) -> None:
    """사용한 키워드 목록 저장."""
    os.makedirs(os.path.dirname(USED_KEYWORDS_PATH), exist_ok=True)
    with open(USED_KEYWORDS_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def mark_keywords_used(keywords: list) -> None:
    """키워드를 '사용 완료'로 표시 (타임스탬프 기록)."""
    data = _load_used_keywords()
    now  = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    for kw in keywords:
        data[kw] = now
    _save_used_keywords(data)
    log(f"사용 완료 키워드 {len(keywords)}개 기록", "info")


def filter_unused_keywords(keywords: list, expire_days: int = 7) -> list:
    """이미 사용한 키워드 제거. expire_days 이후엔 재사용 허용."""
    data    = _load_used_keywords()
    cutoff  = datetime.now() - timedelta(days=expire_days)
    unused  = []
    skipped = []

    for kw in keywords:
        if kw not in data:
            unused.append(kw)
        else:
            used_at = datetime.strptime(data[kw], "%Y-%m-%d %H:%M:%S")
            if used_at < cutoff:
                unused.append(kw)   # 만료됐으니 재사용 가능
            else:
                skipped.append(kw)

    if skipped:
        log(f"중복 키워드 {len(skipped)}개 제외: {skipped[:5]}{'...' if len(skipped) > 5 else ''}", "info")
    return unused


def get_used_keywords_summary() -> str:
    """사용 중인 키워드 현황 요약 문자열 반환."""
    data = _load_used_keywords()
    if not data:
        return "사용한 키워드 없음"
    return f"누적 사용 키워드 {len(data)}개 (최근: {sorted(data.values())[-1]})"


# ─── DataLab 크롤링 ────────────────────────────────────────────────────────────

# 연령/성별/기기 필터 상수 (네이버 DataLab 쇼핑인사이트 실제 파라미터 값)
DATALAB_AGES    = ["10", "20", "30", "40", "50", "60"]
DATALAB_GENDERS = ["f", "m"]
DATALAB_DEVICES = ["pc", "mo"]
DATALAB_TIME_UNITS = ["date", "week", "month"]


def _crawl_datalab_category(cid: str,
                             count: int = 20,
                             days: int = 7,
                             time_unit: str = "date",
                             age: str = "",
                             gender: str = "",
                             device: str = "",
                             page: int = 1) -> list:
    """
    네이버 DataLab 특정 카테고리 인기 키워드 크롤링 (API 키 불필요).

    Args:
        cid:       카테고리 코드 (e.g. "50000007" 스포츠/레저)
        count:     페이지당 키워드 수 (최대 20)
        days:      조회 기간 (오늘 기준 N일 전 ~ 오늘)
        time_unit: date|week|month
        age:       ""|"10"|"20"|...|"60"
        gender:    ""|"f"|"m"
        device:    ""|"pc"|"mo"
        page:      페이지 번호 (1~25까지 서비스 지원)

    Returns:
        [keyword, ...] 단순 리스트
    """
    end_date   = datetime.now()
    start_date = end_date - timedelta(days=days)

    params = {
        "cid":       cid,
        "timeUnit":  time_unit,
        "startDate": start_date.strftime("%Y-%m-%d"),
        "endDate":   end_date.strftime("%Y-%m-%d"),
        "age":       age,
        "gender":    gender,
        "device":    device,
        "page":      page,
        "count":     min(count, 20),
    }
    headers = {
        "User-Agent": FIXED_UA,
        "Referer":    DATALAB_REFER,
    }

    try:
        resp = requests.post(DATALAB_URL, params=params, headers=headers, timeout=10)
        resp.raise_for_status()
        ranks = resp.json().get("ranks", [])
        return [r["keyword"] for r in ranks if r.get("keyword")]
    except Exception as e:
        log(f"DataLab 크롤링 실패 (cid={cid}, age={age}, gender={gender}, device={device}): {e}", "warn")
        return []


def _crawl_datalab_paged(cid: str,
                          pages: int = 5,
                          days: int = 7,
                          time_unit: str = "date",
                          age: str = "",
                          gender: str = "",
                          device: str = "",
                          delay: float = 0.2) -> list:
    """여러 페이지를 순회하여 최대 pages×20 개 키워드 수집."""
    import time as _time
    collected = []
    for p in range(1, pages + 1):
        batch = _crawl_datalab_category(
            cid, count=20, days=days, time_unit=time_unit,
            age=age, gender=gender, device=device, page=p,
        )
        if not batch:
            break
        collected.extend(batch)
        _time.sleep(delay)
    return collected


def collect_datalab_keywords_extended(days: int = 30,
                                       time_unit: str = "week",
                                       pages: int = 3,
                                       by_age: bool = True,
                                       by_gender: bool = False,
                                       by_device: bool = False,
                                       delay: float = 0.3) -> list:
    """
    DataLab 전 카테고리 × 차원(연령/성별/기기) 조합 확장 수집.

    Args:
        days:       조회 기간 (기본 30일)
        time_unit:  date|week|month
        pages:      카테고리 × 차원별 페이지 수 (페이지당 20개)
        by_age:     6개 연령대(10s~60s) 각각 수집
        by_gender:  성별 (f, m) 각각 수집
        by_device:  기기 (pc, mo) 각각 수집
        delay:      요청 간 딜레이 (초)

    Returns:
        [{"keyword": ..., "category": 대분류명, "source": "datalab",
          "cid": ..., "dim": "age=20|gender=f|device=pc", "monthly": 0, "rank": ...}, ...]
        — 대분류당 최대 pages×20 × (1 + ages + genders + devices) 개
    """
    import time as _time
    # 차원 조합 구성
    dims = [("", "", "")]                                 # 전체
    if by_age:
        dims.extend([(a, "", "") for a in DATALAB_AGES])
    if by_gender:
        dims.extend([("", g, "") for g in DATALAB_GENDERS])
    if by_device:
        dims.extend([("", "", d) for d in DATALAB_DEVICES])

    total_calls = len(DATALAB_CATEGORIES) * len(dims) * pages
    log(f"DataLab 확장 수집 — 카테고리 {len(DATALAB_CATEGORIES)}, 차원 {len(dims)}, 페이지 {pages} (총 요청 약 {total_calls}회)", "step")

    results = []
    seen = set()
    call_idx = 0

    for cat_name, cid in DATALAB_CATEGORIES.items():
        for (age, gender, device) in dims:
            for p in range(1, pages + 1):
                call_idx += 1
                batch = _crawl_datalab_category(
                    cid, count=20, days=days, time_unit=time_unit,
                    age=age, gender=gender, device=device, page=p,
                )
                if not batch:
                    break  # 이 차원 페이지네이션 중단 (다음 차원으로)
                dim_label = "|".join(filter(None, [
                    f"age={age}" if age else "",
                    f"gender={gender}" if gender else "",
                    f"device={device}" if device else "",
                ])) or "all"
                added = 0
                for kw in batch:
                    if kw not in seen:
                        seen.add(kw)
                        results.append({
                            "keyword":  kw,
                            "category": cat_name,
                            "source":   "datalab",
                            "cid":      cid,
                            "dim":      dim_label,
                            "monthly":  0,
                            "rank":     9999,
                        })
                        added += 1
                _time.sleep(delay)
            if call_idx % 50 == 0:
                log(f"  진행 요청 {call_idx}/{total_calls} — 누계 {len(results)}개", "info")
        log(f"  ✅ {cat_name} 완료 (누계 {len(results)}개)", "info")

    log(f"DataLab 확장 수집 완료: {len(results)}개 (중복 제거 후)", "ok")
    return results


def get_datalab_keywords(categories: Optional[list] = None,
                         count_per_category: int = 20,
                         top_n: int = 20,
                         expire_days: int = 7) -> list:
    """
    네이버 DataLab 크롤링으로 인기 키워드 수집.

    Args:
        categories:          수집할 카테고리명 리스트 (None이면 전체 랜덤 3개)
        count_per_category:  카테고리당 수집 키워드 수 (최대 20)
        top_n:               최종 반환 키워드 수
        expire_days:         중복 제외 기간 (이 기간 내 사용한 키워드는 스킵)

    Returns:
        미사용 키워드 리스트
    """
    if categories is None:
        # 카테고리 랜덤 3개 선택
        categories = random.sample(list(DATALAB_CATEGORIES.keys()), 3)

    all_keywords = []
    for cat_name in categories:
        cid = DATALAB_CATEGORIES.get(cat_name)
        if not cid:
            log(f"알 수 없는 카테고리: {cat_name}", "warn")
            continue

        log(f"DataLab 크롤링: {cat_name} (cid={cid})", "step")
        keywords = _crawl_datalab_category(cid, count_per_category)
        log(f"  → {len(keywords)}개 수집: {keywords[:5]}...", "info")
        all_keywords.extend(keywords)

    # 중복 제거 (순서 유지)
    seen = set()
    unique = []
    for kw in all_keywords:
        if kw not in seen:
            seen.add(kw)
            unique.append(kw)

    # 이미 사용한 키워드 제외
    unused = filter_unused_keywords(unique, expire_days=expire_days)
    log(f"DataLab 키워드: 수집 {len(unique)}개 → 미사용 {len(unused)}개", "ok")

    return unused[:top_n]


# ─── DatalabKeywords 클래스 (하위 호환) ─────────────────────────────────────────

class DatalabKeywords:
    """네이버 DataLab 쇼핑 트렌드 키워드 수집 (크롤링 방식)."""

    def __init__(self, client_id: Optional[str] = None,
                 client_secret: Optional[str] = None):
        # API 방식 제거, 크롤링으로 대체 (파라미터는 하위 호환용으로 유지)
        pass

    def get_trending_keywords(self, category_id: str = "50000007",
                               days: int = 7, top_n: int = 20) -> list:
        """DataLab 크롤링으로 인기 키워드 반환 (하위 호환 메서드)."""
        # cid로 카테고리명 역탐색
        cid_to_name = {v: k for k, v in DATALAB_CATEGORIES.items()}
        cat_name = cid_to_name.get(category_id, "스포츠/레저")

        keywords = _crawl_datalab_category(category_id, count=top_n)
        if keywords:
            log(f"DataLab 크롤링 성공: {len(keywords)}개 ({cat_name})", "ok")
        return keywords
