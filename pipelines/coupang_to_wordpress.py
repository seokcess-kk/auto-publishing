"""
파이프라인: 쿠팡 상품 크롤링 → WordPress 발행

공통 커널(pipelines._kernel.product_wp)을 사용한 얇은 래퍼.

실행:
    python -m pipelines.coupang_to_wordpress                  # 기본 프로필
    python -m pipelines.coupang_to_wordpress --profile <name> # 특정 프로필
    python -m pipelines.coupang_to_wordpress --profile all    # 전체 프로필
"""
import os
import random
import sys

from dotenv import load_dotenv
load_dotenv()

from common.logger import log
from common.product_html import COUPANG_THEME
from pipelines._kernel.product_wp import ProductWpConfig
from pipelines._kernel.product_wp import run as _kernel_run
from pipelines._kernel.product_wp import run_all as _kernel_run_all
from sources.coupang import CoupangSource


# ─── 스케줄러 메타 ───────────────────────────────────────────────────────────

SCHEDULE = {
    "env":  "SCHEDULE_COUPANG_WP",
    "func": "run",
}


# ─── 기본 키워드 ─────────────────────────────────────────────────────────────

DEFAULT_KEYWORDS = [
    "인기상품", "베스트셀러", "추천상품", "주방용품", "생활용품",
    "건강식품", "뷰티", "스포츠용품", "디지털가전", "패션잡화",
]


# ─── 키워드 수집 (다른 파이프라인에서 import 하므로 유지) ───────────────────

def get_keywords(n: int = 3) -> list:
    """ItemScout 키워드 풀에서 n개 반환. 실패 시 DEFAULT_KEYWORDS 폴백."""
    from sources.itemscout_keywords import get_next_keywords

    try:
        keywords = get_next_keywords(n=n, refill_threshold=50)
        if keywords:
            return keywords
    except Exception as e:
        log(f"ItemScout 키워드 풀 실패 ({e}), 기본 키워드 사용", "warn")

    selected = random.sample(DEFAULT_KEYWORDS, k=min(n, len(DEFAULT_KEYWORDS)))
    log(f"기본 키워드 선택: {selected}", "warn")
    return selected


# ─── 파이프라인 Config ──────────────────────────────────────────────────────

def _coupang_source_factory(profile: dict):
    channel_id = (profile.get("channel_id")
                  or os.getenv("COUPANG_CHANNEL_ID_WP", "")
                  or os.getenv("COUPANG_CHANNEL_ID", ""))
    return CoupangSource(channel_id=channel_id)


_CFG = ProductWpConfig(
    name="쿠팡→WordPress",
    source_factory=_coupang_source_factory,
    theme=COUPANG_THEME,
    post_count_env="COUPANG_POST_COUNT",
    post_count_default=1,
    product_count_env="COUPANG_PRODUCT_COUNT",
    product_count_default=10,
    source_search_kwargs={},
    close_source=False,
    source_kind="coupang",
)


# ─── Public API ──────────────────────────────────────────────────────────────

def run(profile_name: str = None, count_per_keyword: int = None) -> None:
    """쿠팡 크롤링 → WordPress 발행."""
    _kernel_run(_CFG, profile_name=profile_name, count_per_keyword=count_per_keyword)


def run_all(count_per_keyword: int = None) -> None:
    """모든 WP 프로필에 순차 실행."""
    _kernel_run_all(_CFG, count_per_keyword=count_per_keyword)


if __name__ == "__main__":
    count = int(os.getenv("COUPANG_PRODUCT_COUNT", "10"))

    profile_arg = None
    if "--profile" in sys.argv:
        idx = sys.argv.index("--profile")
        if idx + 1 < len(sys.argv):
            profile_arg = sys.argv[idx + 1]

    if profile_arg == "all":
        run_all(count_per_keyword=count)
    else:
        run(profile_name=profile_arg, count_per_keyword=count)
