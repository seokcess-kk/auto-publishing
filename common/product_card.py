"""
'오늘의 추천 상품' 블록 — 쿠팡 상품 1개를 카드 형태 HTML 로 렌더.

여러 파이프라인(일출일몰, 부동산, 정책 등)에서 본문 하단에 공통으로 붙는
수익화 포인트다. 평문 이미지 카드 1종만 렌더한다.
"""
from __future__ import annotations

import os
import random
from typing import Iterable, Optional

from common.logger import log


# 쿠팡 검색 실패 시 재시도하는 기본 키워드 풀 (장르별 섞어 고정 수익 유지)
_FALLBACK_KEYWORDS = [
    "생활용품",
    "주방용품",
    "책상 정리",
    "캠핑 랜턴",
    "아웃도어 보온병",
    "커피 머신",
    "공기청정기",
    "스탠드 조명",
    "방한 담요",
    "헬스 매트",
]


def fetch_recommend_product(
    keywords: Iterable[str],
    *,
    channel_id: str = "",
    count_per_search: int = 10,
) -> Optional[dict]:
    """주어진 키워드 풀에서 랜덤 선택해 쿠팡 상품 1개를 가져온다.

    Args:
        keywords:         우선 시도할 키워드 목록 (랜덤 1개 선택)
        channel_id:       쿠팡 파트너스 채널 ID
        count_per_search: 검색당 요청 수

    Returns:
        product dict (image, affiliate_url, name, price, ...) 또는 None
    """
    from sources.coupang import CoupangSource

    kw_pool: list[str] = list(keywords) or list(_FALLBACK_KEYWORDS)
    channel_id = channel_id or os.getenv("COUPANG_CHANNEL_ID", "")

    coupang = CoupangSource(channel_id=channel_id)

    # 최대 2회 재시도 (첫 키워드 검색이 0건이면 폴백 키워드 중 다른 것)
    tried: set[str] = set()
    for attempt in range(2):
        candidates = [k for k in kw_pool if k not in tried] or list(_FALLBACK_KEYWORDS)
        keyword = random.choice(candidates)
        tried.add(keyword)
        try:
            products = coupang.search(keyword, count=count_per_search) or []
        except Exception as e:
            log(f"[product_card] 쿠팡 검색 예외 ({keyword}): {e}", "warn")
            products = []
        if products:
            product = products[-1]
            product["_keyword"] = keyword
            log(f"[product_card] 추천 상품 선정: {product.get('name', '')[:40]}", "ok")
            return product
        log(f"[product_card] '{keyword}' 결과 0건, 폴백 시도", "warn")

    log("[product_card] 추천 상품 수집 실패", "warn")
    return None


def render_product_card(product: dict, *, obfuscated: Optional[bool] = None) -> str:
    """쿠팡 상품 dict → '📦 오늘의 추천 상품' 카드 HTML.

    Args:
        product:    image / affiliate_url / name / price / discount_rate /
                    review_count 등을 담은 dict. None/빈 dict 이면 '' 반환.
        obfuscated: deprecated. 호출자 호환을 위해 시그니처만 유지하며 무시된다.
    """
    del obfuscated  # deprecated, intentionally ignored

    if not product:
        return ""

    img      = product.get("image", "")
    aff_url  = product.get("affiliate_url", "")
    name     = product.get("name", "") or "추천 상품"
    price    = product.get("price", "")
    discount = product.get("discount_rate", "")
    review   = product.get("review_count", "")

    price_html = ""
    if discount:
        price_html += (
            f'<span style="color:#999;text-decoration:line-through;'
            f'font-size:12px;margin-right:6px;">{discount}</span>'
        )
    if price:
        price_html += (
            f'<span style="color:#e4000f;font-size:17px;font-weight:bold;">{price}</span>'
        )

    meta = f'{review}개 리뷰' if review and review != "0" else ""

    header = (
        '<div style="max-width:680px;margin:32px auto 12px auto;">'
        '<div style="font-size:13px;color:#888;margin-bottom:8px;">📦 오늘의 추천 상품</div>'
    )
    inner = (
        f'<a href="{aff_url}" target="_blank" rel="nofollow sponsored noopener" '
        'style="text-decoration:none;color:inherit;display:block;">'
        '<div style="display:flex;border:1px solid #e0e0e0;border-radius:12px;overflow:hidden;'
        'background:#fff;box-shadow:0 1px 4px rgba(0,0,0,0.06);">'
        '<div style="flex:0 0 110px;min-height:110px;'
        f'background:url(\'{img}\') center/contain no-repeat #f9f9f9;"></div>'
        '<div style="flex:1;padding:12px 14px;display:flex;flex-direction:column;justify-content:center;">'
        f'<div style="font-size:13px;font-weight:600;line-height:1.4;color:#333;margin-bottom:6px;">{name}</div>'
        f'<div style="margin-bottom:4px;">{price_html}</div>'
        f'<div style="font-size:11px;color:#888;">{meta}</div>'
        '</div></div></a>'
    )
    # 쿠팡 파트너스 의무 고지 — 공정거래위원회 추천·보증 심사지침 대응
    disclosure_footer = (
        '<p style="text-align:center;font-size:11px;color:#bbb;margin:8px 0 0 0;">'
        '※ 쿠팡 파트너스 활동을 통해 일정액의 수수료를 제공받을 수 있습니다.</p>'
        '</div>'
    )
    return header + inner + disclosure_footer


# 부동산 파이프라인에서 자주 쓰일 기본 키워드 (청약/이사 관련)
REALESTATE_DEFAULT_KEYWORDS = [
    "이사용 박스",
    "수납장",
    "책상",
    "거실 조명",
    "커튼",
    "공기청정기",
    "식탁",
    "소파",
    "침대 매트리스",
    "커피 머신",
    "로봇 청소기",
    "주방용품 세트",
    "수건 세트",
    "신발장",
    "아이 책상",
]

# 정책/뉴스 류에서 쓸 범용 키워드
GENERIC_DEFAULT_KEYWORDS = list(_FALLBACK_KEYWORDS)


# ─── 뉴스픽 글 카테고리 코드(CAxxyy) → 본문 맥락에 맞는 상품 키워드 ──────────
# 뉴스픽 API 가 글마다 주는 category 코드의 상위 4자리(CA0N)가 대분류다:
#   CA01 시사(정치/사회/경제일반/사건사고/IT보안), CA02 라이프/문화,
#   CA03 연예, CA04 스포츠, CA09 경제/증시.
# 목적: "한동훈 대선 → 캠핑 랜턴" 같은 무관 상품을 없애고 본문과 연관된 상품만
#       붙인다. 자연스러운 상품이 없는 하드뉴스(CA01/CA09)는 카드를 생략한다.
_CATE_HARD_NEWS_PREFIXES = ("CA01", "CA09")  # 시사·증시 → 상품 카드 생략

_CATE_CODE_KEYWORDS = {
    # ── CA02 라이프/문화 (세분) ──
    "CA0203": ["독서대", "북엔드", "책장", "북라이트", "북커버"],            # 도서
    "CA0204": ["독서대", "북엔드", "책장", "북라이트", "북커버"],            # 문학
    "CA0205": ["여행용 캐리어", "목베개", "여권 케이스", "여행 파우치", "보조배터리"],  # 여행
    "CA0206": ["여름 원피스", "린넨 셔츠", "샌들", "선글라스", "버킷햇"],     # 패션
    "CA0207": ["무선 이어폰", "보조배터리", "스마트워치", "기계식 키보드", "휴대용 SSD"],  # 테크/IT
    "CA0208": ["수납 정리함", "디퓨저", "수면 안대", "텀블러", "무드등"],     # 라이프
    "CA0210": ["인테리어 소품", "디퓨저", "캔들", "무드등", "복주머니"],      # 운세(분위기 소품)
    "CA0215": ["게이밍 마우스", "게이밍 키보드", "게이밍 헤드셋", "마우스패드", "게이밍 의자"],  # 게임
    # ── CA04 스포츠 (세분) ──
    "CA0403": ["야구 글러브", "야구 배트", "야구공", "배팅 장갑", "야구 모자"],   # 야구
    "CA0405": ["축구화", "축구공", "정강이 보호대", "스포츠 양말", "축구 유니폼"],  # 축구
    "CA0408": ["골프공", "골프 장갑", "골프 거리측정기", "골프 티", "골프 우산"],   # 골프
}

# 대분류(CA0N) 폴백 풀 — 세분 코드 매칭이 없을 때
_CATE_GROUP_KEYWORDS = {
    "CA02": list(GENERIC_DEFAULT_KEYWORDS),                                       # 라이프 기타 → 생활용품
    "CA03": ["립밤", "쿠션 팩트", "헤어 에센스", "향수", "패션 악세서리"],          # 연예 → 뷰티/패션(시청자층)
    "CA04": ["러닝화", "스포츠 이어폰", "요가매트", "홈트레이닝 밴드", "스포츠 양말"],  # 스포츠 일반
}


def keywords_for_cate_code(code: str):
    """뉴스픽 글 카테고리 코드(CAxxyy) → 연관 상품 키워드 풀.

    반환:
        list[str] — 해당 글 맥락에 맞는 상품 키워드 (랜덤 1개 검색용)
        None      — 자연스러운 상품이 없는 하드뉴스(CA01/CA09) → 카드 생략

    우선순위: 정확 코드 → 대분류(CA0N) 폴백 → 하드뉴스면 None →
    알 수 없는/빈 코드는 generic(기존 동작 유지, 무관 아님).
    """
    if not code:
        return list(GENERIC_DEFAULT_KEYWORDS)        # 코드 없음 → 기존 동작
    code = code.strip().upper()
    if any(code.startswith(p) for p in _CATE_HARD_NEWS_PREFIXES):
        return None                                  # 시사·증시 → 카드 생략
    if code in _CATE_CODE_KEYWORDS:
        return list(_CATE_CODE_KEYWORDS[code])       # 세분 매칭
    grp = code[:4]
    if grp in _CATE_GROUP_KEYWORDS:
        return list(_CATE_GROUP_KEYWORDS[grp])       # 대분류 폴백
    return list(GENERIC_DEFAULT_KEYWORDS)            # 미지 코드 → generic(안전)
