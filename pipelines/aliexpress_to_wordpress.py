"""
파이프라인: 알리익스프레스 상품 크롤링 → WordPress 발행

공통 커널(pipelines._kernel.product_wp)을 사용한 얇은 래퍼.

실행:
    python -m pipelines.aliexpress_to_wordpress                   # 기본 프로필
    python -m pipelines.aliexpress_to_wordpress --profile <name>  # 특정 프로필
    python -m pipelines.aliexpress_to_wordpress --profile all     # 전체 프로필
"""
import os
import sys

from dotenv import load_dotenv
load_dotenv()

from common.product_html import ALIEXPRESS_THEME, render_product_post
from pipelines._kernel.product_wp import ProductWpConfig
from pipelines._kernel.product_wp import run as _kernel_run
from pipelines._kernel.product_wp import run_all as _kernel_run_all
from sources.aliexpress import AliexpressSource

# aliexpress_to_tistory 가 build_content 를 import 하므로 유지
from common.ai_intro import (
    generate_product_intro as generate_intro,
    generate_product_pick_reasons,
)


# ─── 스케줄러 메타 ───────────────────────────────────────────────────────────

SCHEDULE = {
    "env":  "SCHEDULE_ALIEXPRESS_WP",
    "func": "run",          # 기본 프로필만 발행 (1플랫폼·1계정·1건 정책)
}


# ─── HTML 콘텐츠 빌드 (타 파이프라인 재사용용) ───────────────────────────────

def build_content(keyword: str, products: list) -> tuple:
    """알리 (title, content, excerpt, slug). 공통 템플릿 엔진 사용."""
    if not products:
        return "", "", "", ""
    intro_text   = generate_intro(keyword, products)
    pick_reasons = generate_product_pick_reasons(keyword, products)
    return render_product_post(keyword, products, ALIEXPRESS_THEME,
                                intro_text=intro_text,
                                pick_reasons=pick_reasons)


# ─── 파이프라인 Config ──────────────────────────────────────────────────────

def _ali_source_factory(profile: dict):
    tracking_id = os.getenv("ALIEXPRESS_TRACKING_ID", "wordpress")
    return AliexpressSource(tracking_id=tracking_id)


_CFG = ProductWpConfig(
    name="알리→WordPress",
    source_factory=_ali_source_factory,
    theme=ALIEXPRESS_THEME,
    post_count_env="ALIEXPRESS_POST_COUNT",
    post_count_default=1,
    product_count_env="ALIEXPRESS_PRODUCT_COUNT",
    product_count_default=10,
    source_search_kwargs={"require_affiliate": True},
    close_source=True,
    log_prefix="[알리→WP] ",
)


# ─── Public API ──────────────────────────────────────────────────────────────

def run(profile_name: str = None, count_per_keyword: int = None) -> None:
    """알리 크롤링 → WordPress 발행."""
    _kernel_run(_CFG, profile_name=profile_name, count_per_keyword=count_per_keyword)


def run_all(count_per_keyword: int = None) -> None:
    """모든 WP 프로필에 순차 실행."""
    _kernel_run_all(_CFG, count_per_keyword=count_per_keyword)


if __name__ == "__main__":
    count = int(os.getenv("ALIEXPRESS_PRODUCT_COUNT", "10"))

    profile_arg = None
    if "--profile" in sys.argv:
        idx = sys.argv.index("--profile")
        if idx + 1 < len(sys.argv):
            profile_arg = sys.argv[idx + 1]

    if profile_arg == "all":
        run_all(count_per_keyword=count)
    else:
        run(profile_name=profile_arg, count_per_keyword=count)
