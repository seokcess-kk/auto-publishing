"""
상품→WordPress 파이프라인 공통 골격.

구조:
    profile load → login → keyword loop (search → render → post) → notify
각 파이프라인(쿠팡/알리)은 ProductWpConfig 만 정의하여 run(cfg) 호출.
"""
import os
import random
import time
from dataclasses import dataclass, field
from typing import Callable

from common.ai_intro import generate_product_intro, generate_product_pick_reasons
from common.logger import log
from common.notifier import notify_pipeline_result
from common.product_html import ProductTheme, render_product_post
from common.wp_profiles import build_publisher, list_wp_profiles, load_wp_profile


@dataclass
class ProductWpConfig:
    """Product→WP 파이프라인 설정."""
    name: str                                  # 알림용 이름 (예: "쿠팡→WordPress")
    source_factory: Callable[[dict], object]   # (profile) -> Source 인스턴스
    theme: ProductTheme                        # 상품 카드 테마
    post_count_env: str                        # 예: "COUPANG_POST_COUNT"
    post_count_default: int = 1
    product_count_env: str = ""                # 키워드당 상품 수
    product_count_default: int = 10
    source_search_kwargs: dict = field(default_factory=dict)
    close_source: bool = False                 # source.close() 호출 여부
    log_prefix: str = ""                       # 로그 prefix (예: "[알리→WP]")


def _build_content(keyword: str, products: list, theme: ProductTheme) -> tuple:
    """(title, content, excerpt, slug). intro + 카드별 픽 이유는 AI 생성."""
    if not products:
        return "", "", "", ""
    intro_text   = generate_product_intro(keyword, products)
    pick_reasons = generate_product_pick_reasons(keyword, products)
    return render_product_post(keyword, products, theme,
                                intro_text=intro_text,
                                pick_reasons=pick_reasons)


def run(cfg: ProductWpConfig, profile_name: str = None,
        count_per_keyword: int = None) -> None:
    """공통 run() — profile 하나로 실행."""
    from sources.itemscout_keywords import get_pool_status, mark_keywords_used

    profile = load_wp_profile(profile_name)
    if not profile:
        return

    prefix = cfg.log_prefix or ""
    log(f"{prefix}시작: {profile['site_url']} ({profile['name']})", "step")
    log(get_pool_status(), "info")

    publisher = build_publisher(profile)
    if not publisher.login():
        log("WordPress 인증 실패. 종료합니다.", "error")
        return

    source = cfg.source_factory(profile)

    # 키워드 수 / 상품 수 결정
    post_count = int(os.getenv(cfg.post_count_env, str(cfg.post_count_default)))
    if count_per_keyword is None:
        if cfg.product_count_env:
            count_per_keyword = int(os.getenv(cfg.product_count_env,
                                              str(cfg.product_count_default)))
        else:
            count_per_keyword = cfg.product_count_default

    from pipelines.coupang_to_wordpress import get_keywords
    keywords = get_keywords(n=post_count)

    published = 0
    published_keywords = []
    last_url = ""

    try:
        for keyword in keywords[:post_count]:
            log(f"키워드 처리: {keyword}", "step")

            products = source.search(keyword, count=count_per_keyword,
                                     **cfg.source_search_kwargs)
            if not products:
                log(f"'{keyword}' 상품 없음, 건너뜀", "warn")
                continue

            title, content, excerpt, slug = _build_content(keyword, products, cfg.theme)

            result = publisher.post_with_ids(
                title=title,
                content=content,
                category_id=profile["category_id"],
                tag_id=profile["tag_id"],
                excerpt=excerpt,
                slug=slug,
                status="publish",
            )

            if result.success:
                published += 1
                published_keywords.append(keyword)
                if result.url:
                    last_url = result.url
                    from common.publish_queue import add_url as _add_url
                    _add_url(result.url, platform="wordpress", title=title)

            time.sleep(random.uniform(10, 20))
    finally:
        if cfg.close_source and hasattr(source, "close"):
            source.close()

    if published_keywords:
        mark_keywords_used(published_keywords)

    total = min(post_count, len(keywords))
    log(f"{prefix}완료 ({profile['name']}): {published}/{total}건 발행", "step")

    notify_pipeline_result(
        f"{cfg.name} ({profile['name']})",
        published, total,
        details=f"키워드: {', '.join(published_keywords)}" if published_keywords else "",
        url=last_url,
    )


def run_all(cfg: ProductWpConfig, count_per_keyword: int = None) -> None:
    """모든 WordPress 프로필에 순차 실행."""
    profiles = list_wp_profiles()
    if not profiles:
        log("config.json에 등록된 프로필이 없습니다.", "error")
        return

    log(f"{cfg.log_prefix or ''}전체 프로필 발행: {profiles}", "step")
    for name in profiles:
        run(cfg, profile_name=name, count_per_keyword=count_per_keyword)
