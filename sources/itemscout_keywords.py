"""
ItemScout 키워드 수집 및 풀 관리 모듈

수집 전략:
  - ItemScout 내부 API (API 키 불필요)
    POST https://api.itemscout.io/api/category/{cid}/data
  - 12개 카테고리 × 최대 500개 = 최대 6,000개 키워드
  - 중복 제거 후 keyword_pool.json 에 저장
  - 발행 완료 키워드는 used_keywords.json 에 영구 기록
  - 풀에서 키워드 꺼낼 때 used_keywords 에 있으면 자동 제외

참조: 00.Old_Source/상품키워드추출(datalab_itemscout)/
      itemscoute(requests)_naverdatalab(requests)_ver3.py
"""
import os
import json
import time
import random
from datetime import datetime
from urllib import parse

import requests

from common.logger import log


# ─── 경로 설정 ────────────────────────────────────────────────────────────────

_BASE_DIR         = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KEYWORD_POOL_PATH = os.path.join(_BASE_DIR, "data", "keyword_pool.json")
USED_KEYWORDS_PATH = os.path.join(_BASE_DIR, "data", "used_keywords.json")


# ─── ItemScout 카테고리 정의 ───────────────────────────────────────────────────

ITEMSCOUT_CATEGORIES = {
    "패션의류":     "1",
    "패션잡화":     "2",
    "화장품/미용":  "3",
    "디지털/가전":  "4",
    "가구/인테리어": "5",
    "출산/육아":    "6",
    "식품":         "7",
    "스포츠/레저":  "8",
    "생활/건강":    "9",
    "여가/생활편의": "10",
    "면세점":       "11",
    "도서":         "45830",
}

ITEMSCOUT_API_URL = "https://api.itemscout.io/api/category/{cid}/data"
FIXED_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/110.0.0.0 Whale/3.19.166.16 Safari/537.36"
)


# ─── 파일 입출력 헬퍼 ─────────────────────────────────────────────────────────

def _ensure_data_dir():
    os.makedirs(os.path.join(_BASE_DIR, "data"), exist_ok=True)


def _load_json(path: str, default):
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return default


def _save_json(path: str, data) -> None:
    _ensure_data_dir()
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ─── 키워드 풀 관리 ──────────────────────────────────────────────────────────

def load_keyword_pool() -> dict:
    """
    키워드 풀 로드.
    반환 형식:
    {
      "updated_at": "2026-04-10 12:00:00",
      "total": 5800,
      "keywords": [
        {"keyword": "바람막이", "category": "패션의류", "monthly": 136400, "rank": 3},
        ...
      ]
    }
    """
    return _load_json(KEYWORD_POOL_PATH, {"updated_at": None, "total": 0, "keywords": []})


def save_keyword_pool(pool: dict) -> None:
    _save_json(KEYWORD_POOL_PATH, pool)


def load_used_keywords() -> dict:
    """사용 완료 키워드 로드. {keyword: "YYYY-MM-DD HH:MM:SS"}"""
    return _load_json(USED_KEYWORDS_PATH, {})


def mark_keywords_used(keywords: list) -> None:
    """발행 완료된 키워드를 used_keywords.json 에 영구 기록."""
    data = load_used_keywords()
    now  = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    for kw in keywords:
        data[kw] = now
    _save_json(USED_KEYWORDS_PATH, data)
    log(f"발행 완료 키워드 {len(keywords)}개 기록: {keywords}", "info")


def get_pool_status() -> str:
    """풀 현황 요약 문자열."""
    pool = load_keyword_pool()
    used = load_used_keywords()
    total    = pool.get("total", 0)
    used_cnt = len(used)
    available = sum(
        1 for item in pool.get("keywords", [])
        if item["keyword"] not in used
    )
    updated  = pool.get("updated_at", "없음")
    return (
        f"키워드 풀: 전체 {total}개 | 발행완료 {used_cnt}개 | "
        f"잔여 {available}개 | 마지막 수집: {updated}"
    )


# ─── ItemScout 수집 ──────────────────────────────────────────────────────────

def _fetch_category(cid: str, cat_name: str,
                    duration: str = "30d",
                    gender: str = "f,m",
                    ages: str = "10,60",
                    max_count: int = 500) -> list:
    """
    ItemScout 카테고리 키워드 수집.
    반환: [{"keyword": ..., "category": ..., "monthly": ..., "rank": ...}, ...]
    """
    headers = {
        "accept": "application/json, text/plain, */*",
        "content-type": "application/x-www-form-urlencoded",
        "origin": "https://itemscout.io",
        "referer": "https://itemscout.io/",
        "user-agent": FIXED_UA,
    }
    data = {
        "duration": parse.quote(duration, encoding="utf-8"),
        "genders":  parse.quote(gender, encoding="utf-8"),
        "ages":     parse.quote(ages, encoding="utf-8"),
    }
    url = ITEMSCOUT_API_URL.format(cid=cid)

    try:
        resp = requests.post(url, headers=headers, data=data, timeout=20)
        resp.raise_for_status()
        raw_items = resp.json().get("data", {}).get("data", {})
        results = []
        for idx, (kid, v) in enumerate(raw_items.items()):
            if idx >= max_count:
                break
            kw = v.get("keyword", "").strip()
            if not kw:
                continue
            results.append({
                "keyword":  kw,
                "category": cat_name,
                "monthly":  v.get("monthly", {}).get("total", 0),
                "rank":     v.get("rank", 9999),
            })
        return results
    except Exception as e:
        log(f"ItemScout 수집 실패 ({cat_name}): {e}", "warn")
        return []


def collect_all_keywords(max_per_category: int = 500,
                          delay: float = 1.0) -> dict:
    """
    12개 카테고리 전체 키워드 수집 → 중복 제거 → keyword_pool.json 저장.

    Args:
        max_per_category: 카테고리당 최대 수집 수 (최대 500)
        delay: 카테고리 간 요청 딜레이 (초)

    Returns:
        저장된 풀 dict
    """
    log("ItemScout 전체 카테고리 키워드 수집 시작", "step")
    log(f"대상: {len(ITEMSCOUT_CATEGORIES)}개 카테고리 × 최대 {max_per_category}개", "info")

    all_keywords = []
    seen = set()

    for cat_name, cid in ITEMSCOUT_CATEGORIES.items():
        log(f"  수집 중: {cat_name} (cid={cid})", "info")
        items = _fetch_category(cid, cat_name, max_count=max_per_category)

        added = 0
        for item in items:
            kw = item["keyword"]
            if kw not in seen:
                seen.add(kw)
                all_keywords.append(item)
                added += 1

        log(f"  → {len(items)}개 수집, {added}개 추가 (누계 {len(all_keywords)}개)", "info")
        time.sleep(delay)

    # monthly 검색수 내림차순 정렬
    all_keywords.sort(key=lambda x: x["monthly"], reverse=True)

    pool = {
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "total":      len(all_keywords),
        "keywords":   all_keywords,
    }
    save_keyword_pool(pool)
    log(f"키워드 풀 저장 완료: {len(all_keywords)}개 (중복 제거 후)", "ok")
    return pool


# ─── 풀에서 키워드 꺼내기 ────────────────────────────────────────────────────

def _trend_weight(item: dict) -> float:
    """Pandarank rank_change 와 source 에 따라 가중치 계산.

    rank_change=up   → 3.0 (트렌드 상승, 우선 발행)
    rank_change=new  → 2.5 (신규 진입 — up 보다 약간 낮춰 노이즈 완화)
    rank_change=keep → 1.5 (변동 없음, 약간 가산)
    rank_change=down → 0.7 (하락, 약간 감점)
    rank_change 없음 → 1.0 (기본 — ItemScout/DataLab)
    """
    rc = (item.get("rank_change") or "").lower()
    if rc == "up":
        return 3.0
    if rc == "new":
        return 2.5
    if rc == "keep":
        return 1.5
    if rc == "down":
        return 0.7
    return 1.0


_RC_PRIORITY = {"up": 0, "new": 1, "keep": 2, "": 3, "down": 4}


def _build_top_pool(available: list, size: int = 100,
                     quotas: dict = None) -> list:
    """소스별 quota 로 후보 풀 구성 — monthly 단일축 정렬의 편중을 보정.

    available 은 monthly 내림차순으로 정렬되어 들어온다고 가정한다.
    그 상태에서 단순히 앞에서 N개를 자르면 monthly=0 인 Pandarank/DataLab
    항목이 거의 들어오지 못해 트렌드 신호가 죽는다. 이를 보정하기 위해
    소스별 quota 만큼 우선 채우고, 모자란 자리는 monthly 순서로 보충한다.

    Pandarank 항목은 rank_change 우선순위(up>new>keep>none>down)로 재정렬해
    quota 안에서도 트렌드 시그널이 살도록 한다.
    """
    if quotas is None:
        quotas = {"itemscout": 50, "pandarank": 35, "datalab": 15}

    buckets = {src: [] for src in quotas}
    leftovers = []
    for it in available:
        src = it.get("source") or "itemscout"
        if src in buckets:
            buckets[src].append(it)
        else:
            leftovers.append(it)

    buckets["pandarank"].sort(key=lambda x: (
        _RC_PRIORITY.get((x.get("rank_change") or "").lower(), 3),
        x.get("rank", 9999),
    ))

    result, seen = [], set()
    for src, quota in quotas.items():
        for it in buckets[src][:quota]:
            kw = it["keyword"]
            if kw not in seen:
                seen.add(kw)
                result.append(it)

    if len(result) < size:
        for it in available:
            if len(result) >= size:
                break
            kw = it["keyword"]
            if kw not in seen:
                seen.add(kw)
                result.append(it)

    return result[:size]


def _weighted_sample(items: list, n: int, weights: list) -> list:
    """가중치 적용 비복원 샘플링 (Efraimidis-Spirakis A-Res 알고리즘).

    각 항목에 random()**(1/weight) 키를 부여하고 큰 순으로 n개 선택.
    weight 가 클수록 선택될 확률이 높아진다.
    """
    if not items:
        return []
    n = min(n, len(items))
    keyed = [
        (random.random() ** (1.0 / max(w, 1e-9)), idx)
        for idx, w in enumerate(weights)
    ]
    keyed.sort(reverse=True)
    return [items[idx] for _, idx in keyed[:n]]


def get_next_keywords(n: int = 3,
                       refill_threshold: int = 50,
                       categories: list = None,
                       prefer_trending: bool = True) -> list:
    """
    키워드 풀에서 미사용 키워드 n개 반환.
    - used_keywords.json 에 있는 키워드는 자동 제외
    - 잔여 키워드가 refill_threshold 이하면 자동 재수집
    - categories 지정 시 해당 카테고리 키워드만 우선 반환
    - prefer_trending=True 면 Pandarank rank_change=up 키워드를 우선 발행

    Args:
        n:                  가져올 키워드 수
        refill_threshold:   이 개수 이하면 자동 재수집 트리거
        categories:         특정 카테고리 필터 (None = 전체)
        prefer_trending:    True 면 트렌드 가중 샘플링, False 면 단순 랜덤

    Returns:
        키워드 문자열 리스트
    """
    pool = load_keyword_pool()
    used = load_used_keywords()

    # 풀이 비어있으면 즉시 수집
    if not pool.get("keywords"):
        log("키워드 풀 비어있음 → 즉시 수집", "warn")
        pool = collect_all_keywords()

    # 미사용 키워드 필터링
    available = [
        item for item in pool["keywords"]
        if item["keyword"] not in used
        and (categories is None or item["category"] in categories)
    ]

    # 잔여 부족 시 백그라운드 재수집 알림 (실제 재수집은 별도 스케줄)
    if len(available) <= refill_threshold:
        log(f"잔여 키워드 {len(available)}개 — 재수집 필요 (threshold={refill_threshold})", "warn")
        pool = collect_all_keywords()
        used = load_used_keywords()
        available = [
            item for item in pool["keywords"]
            if item["keyword"] not in used
            and (categories is None or item["category"] in categories)
        ]

    if not available:
        log("사용 가능한 키워드 없음", "error")
        return []

    # 소스별 quota 로 후보 풀 구성 (monthly 단일축 편중 보정)
    top_pool = _build_top_pool(available, size=100)

    if prefer_trending:
        weights = [_trend_weight(it) for it in top_pool]
        trend_hot = sum(1 for w in weights if w >= 2.5)
        selected = _weighted_sample(top_pool, n, weights)
        keywords = [item["keyword"] for item in selected]
        log(f"키워드 선택 (trend-weighted, up/new={trend_hot}): {keywords} (잔여 {len(available)}개)", "ok")
    else:
        # 기존 동작: 균등 랜덤
        selected = random.sample(top_pool, min(n, len(top_pool)))
        keywords = [item["keyword"] for item in selected]
        log(f"키워드 선택: {keywords} (잔여 {len(available)}개)", "ok")

    return keywords


def get_used_keywords_summary() -> str:
    """하위 호환용 — get_pool_status() 와 동일."""
    return get_pool_status()


# ─── 다중 소스 통합 수집 (ItemScout + Pandarank + DataLab 확장) ────────────────

def collect_all_keywords_multi(sources: list = None,
                                itemscout_max: int = 500,
                                pandarank_subcats: bool = True,
                                datalab_days: int = 30,
                                datalab_time_unit: str = "week",
                                datalab_pages: int = 3,
                                datalab_by_age: bool = True,
                                datalab_by_gender: bool = False,
                                datalab_by_device: bool = False,
                                delay: float = 0.5) -> dict:
    """
    여러 소스(ItemScout, Pandarank, DataLab 확장)를 통합 수집하여 풀 저장.

    Args:
        sources: ["itemscout", "pandarank", "datalab"] 중 선택 (기본: 전부)
        itemscout_max:      카테고리당 최대 수집 (최대 500)
        pandarank_subcats:  True면 중분류 포함 194개, False면 대분류 10개
        datalab_days:       DataLab 조회 기간 (일)
        datalab_time_unit:  date|week|month
        datalab_pages:      DataLab 카테고리×차원별 페이지 수
        datalab_by_age:     연령대(10s~60s) 6개 차원 추가
        datalab_by_gender:  성별 (f, m) 차원 추가
        datalab_by_device:  기기 (pc, mo) 차원 추가

    Returns:
        저장된 풀 dict — keywords 항목은 source 필드 포함
        우선순위: itemscout(monthly 기준) > pandarank > datalab
    """
    import time as _time
    sources = sources or ["itemscout", "pandarank", "datalab"]
    log(f"멀티소스 키워드 수집 시작 — sources={sources}", "step")

    merged = {}  # keyword → record (source 우선순위로 덮어쓰기 방지)
    source_stats = {}

    # 1) ItemScout (monthly 검색량 제공 — 최우선)
    if "itemscout" in sources:
        items_pool = collect_all_keywords(max_per_category=itemscout_max, delay=delay)
        for item in items_pool.get("keywords", []):
            kw = item["keyword"]
            if kw not in merged:
                item.setdefault("source", "itemscout")
                merged[kw] = item
        source_stats["itemscout"] = items_pool.get("total", 0)

    # 2) Pandarank (판다랭크 — 실시간 트렌드 반영)
    if "pandarank" in sources:
        from sources.pandarank_keywords import collect_pandarank_keywords
        pr_items = collect_pandarank_keywords(
            delay=0.3, include_subcategories=pandarank_subcats,
        )
        added = 0
        for item in pr_items:
            kw = item["keyword"]
            if kw not in merged:
                merged[kw] = item
                added += 1
            else:
                # 기존 항목에 pandarank 랭크 변동 정보를 보존 (정보 증분)
                merged[kw].setdefault("rank_change", item.get("rank_change", ""))
        source_stats["pandarank"] = len(pr_items)
        source_stats["pandarank_new"] = added
        _time.sleep(delay)

    # 3) DataLab 확장 (연령/성별/기기 차원 × 카테고리)
    if "datalab" in sources:
        from sources.datalab_keywords import collect_datalab_keywords_extended
        dl_items = collect_datalab_keywords_extended(
            days=datalab_days,
            time_unit=datalab_time_unit,
            pages=datalab_pages,
            by_age=datalab_by_age,
            by_gender=datalab_by_gender,
            by_device=datalab_by_device,
            delay=0.3,
        )
        added = 0
        for item in dl_items:
            kw = item["keyword"]
            if kw not in merged:
                merged[kw] = item
                added += 1
            else:
                # DataLab 차원 정보 병합 (dim 누적)
                existing_dim = merged[kw].get("dim", "")
                new_dim = item.get("dim", "")
                if new_dim and new_dim not in existing_dim:
                    merged[kw]["dim"] = f"{existing_dim};{new_dim}".strip(";")
        source_stats["datalab"] = len(dl_items)
        source_stats["datalab_new"] = added

    # 최종 풀 저장 — monthly 내림차순 (itemscout 우선), 그 다음은 수집 순서 유지
    all_keywords = list(merged.values())
    all_keywords.sort(
        key=lambda x: (
            -x.get("monthly", 0),
            {"itemscout": 0, "pandarank": 1, "datalab": 2}.get(x.get("source", "unknown"), 9),
            x.get("rank", 9999),
        )
    )

    pool = {
        "updated_at":   datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "total":        len(all_keywords),
        "sources":      source_stats,
        "keywords":     all_keywords,
    }
    save_keyword_pool(pool)
    log(f"멀티소스 풀 저장: {len(all_keywords)}개 | 소스별: {source_stats}", "ok")
    return pool
