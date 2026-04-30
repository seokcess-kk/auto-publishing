"""
일출/일몰 파이프라인 공통 빌더.

- 지역/키워드 상수
- AI 도입부 (common.ai_intro.generate_text 재사용)
- HTML 카드/표/상품 카드 빌더

riseset_to_naver, riseset_to_tistory 가 공유.
"""
from typing import Optional

from common.ai_intro import generate_text
from common.logger import log
from common.product_card import render_product_card


# ─── 상수 ────────────────────────────────────────────────────────────────────

# 일출 관련 쿠팡 검색 키워드 목록 (랜덤 선택)
_SUNRISE_KEYWORDS = [
    "일출 명소 여행",
    "캠핑 랜턴",
    "등산 헤드랜턴",
    "아웃도어 보온병",
    "등산 스틱",
    "트레킹화",
    "방한 장갑",
    "새벽 러닝 반사 조끼",
    "망원경",
    "카메라 삼각대",
    "일출 촬영 카메라",
    "자동차 담요",
    "핫팩",
    "등산 배낭",
    "캠핑 체어",
]

# 주요 관측 지역 (일출 명소 중심)
_LOCATIONS = [
    {"name": "서울",   "lon": "126.9783882", "lat": "37.5666103"},
    {"name": "부산",   "lon": "129.0756416", "lat": "35.1795543"},
    {"name": "강릉",   "lon": "128.8760573", "lat": "37.7518979"},
    {"name": "제주",   "lon": "126.5311884", "lat": "33.4890113"},
    {"name": "여수",   "lon": "127.6622052", "lat": "34.7603541"},
]


# ─── AI 도입부 ───────────────────────────────────────────────────────────────

def generate_intro(date_str: str, locations: list, keyword: str) -> str:
    """일출 정보 + 키워드 기반 도입부 생성."""
    loc_names = ", ".join(d["location"] for d in locations[:3] if d)

    prompt = (
        f"오늘({date_str}) 주요 지역({loc_names})의 일출/일몰 정보를 안내하는 "
        f"블로그 포스트 도입부를 작성해줘.\n"
        f"조건:\n"
        f"- 150~250자 내외\n"
        f"- 일출/일몰 시각을 확인하면 좋은 이유나 팁 1~2가지 포함\n"
        f"- '{keyword}' 관련 상품도 자연스럽게 언급\n"
        f"- 자연스럽고 따뜻한 톤\n"
        f"- 순수 텍스트만, HTML 태그·마크다운 없이\n"
        f"- '~입니다', '~드립니다' 체 사용"
    )

    log(f"AI 일출 도입부 생성: {keyword}", "step")
    return generate_text(prompt, max_len=400)


# ─── HTML 헬퍼 ───────────────────────────────────────────────────────────────

def _time_card(label: str, value: str, text_color: str, bg_color: str) -> str:
    return (
        f'<div style="flex:1;min-width:120px;background:{bg_color};border-radius:10px;'
        f'padding:14px 10px;text-align:center;">'
        f'<div style="font-size:13px;color:#777;margin-bottom:4px;">{label}</div>'
        f'<div style="font-size:22px;font-weight:700;color:{text_color};">{value}</div>'
        f'</div>'
    )


# ─── HTML 콘텐츠 빌드 ────────────────────────────────────────────────────────

def build_content(info_list: list, product: dict,
                  *, obfuscate_product: Optional[bool] = None) -> tuple:
    """포스트 제목·HTML 본문·태그 반환.

    Args:
        info_list:         일출·일몰 지역 정보
        product:           쿠팡 상품 dict (없으면 상품 카드 생략)
        obfuscate_product: deprecated. 시그니처 호환을 위해 유지되며 무시된다.

    Returns:
        (title, content_html, tags)
    """
    if not info_list:
        return "", "", []

    today     = info_list[0]["date"]           # "20260416"
    y, m, d   = today[:4], today[4:6], today[6:]
    date_kor  = f"{y}년 {m}월 {d}일"
    main_loc  = info_list[0]                   # 첫 번째 지역(서울) 대표
    keyword   = product.get("_keyword", "캠핑 랜턴") if product else "일출 여행"

    title = f"{date_kor} 일출일몰 시각 — {', '.join(i['location'] for i in info_list[:3])} 외"

    # (도입부는 run()에서 generate_intro()로 별도 생성 → intro kwarg으로 전달)
    intro_html = ""

    # ── 대표 일출/일몰 시각 카드 (서울 기준)
    highlight_html = (
        f'<div style="display:flex;gap:12px;justify-content:center;flex-wrap:wrap;'
        f'margin:0 auto 24px auto;max-width:680px;">'
        + _time_card("🌅 일출", main_loc["sunrise"], "#FF8F00", "#FFF8E1")
        + _time_card("🌇 일몰", main_loc["sunset"],  "#EF6C00", "#FBE9E7")
        + _time_card("🌕 월출", main_loc["moonrise"], "#5C6BC0", "#E8EAF6")
        + _time_card("🌑 월몰", main_loc["moonset"],  "#37474F", "#ECEFF1")
        + f'</div>'
    )

    # ── 지역별 표
    rows = ""
    for info in info_list:
        rows += (
            f'<tr>'
            f'<td style="padding:8px 12px;text-align:center;font-weight:600;">{info["location"]}</td>'
            f'<td style="padding:8px 12px;text-align:center;color:#FF8F00;">{info["sunrise"]}</td>'
            f'<td style="padding:8px 12px;text-align:center;color:#EF6C00;">{info["sunset"]}</td>'
            f'<td style="padding:8px 12px;text-align:center;color:#5C6BC0;">{info["moonrise"]}</td>'
            f'<td style="padding:8px 12px;text-align:center;color:#37474F;">{info["moonset"]}</td>'
            f'<td style="padding:8px 12px;text-align:center;font-size:12px;color:#888;">{info.get("civil_twilight_begin","")} / {info.get("civil_twilight_end","")}</td>'
            f'</tr>'
        )

    table_html = (
        f'<div style="max-width:680px;margin:0 auto 28px auto;overflow-x:auto;">'
        f'<table style="width:100%;border-collapse:collapse;font-size:13px;">'
        f'<thead>'
        f'<tr style="background:#FFF3E0;">'
        f'<th style="padding:10px 12px;">지역</th>'
        f'<th style="padding:10px 12px;color:#FF8F00;">일출</th>'
        f'<th style="padding:10px 12px;color:#EF6C00;">일몰</th>'
        f'<th style="padding:10px 12px;color:#5C6BC0;">월출</th>'
        f'<th style="padding:10px 12px;color:#37474F;">월몰</th>'
        f'<th style="padding:10px 12px;font-size:12px;color:#888;">박명 시작/끝</th>'
        f'</tr>'
        f'</thead>'
        f'<tbody>{rows}</tbody>'
        f'</table>'
        f'<p style="text-align:right;font-size:11px;color:#bbb;margin-top:4px;">'
        f'출처: 한국천문연구원 (data.go.kr)</p>'
        f'</div>'
    )

    # ── 쿠팡 상품 카드 (공통 product_card 재사용)
    product_html = (
        render_product_card(product, obfuscated=obfuscate_product)
        if product else ""
    )

    # ── 전체 래퍼
    content = (
        f'<div style="max-width:680px;margin:0 auto;padding:20px 16px;'
        f'font-family:-apple-system,\'Noto Sans KR\',sans-serif;">'
        f'<h2 style="font-size:18px;color:#333;margin-bottom:16px;">'
        f'🌅 {date_kor} 일출·일몰 시각 안내</h2>'
        f'{intro_html}'
        f'{highlight_html}'
        f'{table_html}'
        f'{product_html}'
        f'</div>'
    )

    tags = ["일출", "일몰", "일출시간", "일몰시간", "오늘일출", "오늘일몰",
            "월출", "월몰", keyword.replace(" ", "")]
    return title, content, tags[:10]
