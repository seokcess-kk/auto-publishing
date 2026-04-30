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
