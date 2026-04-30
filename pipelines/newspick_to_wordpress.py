"""
파이프라인: 뉴스픽 → WordPress

공통 커널(pipelines._kernel.newspick)을 사용한 얇은 래퍼.

실행:
    python -m pipelines.newspick_to_wordpress
"""
import os

from dotenv import load_dotenv
load_dotenv()

from pipelines._kernel.newspick import NewspickConfig
from pipelines._kernel.newspick import run as _kernel_run
from publishers.wordpress import WordPressPublisher


SCHEDULE = {
    "env":  "SCHEDULE_NEWSPICK_WP",
    "func": "run",
    "args_from_env": ("NEWSPICK_CATEGORY:추천", "POST_COUNT:1:int"),
}


def _wp_factory():
    site_url    = os.getenv("WP_SITE_URL", "")
    jwt_token   = os.getenv("WP_JWT_TOKEN", "")
    wp_user     = os.getenv("WP_USERNAME", "")
    wp_password = os.getenv("WP_APP_PASSWORD", "")
    if not site_url:
        raise ValueError("환경변수 WP_SITE_URL 필요")
    # JWT 우선 — 사이트가 JWT Bearer 전용으로 설정된 경우 Basic Auth 는 401
    if jwt_token:
        return WordPressPublisher(site_url, jwt_token=jwt_token)
    if wp_user and wp_password:
        return WordPressPublisher(site_url, wp_user, wp_password)
    raise ValueError("WP_JWT_TOKEN 또는 WP_USERNAME+WP_APP_PASSWORD 필요")


_CFG = NewspickConfig(
    name="뉴스픽→WordPress",
    publisher_factory=_wp_factory,
    post_category_env="WP_CATEGORY",
    sleep_range=(5, 15),
)


def run(category: str = "추천", count: int = 1,
        use_ai_summary: bool = True) -> None:
    """뉴스픽 수집 → WordPress 발행."""
    _kernel_run(_CFG, category=category, count=count, use_ai_summary=use_ai_summary)


if __name__ == "__main__":
    run(
        category=os.getenv("NEWSPICK_CATEGORY", "추천"),
        count=int(os.getenv("POST_COUNT", "1")),
    )
