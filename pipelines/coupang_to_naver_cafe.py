"""
파이프라인: 쿠팡 파트너스 → 네이버 카페 (쇼핑정보 게시판, 메뉴 3).

Old_Source naver_cafe/네이버카페_쿠팡파트너스/...adpick_ver6.py 양식 이식.
1. 쿠팡 검색으로 키워드별 상품 N개 수집
2. 첫 상품 이미지로 'HIT 상품' 카드 합성 (그라데이션 + tile + 2줄 텍스트)
3. 카페 SmartEditor v2 이미지 업로드 (cafe.upphoto)
4. 표 기반 SmartEditor JSON 빌드 — (이미지) → (RECOMMENDED PRODUCTS) → (구매 댓글)
5. 카페 발행 (placeholder articleId 포함) → 발행 후 ID 로 치환·update
6. 댓글: '구입 링크 ▶▶▶ {단축링크} ◀◀◀' + 본문 + 안내

실행:
    python -m pipelines.coupang_to_naver_cafe
"""
from __future__ import annotations

import os
import random

from dotenv import load_dotenv
load_dotenv()

from common.cafe_card import make_hit_card, random_hit_title
from common.cafe_smarteditor import build_coupang_document
from common.logger import log
from common.notifier import notify_pipeline_result
from common.url_shortener import shorten as shorten_url
from publishers.naver_cafe import NaverCafePublisher
from sources.coupang import CoupangSource


SCHEDULE = {
    "env":  "SCHEDULE_COUPANG_NAVER_CAFE",
    "func": "run",
    "args_from_env": ("COUPANG_PRODUCT_COUNT:10:int",),
}


DEFAULT_KEYWORDS = [
    "주방용품", "생활용품", "건강식품", "디지털가전",
    "패션잡화", "스포츠용품", "뷰티", "캠핑용품",
]
NAVER_CAFE_SHOPPING_MENU_ID = "3"


def _get_keywords(n: int = 1) -> list[str]:
    try:
        from sources.itemscout_keywords import get_next_keywords
        keywords = get_next_keywords(n=n, refill_threshold=50)
        if keywords:
            return keywords
    except Exception as e:
        log(f"ItemScout 키워드 풀 실패 ({e}), 기본 키워드 사용", "warn")
    return random.sample(DEFAULT_KEYWORDS, k=min(n, len(DEFAULT_KEYWORDS)))


def _format_price(price: str) -> str:
    """ '12,345원' / '12345' → '12,345' """
    if not price:
        return "0"
    digits = "".join(c for c in str(price) if c.isdigit())
    if not digits:
        return str(price)
    return f"{int(digits):,}"


def run(count_per_keyword: int = 10) -> None:
    cafe_id    = os.getenv("NAVER_CAFE_ID", "")
    username   = os.getenv("NAVER_USERNAME", "")
    password   = os.getenv("NAVER_PASSWORD", "")
    channel_id = (
        os.getenv("COUPANG_CHANNEL_ID_NAVERCAFE")
        or os.getenv("COUPANG_CHANNEL_ID", "")
    )
    if not all([cafe_id, username, password]):
        raise ValueError("NAVER_CAFE_ID, NAVER_USERNAME, NAVER_PASSWORD 필요")

    keyword = _get_keywords(n=1)[0]
    log(f"[쿠팡→네이버카페] 키워드: {keyword}", "step")

    coupang  = CoupangSource(channel_id=channel_id)
    products = coupang.search(keyword, count=count_per_keyword) or []
    if not products:
        log("쿠팡 상품 0건 — 종료", "error")
        notify_pipeline_result("쿠팡→네이버카페", 0, 1, details=f"상품 수집 실패 ({keyword})")
        return

    product = products[0]
    title = (
        f"{random_hit_title('coupang')} - {product.get('name', '')[:60]}"
        if not product.get('name', '').startswith(keyword)
        else f"{keyword} 상품 추천 - {product.get('name', '')[:60]}"
    )

    # 1) HIT 상품 카드 합성
    from datetime import datetime
    today = datetime.now()
    card_path = (
        os.path.dirname(__file__) + "/../data/cafe/coupang/"
        f"{today:%Y-%m-%d_%H%M%S}.png"
    )
    image_url = (product.get("image", "") or "").replace("230x230", "600x600")
    if not image_url:
        log("쿠팡 상품 이미지 URL 없음 — 종료", "error")
        notify_pipeline_result("쿠팡→네이버카페", 0, 1, details="이미지 URL 없음")
        return
    line1 = random_hit_title("coupang")
    line2 = keyword
    saved = make_hit_card(image_url, line1, line2, card_path,
                         tile_path=None)
    log(f"HIT 카드 저장: {saved}", "ok")

    # 2) 카페 publisher 로그인
    cafe = NaverCafePublisher(cafe_id, username, password)
    if not cafe.login():
        log("네이버 카페 로그인 실패", "error")
        notify_pipeline_result("쿠팡→네이버카페", 0, 1, details="로그인 실패")
        return

    # 3) SmartEditor 이미지 업로드
    img_meta = cafe.upload_image_se(str(saved), menu_id=NAVER_CAFE_SHOPPING_MENU_ID)
    if not img_meta:
        log("카페 이미지 업로드 실패 — 종료", "error")
        notify_pipeline_result("쿠팡→네이버카페", 0, 1, details="이미지 업로드 실패")
        return

    # 4) document JSON 빌드 (placeholder)
    cafe_no = cafe.cafe_no or os.getenv("NAVER_CAFE_CLUB_ID", "")
    content_json_template = build_coupang_document(
        image_src=img_meta["src"],
        image_path=img_meta["path"],
        image_filename=img_meta["filename"],
        image_filesize=img_meta["filesize"],
        image_width=img_meta["width"],
        image_height=img_meta["height"],
        cafe_id_no=cafe_no,
        article_id_placeholder="%ARTICLE_ID%",
        product_name=product.get("name", "") or keyword,
        price=_format_price(product.get("price", "0")),
        discount_rate=product.get("discount_rate", "No data") or "No data",
        star_rating=product.get("rating", "No data") or "No data",
        review_count=str(product.get("review_count", "0") or "0"),
    )

    # 5) 발행 (placeholder 포함). 응답으로 받은 articleId 로 치환 후 update.
    result = cafe.post_with_document(
        title=title,
        content_json=content_json_template,
        menu_id=NAVER_CAFE_SHOPPING_MENU_ID,
        tags=[keyword, "인기상품", "추천상품", "연관상품"],
    )
    if not result.success:
        notify_pipeline_result("쿠팡→네이버카페", 0, 1, details=str(result.message))
        return

    article_id = result.post_id or ""
    log(f"카페 발행 성공: {result.url}", "ok")

    # 본문 placeholder 치환 → update
    if article_id:
        try:
            final_json = content_json_template.replace("%ARTICLE_ID%", article_id)
            cafe.update_article(
                article_id=article_id, title=title,
                content_json=final_json, menu_id=NAVER_CAFE_SHOPPING_MENU_ID,
            )
        except Exception as e:
            log(f"placeholder 치환 update 실패 (무시): {e}", "warn")

    # 6) 댓글 — '구입 링크 ▶▶▶ {short_url} ◀◀◀' + 본문 + 안내
    if article_id:
        affiliate_url = product.get("affiliate_url", "") or ""
        try:
            short = shorten_url(affiliate_url) if affiliate_url else ""
        except Exception:
            short = affiliate_url
        comment = (
            f"구입 링크 ▶▶▶ {short or affiliate_url} ◀◀◀\n\n"
            f"{title}\n\n"
            "🥢🥢🥢 파트너스 활동을 통해 일정액의 수수료를 제공받을 수 있습니다. "
            "행운이 가득한 하루 되세요."
        )
        try:
            cafe.post_comment(article_id, comment)
        except Exception as e:
            log(f"댓글 작성 실패 (무시): {e}", "warn")

    if result.url:
        from common.publish_queue import add_url as _add_url
        _add_url(result.url, platform="naver_cafe", title=title)

    notify_pipeline_result(
        "쿠팡→네이버카페", 1, 1,
        details=keyword,
        url=result.url or "",
    )


if __name__ == "__main__":
    run(count_per_keyword=int(os.getenv("COUPANG_PRODUCT_COUNT", "10")))
