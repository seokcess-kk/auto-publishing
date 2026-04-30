"""
상품 포스트 HTML 렌더링 공통 모듈.

쿠팡/알리 등 상품형 파이프라인이 공유하는 카드 템플릿.
차이는 ProductTheme 데이터클래스로 주입.
"""
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


def render_product_post(keyword: str, products: list, theme: ProductTheme,
                        intro_text: str = "") -> tuple:
    """(title, content_html, excerpt, slug) 반환. content 는 wp:html 블록으로 감쌈."""
    if not products:
        return "", "", "", ""

    title = f"{keyword} TOP{len(products)} 추천 - {products[0]['name'][:50]}"
    slug  = products[0]["name"][:69].replace(" ", "-")

    excerpt = theme.excerpt_template.format(keyword=keyword, count=len(products))

    cards_html = "".join(_build_card(i, p, theme) for i, p in enumerate(products))

    intro_html = ""
    if intro_text:
        intro_html = (
            f'<div style="padding:16px 20px;margin:0 auto 20px auto;max-width:680px;'
            f'background:#f8f9fa;border-radius:10px;font-size:14px;line-height:1.8;color:#444;">'
            f'{intro_text}</div>'
        )

    inner_html = (
        f'<div style="max-width:680px;margin:0 auto;padding:20px 16px;'
        f'font-family:-apple-system,\'Noto Sans KR\',sans-serif;">'
        f'<div style="text-align:center;padding:16px 0 20px;color:#555;font-size:14px;line-height:1.6;">'
        f'{theme.header_emoji} {theme.header_prefix} '
        f'<span style="color:{theme.accent_color};font-weight:600;">'
        f'{keyword} 인기상품 TOP{len(products)}</span>'
        f'을 추천합니다</div>'
        f'{intro_html}'
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
