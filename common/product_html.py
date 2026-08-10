"""
상품 포스트 HTML 렌더링 공통 모듈.

쿠팡/알리 등 상품형 파이프라인이 공유하는 카드 템플릿.
차이는 ProductTheme 데이터클래스로 주입.
"""
import html as _html
import json
import os
import random
import re
from dataclasses import dataclass, field
from typing import List

from common import affiliate_notice as _affiliate_notice


_TAG_RE = re.compile(r"<[^>]+>")


def visible_text_len(html: str) -> int:
    """렌더된 본문에서 태그 제거 후 가시 텍스트 길이.

    파이프라인의 thin content 게이트용 — AI 생성이 통째로 실패해 카드 나열만
    남은 글이 그대로 발행되는 것을 막는 판단 기준.
    """
    if not html:
        return 0
    return len(_TAG_RE.sub("", html).strip())


@dataclass
class ProductTheme:
    """상품 카드 테마 (파이프라인별 차이를 캡슐화)."""
    header_emoji: str          # 예: "📊" 또는 "🛒"
    header_prefix: str         # 예: "데이터 분석 기반" 또는 "알리익스프레스"
    accent_color: str          # 예: "#e4000f" 또는 "#ff4747"
    footer_note: str           # 하단 재고지 (본문 최상단 배너는 notice_source 기준)
    notice_source: str = "coupang"   # common.affiliate_notice 의 제휴처 식별자
    show_discount: bool = False
    meta_fields: List[str] = field(default_factory=list)
    # meta_fields 예: ["rating:⭐ {}", "review_count:{}개 리뷰"]
    excerpt_template: str = (
        "본 상품 키워드({keyword})는 네이버 데이터랩과 아이템스카우트 데이터 조합으로 "
        "선정하였으며, 인기/추천 상품 TOP{count}을 추천해 드립니다."
    )


def _build_meta_html(product: dict, meta_fields: List[str]) -> str:
    """meta_fields 규칙에 따라 상품 메타 문자열 생성."""
    parts = []
    for rule in meta_fields:
        key, _, fmt = rule.partition(":")
        val = product.get(key, "")
        if val and val not in ("No data", "0", ""):
            parts.append(fmt.format(val))
    return " · ".join(parts)


def _parse_count(val) -> int:
    """'1,513' / '5,000+' / '' → 정수. 숫자가 없으면 0."""
    if not val:
        return 0
    import re as _re
    digits = _re.sub(r"[^\d]", "", str(val))
    return int(digits) if digits else 0


def sort_products_by_popularity(products: list) -> list:
    """리뷰수(쿠팡)/판매량(알리) 내림차순 정렬 — 베스트셀러를 1위(상단/CTA)로.

    검색 원순서 대신 사회적 증거가 가장 강한 상품을 맨 앞으로 보내 1위 CTA 와
    상단 카드의 클릭·전환을 높인다. 동점·결측은 원래 순서 유지(stable sort).
    pick_reason 인덱스와 어긋나지 않도록 소스 search() 단계에서 호출해야 한다.
    """
    if not products:
        return products
    return sorted(
        products,
        key=lambda p: max(_parse_count(p.get("review_count")),
                          _parse_count(p.get("sales_num"))),
        reverse=True,
    )


def _build_price_html(product: dict, theme: ProductTheme) -> str:
    """가격 HTML — 정가(취소선) → 할인가(강조) → 할인율(배지) 앵커링.

    정가/할인율은 theme.show_discount 가 True 이고 데이터가 있을 때만 노출하므로
    데이터가 없는 상품은 기존처럼 가격만 표시(비파괴).
    """
    price    = product.get("price", "")
    original = product.get("original_price", "")
    discount = product.get("discount_rate", "")
    html = ""
    if theme.show_discount and original and original != price:
        html += (
            f'<span style="color:#999;text-decoration:line-through;'
            f'font-size:13px;margin-right:6px;">{original}</span>'
        )
    if price:
        html += (
            f'<span style="color:{theme.accent_color};font-size:18px;'
            f'font-weight:bold;">{price}</span>'
        )
    if theme.show_discount and discount:
        html += (
            f'<span style="display:inline-block;margin-left:6px;padding:1px 6px;'
            f'background:{theme.accent_color};color:#fff;font-size:12px;'
            f'font-weight:700;border-radius:4px;vertical-align:middle;">{discount}↓</span>'
        )
    return html


def _build_card(idx: int, product: dict, theme: ProductTheme) -> str:
    """단일 상품 카드 HTML.

    카드 전체가 제휴 링크(<a>)이며, 내부에는 버튼처럼 보이는 <span> 을 둔다
    (<a> 중첩은 비표준이라 금지). 카드 어디를 눌러도 제휴 링크로 이동하지만
    버튼 어포던스가 있어야 모바일 탭 전환이 오른다. arrival_time(빠른배송)이
    있으면 신뢰·긴급 배지로 노출.
    """
    img     = product.get("image", "")
    aff_url = product.get("affiliate_url", "")
    name    = product.get("name", "")
    arrival = (product.get("arrival_time", "") or "").strip()
    price_html = _build_price_html(product, theme)
    meta_html  = _build_meta_html(product, theme.meta_fields)

    arrival_html = ""
    if arrival:
        arrival_html = (
            f'<div style="margin-bottom:4px;"><span style="display:inline-block;'
            f'padding:1px 7px;background:#eafaf1;color:#1a8f4d;font-size:11px;'
            f'font-weight:700;border-radius:4px;">🚀 {arrival}</span></div>'
        )

    button_html = (
        f'<span style="display:inline-block;align-self:flex-start;margin-top:8px;'
        f'padding:7px 16px;background:{theme.accent_color};color:#fff;font-size:12px;'
        f'font-weight:700;border-radius:6px;">최저가 보기 ▶</span>'
    )

    # <img alt> + <h3>: CSS background/일반 div 는 이미지 색인·문서 구조 신호가
    # 전혀 없다. flex 카드 레이아웃은 그대로 유지 (a 내부 h3 는 HTML5 유효).
    alt = _html.escape(name[:60], quote=True)
    return (
        f'<a href="{aff_url}" target="_blank" rel="nofollow sponsored noopener" '
        f'style="text-decoration:none;color:inherit;display:block;margin:0 auto 14px auto;max-width:680px;">'
        f'<div style="display:flex;border:1px solid #e0e0e0;border-radius:12px;overflow:hidden;background:#fff;'
        f'box-shadow:0 1px 4px rgba(0,0,0,0.06);">'
        f'<img src="{img}" alt="{alt}" loading="lazy" '
        f'style="flex:0 0 120px;width:120px;min-height:120px;object-fit:contain;background:#f9f9f9;">'
        f'<div style="flex:1;padding:12px 14px;display:flex;flex-direction:column;justify-content:center;">'
        f'<h3 style="font-size:13px;font-weight:600;line-height:1.4;color:#333;margin:0 0 6px;">'
        f'{idx+1}. {name}</h3>'
        f'{arrival_html}'
        f'<div style="margin-bottom:4px;">{price_html}</div>'
        f'<div style="font-size:11px;color:#888;">{meta_html}</div>'
        f'{button_html}'
        f'</div></div></a>'
    )


def _shorten_product_name(name: str, limit: int) -> str:
    """상품명 끝에서 단어 경계로 절단 — 영문 토큰 잘림 방지."""
    if not name or len(name) <= limit:
        return name or ""
    cut = name[:limit]
    if " " in cut:
        cut = cut.rsplit(" ", 1)[0]
    return cut


def make_product_title(keyword: str, products: list) -> str:
    """발행 제목 생성 — 35~45자 목표.

    1) AI(generate_product_title) 시도 — 성공 시 그 결과 사용
    2) 실패/짧음 시 5개 폴백 템플릿 중 랜덤 선택 (매 발행 다양성 확보)

    모든 폴백 템플릿은 키워드/상품명 길이에 따라 45자 이내로 자동 절단된다.
    """
    if not products:
        return f"{keyword} 추천 모음"

    # 1) AI 우선
    try:
        from common.ai_intro import generate_product_title as _ai_title
        ai = _ai_title(keyword, products)
        if ai and 20 <= len(ai) <= 45:
            return ai
    except Exception:
        pass

    # 2) 폴백 템플릿 — 모두 45자 이내가 되도록 상품명 길이 동적 조정
    n = len(products)
    pname = products[0].get("name", "") or ""
    kw = keyword.strip()

    # 키워드 + 패턴 토큰 길이를 빼고 남은 자리만큼 상품명 절단
    candidates: list = []

    # T1: "{kw} 인기 TOP{n} - {짧은 상품명}"
    fixed = len(kw) + len(f" 인기 TOP{n} - ")
    if fixed < 45:
        candidates.append(f"{kw} 인기 TOP{n} - {_shorten_product_name(pname, 45 - fixed)}")

    # T2: "지금 핫한 {kw} 베스트{n} 모음 - {짧은 상품명}"
    fixed = len(kw) + len(f"지금 핫한  베스트{n} 모음 - ")
    if fixed < 45:
        candidates.append(
            f"지금 핫한 {kw} 베스트{n} 모음 - {_shorten_product_name(pname, 45 - fixed)}"
        )

    # T3: "{kw} 추천 BEST{n}: {짧은 상품명} 외"
    fixed = len(kw) + len(f" 추천 BEST{n}:  외")
    if fixed < 45:
        candidates.append(
            f"{kw} 추천 BEST{n}: {_shorten_product_name(pname, 45 - fixed)} 외"
        )

    # T4: "꼭 알아야 할 {kw} TOP{n} 후기 정리"
    t4 = f"꼭 알아야 할 {kw} TOP{n} 후기 정리"
    if 25 <= len(t4) <= 45:
        candidates.append(t4)

    # T5: "{kw} 살까 말까? 인기 {n}종 비교"
    t5 = f"{kw} 살까 말까? 인기 {n}종 비교"
    if 20 <= len(t5) <= 45:
        candidates.append(t5)

    # 안전망: 어떤 후보도 안 만들어졌으면 기본 템플릿
    if not candidates:
        return f"{kw} TOP{n} 추천"

    return random.choice(candidates)


def _build_top_cta_html(top_product: dict, theme: ProductTheme) -> str:
    """인트로 직후 above-the-fold CTA 박스 — 첫 화면에서 바로 클릭 가능하도록.

    1위 상품의 어필리에이트 링크를 강조한 한 줄 + 버튼 형태.
    """
    aff = top_product.get("affiliate_url", "") or top_product.get("url", "") or ""
    name = (top_product.get("name", "") or "")[:50]
    if not aff or not name:
        return ""
    return (
        f'<div style="text-align:center;margin:0 auto 22px auto;max-width:680px;'
        f'padding:14px 16px;background:#fff8f8;border:1px solid {theme.accent_color}33;'
        f'border-radius:12px;">'
        f'<div style="font-size:14px;color:#333;margin-bottom:10px;line-height:1.5;">'
        f'<span style="color:{theme.accent_color};font-weight:700;">🔥 지금 1위</span> '
        f'<span style="color:#222;">{name}</span></div>'
        f'<a href="{aff}" target="_blank" rel="nofollow sponsored" '
        f'style="display:inline-block;padding:10px 22px;background:{theme.accent_color};'
        f'color:#fff;font-weight:700;font-size:14px;text-decoration:none;border-radius:8px;">'
        f'바로가기 ▶</a></div>'
    )


def _build_pick_reason_html(text: str) -> str:
    """카드 직전에 들어갈 한 줄 후킹/픽 이유 — 본문 spread 와 클릭 유도."""
    if not text:
        return ""
    return (
        f'<div style="text-align:center;margin:6px auto 8px auto;max-width:680px;'
        f'padding:0 12px;font-size:14px;line-height:1.6;color:#444;">'
        f'{text}</div>'
    )


def _build_related_html(related_links: list) -> str:
    """'함께 보면 좋은 글' 내부링크 블록 — 소유 도메인 내 링크 순환으로 체류시간과
    크롤 경로를 만든다. publish_queue.get_recent_by_platform() 결과를 그대로 받는다."""
    if not related_links:
        return ""
    items = "".join(
        f'<li style="margin:5px 0;"><a href="{r["url"]}" '
        f'style="color:#3366cc;font-size:14px;">{_html.escape(r["title"])}</a></li>'
        for r in related_links if r.get("url") and r.get("title")
    )
    if not items:
        return ""
    return (
        f'<div style="margin:22px auto 0;max-width:680px;padding:14px 18px;'
        f'background:#f8f9fa;border-radius:10px;">'
        f'<h2 style="font-size:15px;margin:0 0 8px;color:#333;">함께 보면 좋은 글</h2>'
        f'<ul style="margin:0;padding-left:18px;">{items}</ul></div>'
    )


def _build_comparison_table(keyword: str, products: list,
                            theme: ProductTheme) -> str:
    """상품 한눈에 비교표 — 링크 없는 순수 정보성 <table>.

    카드(제휴 링크) 나열만으로는 애드센스 thin content 판정을 받으므로,
    수집 데이터를 표로 재구성해 '비교' 가치를 더한다. 링크가 없어 제휴 링크
    밀도도 올리지 않는다. 데이터가 있는 컬럼만 동적으로 노출.
    """
    if not products or len(products) < 2:
        return ""

    has_rating = any(p.get("rating") for p in products)
    has_review = any(_parse_count(p.get("review_count")) for p in products)
    has_sales  = any(_parse_count(p.get("sales_num")) for p in products)

    heads = ["순위", "상품명", "가격"]
    if has_rating:
        heads.append("평점")
    if has_review:
        heads.append("리뷰")
    elif has_sales:
        heads.append("판매")

    th = "".join(
        f'<th style="padding:8px 10px;background:#f4f5f7;border:1px solid #e3e5e8;'
        f'font-size:12px;color:#333;white-space:nowrap;">{h}</th>'
        for h in heads
    )

    rows = []
    for i, p in enumerate(products):
        name = _html.escape(_shorten_product_name(p.get("name", "") or "", 42))
        cells = [
            f'<td style="padding:8px 10px;border:1px solid #e3e5e8;font-size:12px;'
            f'text-align:center;font-weight:700;color:{theme.accent_color};">{i + 1}</td>',
            f'<td style="padding:8px 10px;border:1px solid #e3e5e8;font-size:12px;'
            f'color:#333;">{name}</td>',
            f'<td style="padding:8px 10px;border:1px solid #e3e5e8;font-size:12px;'
            f'white-space:nowrap;color:#333;">{p.get("price", "") or "-"}</td>',
        ]
        if has_rating:
            cells.append(
                f'<td style="padding:8px 10px;border:1px solid #e3e5e8;font-size:12px;'
                f'text-align:center;white-space:nowrap;">{p.get("rating", "") or "-"}</td>')
        if has_review:
            cells.append(
                f'<td style="padding:8px 10px;border:1px solid #e3e5e8;font-size:12px;'
                f'text-align:center;white-space:nowrap;">{p.get("review_count", "") or "-"}</td>')
        elif has_sales:
            cells.append(
                f'<td style="padding:8px 10px;border:1px solid #e3e5e8;font-size:12px;'
                f'text-align:center;white-space:nowrap;">{p.get("sales_num", "") or "-"}</td>')
        rows.append(f"<tr>{''.join(cells)}</tr>")

    return (
        f'<h2 style="font-size:18px;margin:26px 0 12px;color:#222;">'
        f'{keyword} 한눈에 비교</h2>'
        f'<div style="overflow-x:auto;margin:0 auto 6px;max-width:680px;">'
        f'<table style="width:100%;border-collapse:collapse;background:#fff;">'
        f'<thead><tr>{th}</tr></thead>'
        f'<tbody>{"".join(rows)}</tbody></table></div>'
    )


def _build_itemlist_jsonld(keyword: str, products: list) -> str:
    """ItemList JSON-LD — PRODUCT_JSONLD=true 일 때만 본문에 삽입.

    Tistory 에디터(TinyMCE setContent)가 <script> 를 스트립할 가능성이 있어
    기본 off. 실발행 1건으로 잔존 확인 후 .env 에서 켠다.
    """
    if os.getenv("PRODUCT_JSONLD", "false").strip().lower() not in ("1", "true", "yes"):
        return ""
    items = []
    for i, p in enumerate(products):
        name = (p.get("name") or "").strip()
        if not name:
            continue
        item = {"@type": "ListItem", "position": i + 1, "name": name}
        if p.get("image"):
            item["image"] = p["image"]
        items.append(item)
    if not items:
        return ""
    data = {
        "@context": "https://schema.org",
        "@type": "ItemList",
        "name": f"{keyword} 추천 TOP{len(items)}",
        "itemListElement": items,
    }
    return ('<script type="application/ld+json">'
            + json.dumps(data, ensure_ascii=False) + "</script>")


def _style_guide_html(guide_html: str) -> str:
    """AI 구매 가이드(<h2>/<h3>/<p> 무스타일)를 포스트 톤에 맞춰 인라인 스타일링."""
    if not guide_html:
        return ""
    g = guide_html
    g = g.replace("<h2>", '<h2 style="font-size:18px;margin:26px 0 12px;color:#222;">')
    g = g.replace("<h3>", '<h3 style="font-size:15px;margin:18px 0 8px;color:#333;">')
    g = g.replace("<p>", '<p style="font-size:14px;line-height:1.8;color:#444;margin:0 0 12px;">')
    return g


def render_product_post(keyword: str, products: list, theme: ProductTheme,
                        intro_text: str = "",
                        pick_reasons: list = None,
                        related_links: list = None,
                        guide_html: str = "") -> tuple:
    """(title, content_html, excerpt, slug) 반환. content 는 wp:html 블록으로 감쌈.

    related_links: publish_queue 최근 글 [{url,title},...] — 있을 때만 내부링크
    블록 렌더 (WP 커널 등 기존 호출처는 None 그대로 → 비회귀).
    guide_html: generate_product_guide() 결과 (<h2>/<h3>/<p>) — 카드 아래
    구매 가이드+FAQ 섹션. 빈 값이면 기존 레이아웃 그대로 (비회귀).
    """
    if not products:
        return "", "", "", ""

    title = make_product_title(keyword, products)
    slug  = products[0]["name"][:69].replace(" ", "-")

    excerpt = theme.excerpt_template.format(keyword=keyword, count=len(products))

    # 카드 직전 픽 이유 한 줄 + 카드 — interleave
    card_blocks = []
    for i, p in enumerate(products):
        pr = (pick_reasons[i] if pick_reasons and i < len(pick_reasons) else "").strip()
        card_blocks.append(_build_pick_reason_html(pr))
        card_blocks.append(_build_card(i, p, theme))
    cards_html = "".join(card_blocks)

    intro_html = ""
    if intro_text:
        intro_html = (
            f'<div style="padding:16px 20px;margin:0 auto 16px auto;max-width:680px;'
            f'background:#f8f9fa;border-radius:10px;font-size:14px;line-height:1.8;color:#444;">'
            f'{intro_text}</div>'
        )

    top_cta_html = _build_top_cta_html(products[0], theme)

    # h2 섹션 헤딩 — 키워드가 든 문서 구조 신호 (기존 리드 문구는 스타일 유지)
    section_h2 = (
        f'<h2 style="font-size:18px;margin:8px 0 14px;color:#222;">'
        f'{keyword} 추천 순위 TOP{len(products)}</h2>'
    )
    related_html = _build_related_html(related_links or [])
    jsonld_html = _build_itemlist_jsonld(keyword, products)
    table_html = _build_comparison_table(keyword, products, theme)
    guide_styled = _style_guide_html(guide_html)

    # 대가성 문구는 반드시 본문 최상단 — 쿠팡 파트너스 심사 요건(2026-08 반려
    # 사유). 헤더·CTA·이미지보다 앞에 와야 한다.
    notice_html = _affiliate_notice.notice_html(theme.notice_source)

    inner_html = (
        f'<div style="max-width:680px;margin:0 auto;padding:20px 16px;'
        f'font-family:-apple-system,\'Noto Sans KR\',sans-serif;">'
        f'{notice_html}'
        f'<div style="text-align:center;padding:16px 0 20px;color:#555;font-size:14px;line-height:1.6;">'
        f'{theme.header_emoji} {theme.header_prefix} '
        f'<span style="color:{theme.accent_color};font-weight:600;">'
        f'{keyword} 인기상품 TOP{len(products)}</span>'
        f'을 추천합니다</div>'
        f'{top_cta_html}'
        f'{intro_html}'
        f'{section_h2}'
        f'{cards_html}'
        f'{table_html}'
        f'{guide_styled}'
        f'{related_html}'
        f'<div style="text-align:center;padding:16px 0 8px;font-size:11px;color:#bbb;">'
        f'{theme.footer_note}</div>'
        f'{jsonld_html}'
        f'</div>'
    )
    content = f'<!-- wp:html -->{inner_html}<!-- /wp:html -->'

    return title, content, excerpt, slug


# ─── 사전 정의 테마 ──────────────────────────────────────────────────────────

COUPANG_THEME = ProductTheme(
    header_emoji="📊",
    header_prefix="데이터 분석 기반",
    accent_color="#e4000f",
    footer_note="※ 이 게시물은 쿠팡 파트너스 활동의 일환으로, "
                "이에 따른 일정액의 수수료를 제공받습니다.",
    notice_source=_affiliate_notice.COUPANG,
    show_discount=True,
    meta_fields=["rating:⭐ {}", "review_count:{}개 리뷰"],
    excerpt_template=(
        "본 상품 키워드({keyword})는 네이버 데이터랩(naver datalab)과 "
        "아이템 스카우트(item scout)의 데이터를 조합하여 선정하였으며, "
        "인기/추천 상품 리스트 TOP{count}을 추천해 드립니다."
    ),
)

ALIEXPRESS_THEME = ProductTheme(
    header_emoji="🛒",
    header_prefix="알리익스프레스",
    accent_color="#ff4747",
    footer_note="※ 이 게시물은 알리익스프레스 파트너스 활동의 일환으로, "
                "이에 따른 일정액의 수수료를 제공받습니다.",
    notice_source=_affiliate_notice.ALIEXPRESS,
    show_discount=True,
    meta_fields=["rating:⭐ {}", "sales_num:{} 판매"],
    excerpt_template=(
        "본 상품 키워드({keyword})는 네이버 데이터랩과 아이템스카우트 데이터 조합으로 "
        "선정하였으며, 알리익스프레스 인기/추천 상품 TOP{count}을 추천해 드립니다."
    ),
)
