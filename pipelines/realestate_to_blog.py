"""
파이프라인: 청약 분양정보 → 티스토리 / 네이버 블로그 (단지별 개별 포스트).

각 분양 단지 1건당 블로그 글 1개를 발행한다. 구 Old_Source 의 요약/상세
레이아웃을 유지하면서 CTA, 요약 카드, 해시태그를 advanced 수준으로 보강.

중복 발행 방지:
    data/realestate_published.json 에 HOUSE_MANAGE_NO 를 기록해 이미 발행한
    단지는 건너뛴다.

실행:
    python -m pipelines.realestate_to_blog
    POST_COUNT=1 python -m pipelines.realestate_to_blog   # 테스트
"""
import json
import os
import random
import time
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

from common.logger import log
from common.product_card import (
    REALESTATE_DEFAULT_KEYWORDS,
    fetch_recommend_product,
    render_product_card,
)
from common.tistory_blogs import resolve_blog_name
from sources.realestate import RealestateSource
from publishers.tistory import TistoryPublisher
from publishers.naver_blog import NaverBlogPublisher


def _maybe_close(publisher) -> None:
    """Publisher 에 close() 가 있으면 호출 (Playwright context 정리용)."""
    close = getattr(publisher, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            pass


SCHEDULE = {
    "env":  "SCHEDULE_REALESTATE",
    "func": "run",
    "args_from_env": ("POST_COUNT:1:int",),
}


ROOT = Path(__file__).resolve().parent.parent
HISTORY_PATH = ROOT / "data" / "realestate_published.json"


# ─── 중복 발행 이력 ──────────────────────────────────────────────────────────
def _load_history() -> set[str]:
    try:
        with open(HISTORY_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return set(data.get("published", []))
    except (FileNotFoundError, json.JSONDecodeError):
        return set()


def _save_history(keys: set[str]) -> None:
    HISTORY_PATH.parent.mkdir(exist_ok=True)
    payload = {
        "published": sorted(keys),
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    }
    with open(HISTORY_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def run(count: int = 1,
        target: str = "tistory",
        regions: list[str] = None,
        days_ahead: int = 30) -> None:
    """청약 분양 단지 개별 포스트 N 건 발행.

    Args:
        count:      최대 발행 건수 (기본 3)
        target:     'tistory' | 'naver'
        regions:    SUBSCRPT_AREA_CODE_NM 필터 (예: ['서울','경기']). None 이면 전국.
        days_ahead: 오늘부터 N 일 이내 접수 건만 포함.
    """
    if regions is None:
        env_regions = os.getenv("REALESTATE_REGIONS", "").strip()
        if env_regions:
            regions = [r.strip() for r in env_regions.split(",") if r.strip()]
        else:
            regions = None  # None = 지역 필터 해제 (전국)

    realestate = RealestateSource()

    # APT + 오피스텔/임대 모두 수집 (REST API — Playwright 무관)
    all_items: list[dict] = []
    all_items += realestate.get_apt_subscriptions(per_page=100)
    all_items += realestate.get_urbty_subscriptions(per_page=100)

    # 지역 필터링 + 다가오는 접수 건만
    if regions:
        candidates: list[dict] = []
        seen_mgmt: set[str] = set()
        for region in regions:
            for it in realestate.filter_upcoming(
                all_items, days_ahead=days_ahead, region=region,
            ):
                mgmt = it.get("HOUSE_MANAGE_NO") or ""
                if mgmt in seen_mgmt:
                    continue
                seen_mgmt.add(mgmt)
                candidates.append(it)
    else:
        candidates = realestate.filter_upcoming(all_items, days_ahead=days_ahead)

    # 이미 발행한 건 제외
    history = _load_history()
    fresh = [c for c in candidates if (c.get("HOUSE_MANAGE_NO") or "") not in history]

    log(f"후보: 전체 {len(candidates)}건 중 신규 {len(fresh)}건 (이력 {len(history)}건 스킵)", "info")

    if not fresh:
        log("발행할 신규 분양 단지 없음", "warn")
        from common.notifier import notify_pipeline_result
        notify_pipeline_result(
            f"부동산→{target}", 0, count, details="신규 분양 없음",
        )
        return

    # 단지별 추천 상품을 publisher 로그인 전에 미리 수집해 둔다.
    # (티스토리 publisher 가 sync_playwright 를 켠 뒤 쿠팡 source 가 또
    # sync_playwright 를 호출하면 'inside asyncio loop' 충돌)
    channel_id = os.getenv("COUPANG_CHANNEL_ID_REALESTATE") or os.getenv("COUPANG_CHANNEL_ID", "")
    targets = fresh[:count]
    products: list[dict | None] = []
    for _ in targets:
        products.append(fetch_recommend_product(
            REALESTATE_DEFAULT_KEYWORDS, channel_id=channel_id,
        ))

    # 발행기 초기화 + 로그인 (sync_playwright 켜짐)
    if target == "tistory":
        blog_name = resolve_blog_name("realestate")
        pub = TistoryPublisher(blog_name)
    else:
        blog_id  = os.getenv("NAVER_BLOG_ID", "")
        username = os.getenv("NAVER_USERNAME", "")
        password = os.getenv("NAVER_PASSWORD", "")
        pub = NaverBlogPublisher(blog_id, username, password)

    if not pub.login():
        log("로그인 실패", "error")
        from common.notifier import notify_pipeline_result
        notify_pipeline_result(
            f"부동산→{target}", 0, count, details="로그인 실패",
        )
        _maybe_close(pub)
        return

    # 발행
    published = 0
    last_url = ""
    try:
        for it, product in zip(targets, products):
            post = realestate.build_post(it)
            title = post["title"]
            log(f"발행 시도: {title}", "step")

            # 네이버 블로그는 inline <script> 를 필터링하므로 난독화 비활성.
            obfuscate = False if target == "naver" else None  # None → env 따름
            content = post["content"] + render_product_card(product, obfuscated=obfuscate)

            post_kwargs = dict(
                title=title,
                content=content,
                tags=post["tags"][:10],  # 태그 과다 방지
                category=os.getenv("REALESTATE_CATEGORY", "부동산"),
            )
            if target == "naver":
                cat_no = int(
                    os.getenv("NAVER_REALESTATE_CATEGORY_NO")
                    or os.getenv("NAVER_RISESET_CATEGORY_NO")
                    or "1"
                )
                post_kwargs["category_no"] = cat_no

            result = pub.post(**post_kwargs)
            if result.success:
                published += 1
                mgmt = post["house_manage_no"]
                if mgmt and mgmt != "-":
                    history.add(mgmt)
                    _save_history(history)
                log(f"발행 완료: {title} — {result.url or ''}", "ok")
                if result.url:
                    last_url = result.url
                    plat = "tistory" if "tistory" in result.url else "wordpress"
                    from common.publish_queue import add_url as _add_url
                    _add_url(result.url, platform=plat, title=title)
            else:
                log(f"발행 실패: {title} — {result.message}", "error")

            time.sleep(random.uniform(10, 20))
    finally:
        _maybe_close(pub)

    log(f"부동산→블로그 완료: {published}/{count}건", "step")

    from common.notifier import notify_pipeline_result
    details = f"지역: {', '.join(regions)}" if regions else "전국"
    notify_pipeline_result(
        f"부동산→{target}", published, count, details=details, url=last_url,
    )


if __name__ == "__main__":
    run(
        count=int(os.getenv("POST_COUNT", "1")),
        target=os.getenv("BLOG_TARGET", "tistory"),
    )
