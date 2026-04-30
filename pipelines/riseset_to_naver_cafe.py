"""
파이프라인: 일출/일몰 → 네이버 카페 (일출일몰 메뉴 8).

Old_Source naver_cafe/네이버카페_일출일몰 양식 이식.
1. 한국천문연구원 일출/일몰 5개 지역 수집
2. 쿠팡 추천 상품 1개 (검색 키워드 = 캠핑 랜턴 등)
3. 상품 이미지로 HIT 일출일몰 카드 합성
4. SmartEditor 이미지 업로드
5. document JSON: 일출일몰 표 (지역×시각) + 추천 상품 카드 + 댓글 링크
6. 발행 후 articleId 치환 update
7. 댓글: 구입 링크 ▶▶▶ {short} ◀◀◀ + 본문 + 안내

실행:
    python -m pipelines.riseset_to_naver_cafe
"""
from __future__ import annotations

import os
import random
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

from common.cafe_card import make_hit_card, random_hit_title
from common.cafe_smarteditor import build_riseset_document
from common.logger import log
from common.notifier import notify_pipeline_result
from common.url_shortener import shorten as shorten_url
from publishers.naver_cafe import NaverCafePublisher
from sources.coupang import CoupangSource
from sources.riseset import RiseSetSource

from pipelines._riseset_common import _LOCATIONS, _SUNRISE_KEYWORDS


SCHEDULE = {
    "env":  "SCHEDULE_RISESET_NAVER_CAFE",
    "func": "run",
}


ROOT = Path(__file__).resolve().parent.parent
NAVER_CAFE_RISESET_MENU_ID = "8"


def _format_price(price: str) -> str:
    if not price:
        return "0"
    digits = "".join(c for c in str(price) if c.isdigit())
    return f"{int(digits):,}" if digits else str(price)


def _build_riseset_lines(info_list: list[dict]) -> list[str]:
    """지역별 일출일몰을 한 줄씩 텍스트 라인으로."""
    if not info_list:
        return []
    today = info_list[0].get("date", "")
    if today and len(today) == 8:
        date_str = f"{today[:4]}년 {today[4:6]}월 {today[6:]}일"
    else:
        date_str = datetime.now().strftime("%Y년 %m월 %d일")

    main = info_list[0]
    lines = [
        f"🌅 일출 {main.get('sunrise', '-')}    🌇 일몰 {main.get('sunset', '-')}",
        f"🌕 월출 {main.get('moonrise', '-')}    🌑 월몰 {main.get('moonset', '-')}",
        "",
        f"📅 {date_str} 지역별 출몰 시각",
    ]
    for info in info_list[:5]:
        lines.append(
            f"  • {info['location']} — 일출 {info.get('sunrise', '-')} / "
            f"일몰 {info.get('sunset', '-')} / "
            f"월출 {info.get('moonrise', '-')} / "
            f"월몰 {info.get('moonset', '-')}"
        )
    lines.append("")
    lines.append("출처: 한국천문연구원 (data.go.kr)")
    return lines


def run() -> None:
    cafe_id  = os.getenv("NAVER_CAFE_ID", "")
    username = os.getenv("NAVER_USERNAME", "")
    password = os.getenv("NAVER_PASSWORD", "")
    if not all([cafe_id, username, password]):
        raise ValueError("NAVER_CAFE_ID, NAVER_USERNAME, NAVER_PASSWORD 필요")

    riseset   = RiseSetSource()
    info_list = riseset.get_multi_location(_LOCATIONS)
    if not info_list:
        log("일출/일몰 데이터 수집 실패", "error")
        notify_pipeline_result("일출일몰→네이버카페", 0, 1, details="일출 API 실패")
        return

    keyword    = random.choice(_SUNRISE_KEYWORDS)
    channel_id = (
        os.getenv("COUPANG_CHANNEL_ID_NAVERCAFE")
        or os.getenv("COUPANG_CHANNEL_ID", "")
    )
    products = []
    try:
        products = CoupangSource(channel_id=channel_id).search(keyword, count=10) or []
    except Exception as e:
        log(f"쿠팡 검색 실패 (무시): {e}", "warn")
    product = products[-1] if products else {}

    today = info_list[0].get("date", "")
    if today and len(today) == 8:
        date_kor = f"{today[:4]}년 {today[4:6]}월 {today[6:]}일"
    else:
        date_kor = datetime.now().strftime("%Y년 %m월 %d일")
    title = f"{date_kor} 일출일몰 시각 — 서울, 부산, 강릉 외"

    # 카드 합성 (상품 이미지 → 일출일몰 컨셉 텍스트)
    image_url = (
        (product.get("image", "") or "").replace("230x230", "600x600")
        or "https://placehold.co/600x600/87CEEB/333333.png"
    )
    out_dir = ROOT / "data" / "cafe" / "riseset"
    out_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.now()
    card_path = out_dir / f"{now:%Y-%m-%d_%H%M%S}.png"
    line1 = random_hit_title("riseset")
    line2 = product.get("name", keyword)[:18] if product else keyword
    saved = make_hit_card(
        image_url, line1, line2, card_path,
        tile_path=ROOT / "resources" / "cafe_assets" / "tiles" / "riseset_tile.png",
    )
    log(f"일출일몰 카드 저장: {saved}", "ok")

    # 카페 publisher 로그인
    cafe = NaverCafePublisher(cafe_id, username, password)
    if not cafe.login():
        log("네이버 카페 로그인 실패", "error")
        notify_pipeline_result("일출일몰→네이버카페", 0, 1, details="로그인 실패")
        return
    cafe_no = cafe.cafe_no or os.getenv("NAVER_CAFE_CLUB_ID", "")

    img_meta = cafe.upload_image_se(str(saved), menu_id=NAVER_CAFE_RISESET_MENU_ID)
    if not img_meta:
        log("카페 이미지 업로드 실패 — 종료", "error")
        notify_pipeline_result("일출일몰→네이버카페", 0, 1, details="이미지 업로드 실패")
        return

    riseset_lines = _build_riseset_lines(info_list)
    content_json_template = build_riseset_document(
        image_src=img_meta["src"],
        image_path=img_meta["path"],
        image_filename=img_meta["filename"],
        image_filesize=img_meta["filesize"],
        image_width=img_meta["width"],
        image_height=img_meta["height"],
        cafe_id_no=cafe_no,
        article_id_placeholder="%ARTICLE_ID%",
        riseset_table_html_lines=riseset_lines,
        product_name=product.get("name", "") or keyword,
        product_price=_format_price(product.get("price", "0")),
        product_review=str(product.get("review_count", "0") or "0"),
    )

    result = cafe.post_with_document(
        title=title,
        content_json=content_json_template,
        menu_id=NAVER_CAFE_RISESET_MENU_ID,
        tags=["일출", "일몰", "일출시간", "일몰시간", "오늘일출", "오늘일몰",
              "월출", "월몰", keyword.replace(" ", "")],
    )
    if not result.success:
        notify_pipeline_result("일출일몰→네이버카페", 0, 1, details=str(result.message))
        return

    article_id = result.post_id or ""
    log(f"카페 발행 성공: {result.url}", "ok")

    if article_id:
        try:
            final_json = content_json_template.replace("%ARTICLE_ID%", article_id)
            cafe.update_article(
                article_id=article_id, title=title,
                content_json=final_json,
                menu_id=NAVER_CAFE_RISESET_MENU_ID,
            )
        except Exception as e:
            log(f"update 실패 (무시): {e}", "warn")

    if article_id and product.get("affiliate_url"):
        try:
            short = shorten_url(product["affiliate_url"])
        except Exception:
            short = product["affiliate_url"]
        comment_lines = [
            f"구입 링크 ▶▶▶ {short or product['affiliate_url']} ◀◀◀",
            "",
            f"{date_kor} 지역별 해당 출몰시각정보 (일출, 일몰, 월출, 월몰)",
            "",
            "🥢🥢🥢 파트너스 활동을 통해 일정액의 수수료를 제공받을 수 있습니다. "
            "행운이 가득한 하루 되세요.",
        ]
        try:
            cafe.post_comment(article_id, "\n".join(comment_lines))
        except Exception as e:
            log(f"댓글 작성 실패 (무시): {e}", "warn")

    notify_pipeline_result(
        "일출일몰→네이버카페", 1, 1,
        details=f"{date_kor} · {result.url}",
    )


if __name__ == "__main__":
    run()
