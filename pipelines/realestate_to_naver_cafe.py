"""
파이프라인: 청약 분양정보 → 네이버 카페 (분양정보, 메뉴 7).

Old_Source naver_cafe/네이버카페_부동산정보 양식 이식.
1. 청약홈 분양정보 1건 선정
2. HIT 분양정보 카드 3장 합성 (네이버 이미지 검색에서 키워드 이미지 가져와 합성)
3. SmartEditor 이미지 업로드
4. 도입부 + 요약 정보 + 상세 정보 + 사진 정보&링크 정보 + RECOMMENDED PRODUCTS
5. 발행 후 articleId 로 치환 update
6. 댓글: 구입 링크 ▶▶▶ {short_url} ◀◀◀ + 본문 + 안내

실행:
    python -m pipelines.realestate_to_naver_cafe
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
load_dotenv()

from common.cafe_card import make_hit_card, random_hit_title
from common.cafe_smarteditor import build_realestate_document
from common.logger import log
from common.notifier import notify_pipeline_result
from common.url_shortener import shorten as shorten_url
from publishers.naver_cafe import NaverCafePublisher
from sources.coupang import CoupangSource
from sources.realestate import RealestateSource


SCHEDULE = {
    "env":  "SCHEDULE_REALESTATE_NAVER_CAFE",
    "func": "run",
    "args_from_env": ("POST_COUNT:1:int",),
}


ROOT = Path(__file__).resolve().parent.parent
HISTORY_PATH = ROOT / "data" / "realestate_cafe_published.json"
NAVER_CAFE_REALESTATE_MENU_ID = "7"
DEFAULT_REGIONS = ["서울", "경기", "인천"]


def _load_history() -> set[str]:
    try:
        with open(HISTORY_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return set(data.get("published", []))
    except (FileNotFoundError, json.JSONDecodeError):
        return set()


def _save_history(keys: set[str]) -> None:
    HISTORY_PATH.parent.mkdir(exist_ok=True)
    with open(HISTORY_PATH, "w", encoding="utf-8") as f:
        json.dump(
            {"published": sorted(keys),
             "updated_at": datetime.now().isoformat(timespec="seconds")},
            f, ensure_ascii=False, indent=2,
        )


def _placeholder_image_url() -> str:
    """카드 합성용 placeholder 이미지 (네이버 검색 이미지 대체)."""
    # 단색 placeholder (Old_Source 는 네이버 이미지 검색을 썼지만 안정성 위해
    # 단색 그라데이션 카드면 충분 — 텍스트가 메인이라)
    return "https://placehold.co/600x600/f0f0f0/cccccc.png"


def run(count: int = 1, days_ahead: int = 30,
        regions: Optional[list[str]] = None) -> None:
    cafe_id  = os.getenv("NAVER_CAFE_ID", "")
    username = os.getenv("NAVER_USERNAME", "")
    password = os.getenv("NAVER_PASSWORD", "")
    if not all([cafe_id, username, password]):
        raise ValueError("NAVER_CAFE_ID, NAVER_USERNAME, NAVER_PASSWORD 필요")

    if regions is None:
        env_regions = os.getenv("REALESTATE_REGIONS", "").strip()
        regions = (
            [r.strip() for r in env_regions.split(",") if r.strip()]
            or list(DEFAULT_REGIONS)
        )

    realestate = RealestateSource()
    all_items = (
        realestate.get_apt_subscriptions(per_page=100)
        + realestate.get_urbty_subscriptions(per_page=100)
    )

    candidates: list[dict] = []
    seen: set[str] = set()
    for region in regions:
        for it in realestate.filter_upcoming(all_items,
                                             days_ahead=days_ahead, region=region):
            mgmt = it.get("HOUSE_MANAGE_NO") or ""
            if mgmt in seen:
                continue
            seen.add(mgmt)
            candidates.append(it)

    history = _load_history()
    fresh = [c for c in candidates if (c.get("HOUSE_MANAGE_NO") or "") not in history]
    log(f"후보: 전체 {len(candidates)}건 중 신규 {len(fresh)}건", "info")
    if not fresh:
        log("발행할 신규 분양 단지 없음", "warn")
        notify_pipeline_result("부동산→네이버카페", 0, count, details="신규 분양 없음", reason="empty")
        return

    targets = fresh[:count]

    # 쿠팡 추천 상품 1개 (RECOMMENDED PRODUCTS 박스용)
    channel_id = (
        os.getenv("COUPANG_CHANNEL_ID_NAVERCAFE")
        or os.getenv("COUPANG_CHANNEL_ID", "")
    )
    products: list[dict] = []
    try:
        products = CoupangSource(channel_id=channel_id).search("부동산", count=10) or []
    except Exception as e:
        log(f"쿠팡 추천 상품 수집 실패 (무시): {e}", "warn")
    product = products[0] if products else {}

    # 카페 publisher 로그인
    cafe = NaverCafePublisher(cafe_id, username, password)
    if not cafe.login():
        log("네이버 카페 로그인 실패", "error")
        notify_pipeline_result("부동산→네이버카페", 0, count, details="로그인 실패")
        return
    cafe_no = cafe.cafe_no or os.getenv("NAVER_CAFE_CLUB_ID", "")

    today = datetime.now()
    published = 0
    for it in targets:
        house_nm  = it.get("HOUSE_NM", "")
        area_nm   = it.get("SUBSCRPT_AREA_CODE_NM", "")
        title = f"{area_nm} APT 분양 정보 {house_nm}".strip()

        # HIT 분양정보 카드 1장 (사진 정보 섹션 제거로 표지 카드 1장으로 충분)
        from pathlib import Path as _P
        out_dir = _P(ROOT / "data" / "cafe" / "realestate")
        out_dir.mkdir(parents=True, exist_ok=True)
        try:
            card_path = make_hit_card(
                _placeholder_image_url(),
                "핫핫!! 분양정보", house_nm,
                out_dir / f"{today:%Y-%m-%d_%H%M%S}.png",
                tile_path=ROOT / "resources" / "cafe_assets" / "tiles" / "realestate_tile.png",
            )
        except Exception as e:
            log(f"카드 합성 실패 — 글 발행 중단: {e}", "error")
            continue

        # 카페 SE 이미지 업로드
        uploaded_meta = cafe.upload_image_se(str(card_path), menu_id=NAVER_CAFE_REALESTATE_MENU_ID)
        if not uploaded_meta:
            log("이미지 업로드 실패 — 글 발행 중단", "error")
            continue
        uploaded = [uploaded_meta]

        # detail dict 에 RECOMMENDED PRODUCTS 박스용 값을 비공식 키로 주입
        detail = dict(it)
        detail["__product_name__"] = product.get("name", "") or "추천 상품"
        detail["__price__"]    = str(product.get("price", "0") or "0").replace(",", "")
        detail["__discount__"] = product.get("discount_rate", "No data") or "No data"
        detail["__rating__"]   = product.get("rating", "5.0") or "5.0"
        detail["__review__"]   = str(product.get("review_count", "0") or "0")

        content_json_template = build_realestate_document(
            images=uploaded,
            cafe_id_no=cafe_no,
            article_id_placeholder="%ARTICLE_ID%",
            summary=it,
            detail=detail,
            intro_area=area_nm,
            intro_house=house_nm,
            intro_rcept=it.get("RCEPT_BGNDE", "-"),
        )

        result = cafe.post_with_document(
            title=title,
            content_json=content_json_template,
            menu_id=NAVER_CAFE_REALESTATE_MENU_ID,
            tags=["분양정보", "부동산", area_nm, house_nm.replace(" ", "")],
        )
        if not result.success:
            log(f"발행 실패: {result.message}", "error")
            continue

        article_id = result.post_id or ""
        published += 1
        mgmt = it.get("HOUSE_MANAGE_NO") or ""
        if mgmt:
            history.add(mgmt)
            _save_history(history)

        # placeholder 치환 update
        if article_id:
            try:
                final_json = content_json_template.replace("%ARTICLE_ID%", article_id)
                cafe.update_article(
                    article_id=article_id, title=title,
                    content_json=final_json,
                    menu_id=NAVER_CAFE_REALESTATE_MENU_ID,
                )
            except Exception as e:
                log(f"update 실패 (무시): {e}", "warn")

        # 댓글 — RECOMMENDED PRODUCTS 박스의 쿠팡 상품 affiliate URL 로 단축
        if article_id:
            affiliate_url = (product.get("affiliate_url") or "").strip()
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

    notify_pipeline_result(
        "부동산→네이버카페", published, count,
        details=f"지역: {', '.join(regions)}",
    )


if __name__ == "__main__":
    run(count=int(os.getenv("POST_COUNT", "1")))
