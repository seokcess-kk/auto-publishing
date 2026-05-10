"""
상품 포스트 HTML 렌더링 공통 모듈.

쿠팡/알리 등 상품형 파이프라인이 공유하는 카드 템플릿.
차이는 ProductTheme 데이터클래스로 주입.
"""
import random
from dataclasses import dataclass, field
from typing import List


@dataclass
class ProductTheme:
    """상품 카드 테마 (파이프라인별 차이를 캡슐화)."""
    header_emoji: str          # 예: "📊" 또는 "🛒"
    header_prefix: str         # 예: "데이터 분석 기반" 또는 "알리익스프레스"
    accent_color: str          # 예: "#e4000f" 또는 "#ff4747"
    footer_note: str           # 예: "※ 파트너스 활동을 통해..."
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


def _build_price_html(product: dict, theme: ProductTheme) -> str:
    """가격 HTML 생성 (할인 표시는 theme.show_discount 에 따라)."""
    price    = product.get("price", "")
    discount = product.get("discount_rate", "")
    html = ""
    if theme.show_discount and discount:
        html += (
            f'<span style="color:#999;text-decoration:line-through;'
            f'font-size:13px;margin-right:6px;">{discount}</span>'
        )
    if price:
        html += (
            f'<span style="color:{theme.accent_color};font-size:18px;'
            f'font-weight:bold;">{price}</span>'
        )
    return html


def _build_card(idx: int, product: dict, theme: ProductTheme) -> str:
    """단일 상품 카드 HTML."""
    img     = product.get("image", "")
    aff_url = product.get("affiliate_url", "")
    name    = product.get("name", "")
    price_html = _build_price_html(product, theme)
    meta_html  = _build_meta_html(product, theme.meta_fields)
    return (
        f'<a href="{aff_url}" target="_blank" rel="nofollow" '
        f'style="text-decoration:none;color:inherit;display:block;margin:0 auto 14px auto;max-width:680px;">'
        f'<div style="display:flex;border:1px solid #e0e0e0;border-radius:12px;overflow:hidden;background:#fff;'
        f'box-shadow:0 1px 4px rgba(0,0,0,0.06);">'
        f'<div style="flex:0 0 120px;min-height:120px;background:url(\'{img}\') center/contain no-repeat #f9f9f9;"></div>'
        f'<div style="flex:1;padding:12px 14px;display:flex;flex-direction:column;justify-content:center;">'
        f'<div style="font-size:13px;font-weight:600;line-height:1.4;color:#333;margin-bottom:6px;">'
        f'{idx+1}. {name}</div>'
        f'<div style="margin-bottom:4px;">{price_html}</div>'
        f'<div style="font-size:11px;color:#888;">{meta_html}</div>'
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


def render_product_post(keyword: str, products: list, theme: ProductTheme,
                        intro_text: str = "",
                        pick_reasons: list = None) -> tuple:
    """(title, content_html, excerpt, slug) 반환. content 는 wp:html 블록으로 감쌈."""
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

    inner_html = (
        f'<div style="max-width:680px;margin:0 auto;padding:20px 16px;'
        f'font-family:-apple-system,\'Noto Sans KR\',sans-serif;">'
        f'<div style="text-align:center;padding:16px 0 20px;color:#555;font-size:14px;line-height:1.6;">'
        f'{theme.header_emoji} {theme.header_prefix} '
        f'<span style="color:{theme.accent_color};font-weight:600;">'
        f'{keyword} 인기상품 TOP{len(products)}</span>'
        f'을 추천합니다</div>'
        f'{intro_html}'
        f'{top_cta_html}'
        f'{cards_html}'
        f'<div style="text-align:center;padding:16px 0 8px;font-size:11px;color:#bbb;">'
        f'{theme.footer_note}</div>'
        f'</div>'
    )
    content = f'<!-- wp:html -->{inner_html}<!-- /wp:html -->'

    return title, content, excerpt, slug


# ─── 사전 정의 테마 ──────────────────────────────────────────────────────────

COUPANG_THEME = ProductTheme(
    header_emoji="📊",
    header_prefix="데이터 분석 기반",
    accent_color="#e4000f",
    footer_note="※ 파트너스 활동을 통해 일정액의 수수료를 제공받을 수 있습니다.",
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
    footer_note="※ 알리익스프레스 파트너스 활동을 통해 일정액의 수수료를 제공받을 수 있습니다.",
    show_discount=False,
    meta_fields=["rating:⭐ {}", "sales_num:{} 판매"],
    excerpt_template=(
        "본 상품 키워드({keyword})는 네이버 데이터랩과 아이템스카우트 데이터 조합으로 "
        "선정하였으며, 알리익스프레스 인기/추천 상품 TOP{count}을 추천해 드립니다."
    ),
)
