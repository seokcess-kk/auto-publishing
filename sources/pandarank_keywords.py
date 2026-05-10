"""
판다랭크(Pandarank) 쇼핑 키워드 수집 모듈

수집 전략:
  - Pandarank 내부 API (API 키 불필요)
    GET https://pandarank.net/api/categories/home/{cid}
  - 대분류 10개 + 중분류 184개 = 총 194개 카테고리
  - 카테고리당 bestKeyword 최대 20개 → 이론상 최대 3,880개
  - 중복 제거 후 상위 카테고리(대분류) 이름으로 라벨링
"""
import time

import requests

from common.logger import log


PANDARANK_API_URL = "https://pandarank.net/api/categories/home/{cid}"
FIXED_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

# ─── 대분류 매핑 (pandarank cid → 카테고리명) ─────────────────────────────────

PANDARANK_TOP_CATEGORIES = {
    "50000000": "패션의류",
    "50000001": "패션잡화",
    "50000002": "화장품/미용",
    "50000003": "디지털/가전",
    "50000004": "가구/인테리어",
    "50000005": "출산/육아",
    "50000006": "식품",
    "50000007": "스포츠/레저",
    "50000008": "생활/건강",
    "50000009": "여가/생활편의",
}

# ─── 중분류(2차) 카테고리 코드 ────────────────────────────────────────────────
# 대분류 + 중분류 총 200개 명시. 그중 PANDARANK_DEAD_CIDS 6개는 /home API 가
# 일관되게 빈 응답을 주므로 수집 단계에서 자동 스킵한다.

# /api/categories/home/{cid} 가 비어있어 수집되지 않는 cid (구조적 dead).
# 정기 재검증 후 데이터가 돌아오면 셋에서 제거.
PANDARANK_DEAD_CIDS = {
    "50000009",  # 대분류: 여가/생활편의 (대분류 자체 빈 응답)
    "50000099",  # 디지털/가전 산하
    "50007127",  # 출산/육아 산하
    "50007256",  # 여가/생활편의 산하
    "50007261",  # 여가/생활편의 산하
    "50007286",  # 여가/생활편의 산하
}

PANDARANK_SUB_CATEGORIES = [
    # 대분류 10개
    "50000000", "50000001", "50000002", "50000003", "50000004",
    "50000005", "50000006", "50000007", "50000008", "50000009",
    # 중분류 184개
    "50000167", "50000169", "50000173", "50000174", "50000175", "50000176", "50000177", "50000178", "50000179", "50000180",
    "50000181", "50000182", "50000166", "50000183", "50000184", "50000185", "50000186", "50000189", "50000190", "50000194",
    "50000195", "50000192", "50000193", "50000191", "50000202", "50000200", "50000197", "50000198", "50000199", "50000196",
    "50000201", "50000151", "50000091", "50000205", "50000089", "50000153", "50000208", "50000209", "50000210", "50000211",
    "50000206", "50000213", "50000214", "50000212", "50000204", "50000087", "50000088", "50000090", "50000152", "50000092",
    "50000093", "50000094", "50000095", "50000096", "50000097", "50000098", "50000099", "50000100", "50000101", "50000102",
    "50000103", "50000104", "50000105", "50000106", "50000107", "50000108", "50000109", "50000110", "50000111", "50000112",
    "50000113", "50000154", "50000114", "50000115", "50000116", "50000117", "50000118", "50000119", "50000120", "50000121",
    "50000122", "50000123", "50000124", "50000125", "50000126", "50000127", "50000128", "50000129", "50000130", "50000131",
    "50000132", "50000133", "50000134", "50000135", "50000136", "50000137", "50000138", "50007135", "50000139", "50000140",
    "50007127", "50000141", "50000142", "50000143", "50000144", "50000145", "50000159", "50000160", "50000146", "50000147",
    "50000148", "50000149", "50000150", "50000026", "50000023", "50000024", "50011940", "50012460", "50012520", "50012620",
    "50012782", "50013360", "50013520", "50013960", "50013881", "50014240", "50000027", "50000028", "50000029", "50000161",
    "50000162", "50000163", "50000164", "50000030", "50000031", "50000033", "50000034", "50000035", "50000036", "50000037",
    "50000038", "50000039", "50000040", "50000041", "50000042", "50000045", "50000046", "50000048", "50000049", "50000050",
    "50000051", "50000052", "50000053", "50000020", "50000021", "50000022", "50000165", "50000158", "50000054", "50000055",
    "50000056", "50000057", "50000156", "50000155", "50000058", "50000061", "50000062", "50000063", "50000064", "50000065",
    "50000066", "50000067", "50000068", "50000069", "50000070", "50000071", "50000072", "50000073", "50000074", "50000079",
    "50000080", "50000075", "50000157", "50000076", "50000077", "50000078", "50007252", "50007256", "50007261", "50007286",
]


# ─── 카테고리 트리 매핑 ────────────────────────────────────────────────────────
# /api/categories 가 4-level 트리(category_id → category1_nm)를 제공하므로
# 그것을 동적으로 받아와 매핑한다. 트리 조회가 실패하면 대분류 정적 매핑으로 fallback.

PANDARANK_TREE_URL = "https://pandarank.net/api/categories"


def _fetch_category_tree() -> dict:
    """cid → 대분류명(category1_nm) 매핑. 실패 시 빈 dict."""
    try:
        resp = requests.get(PANDARANK_TREE_URL,
                             headers={"User-Agent": FIXED_UA}, timeout=10)
        resp.raise_for_status()
        item0 = (resp.json().get("items") or [{}])[0]
        mapping = {}
        for v in item0.values():
            if isinstance(v, list):
                for c in v:
                    cid = c.get("category_id")
                    top = c.get("category1_nm")
                    if cid and top:
                        mapping[cid] = top
        return mapping
    except Exception as e:
        log(f"Pandarank 카테고리 트리 조회 실패: {e}", "warn")
        return {}


# ─── 수집 함수 ────────────────────────────────────────────────────────────────

def _fetch_category(cid: str) -> list:
    """단일 카테고리의 bestKeyword 리스트 반환. 실패 시 []."""
    headers = {"User-Agent": FIXED_UA}
    url = PANDARANK_API_URL.format(cid=cid)
    try:
        resp = requests.get(url, headers=headers, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        items = data.get("items", [])
        if not items:
            return []
        best = items[0].get("bestKeyword") or []
        return [
            {
                "keyword": b.get("keyword", "").strip(),
                "rank_change": b.get("rank") or "",
                "count_label": b.get("count") or "",
            }
            for b in best
            if b.get("keyword")
        ]
    except Exception as e:
        log(f"Pandarank 수집 실패 (cid={cid}): {e}", "warn")
        return []


def collect_pandarank_keywords(delay: float = 0.3,
                                include_subcategories: bool = True) -> list:
    """
    판다랭크 카테고리 순회하여 키워드 수집.

    Args:
        delay: 카테고리 간 요청 딜레이 (초)
        include_subcategories: True면 대분류+중분류 194개, False면 대분류 10개만

    Returns:
        [{"keyword": ..., "category": 대분류명, "source": "pandarank",
          "cid": ..., "rank_change": "up/down/-", "monthly": 0}, ...]
        (monthly 는 판다랭크가 제공하지 않으므로 0 고정 — 정렬 시엔 itemscout 가 우선됨)
    """
    raw_cids = PANDARANK_SUB_CATEGORIES if include_subcategories else list(PANDARANK_TOP_CATEGORIES.keys())
    cids = [c for c in raw_cids if c not in PANDARANK_DEAD_CIDS]
    skipped = len(raw_cids) - len(cids)
    log(f"Pandarank 수집 시작 — 대상 카테고리 {len(cids)}개"
        + (f" (dead {skipped}개 자동 스킵)" if skipped else ""), "step")

    cid_to_top = _fetch_category_tree()
    if not cid_to_top:
        cid_to_top = dict(PANDARANK_TOP_CATEGORIES)
        log("카테고리 트리 미사용 — 정적 대분류 매핑 fallback", "warn")
    else:
        unmapped = sum(1 for c in cids if c not in cid_to_top)
        if unmapped:
            log(f"카테고리 트리 매핑 — {len(cids)-unmapped}/{len(cids)} 적중 (미매핑 {unmapped}개는 '기타')", "info")

    results = []
    seen = set()
    ok_count = 0

    for idx, cid in enumerate(cids, 1):
        top_name = cid_to_top.get(cid, "기타")

        items = _fetch_category(cid)
        added = 0
        for it in items:
            kw = it["keyword"]
            if kw and kw not in seen:
                seen.add(kw)
                results.append({
                    "keyword":     kw,
                    "category":    top_name,
                    "source":      "pandarank",
                    "cid":         cid,
                    "rank_change": it["rank_change"],
                    "monthly":     0,
                    "rank":        9999,
                })
                added += 1
        if items:
            ok_count += 1
        if idx % 20 == 0 or idx == len(cids):
            log(f"  진행 {idx}/{len(cids)} — 누계 {len(results)}개 (성공 {ok_count})", "info")
        time.sleep(delay)

    log(f"Pandarank 수집 완료: {len(results)}개 키워드 (성공 카테고리 {ok_count}/{len(cids)})", "ok")
    return results
