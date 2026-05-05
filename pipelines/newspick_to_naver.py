"""
파이프라인: 뉴스픽 → 네이버 블로그 / 카페

실행:
    python -m pipelines.newspick_to_naver
"""
import os
import random
import time

from dotenv import load_dotenv
load_dotenv()

from common.logger import log
from sources.newspick import NewspickSource
from sources.gemini_generator import GeminiGenerator
from publishers.naver_blog import NaverBlogPublisher
from publishers.naver_cafe import NaverCafePublisher


SCHEDULE = {
    "env":  "SCHEDULE_NEWSPICK_NAVER",
    "func": "run_blog",
    "args_from_env": ("NEWSPICK_CATEGORY:추천", "POST_COUNT:1:int"),
}


def run_blog(category: str = "추천", count: int = 1) -> None:
    """뉴스픽 → 네이버 블로그."""
    blog_id  = os.getenv("NAVER_BLOG_ID", "")
    username = os.getenv("NAVER_USERNAME", "")
    password = os.getenv("NAVER_PASSWORD", "")
    if not all([blog_id, username, password]):
        raise ValueError("환경변수 NAVER_BLOG_ID, NAVER_USERNAME, NAVER_PASSWORD 필요")

    # 네이버 블로그 RabbitWrite API 는 categoryId=0 이면 'invalid parameter' 반환.
    # NAVER_NEWSPICK_CATEGORY_NO > NAVER_RISESET_CATEGORY_NO > 1 순으로 폴백.
    cat_no = int(
        os.getenv("NAVER_NEWSPICK_CATEGORY_NO")
        or os.getenv("NAVER_RISESET_CATEGORY_NO")
        or "1"
    )

    newspick = NewspickSource(referral_code=os.getenv("NEWSPICK_REFERRAL", ""))
    blog     = NaverBlogPublisher(blog_id, username, password)
    gemini   = GeminiGenerator()

    if not blog.login():
        log("네이버 블로그 로그인 실패", "error")
        return
    if not newspick.ensure_session():
        log("뉴스픽 세션 없음", "error")
        return

    articles  = newspick.fetch_with_links(category=category, count=count)
    published = 0
    last_url = ""
    for article in articles:
        title   = article["title"]
        content = f'<p><a href="{article["short_url"]}">{title}</a></p>'
        if article.get("summary"):
            content += f"\n<p>{gemini.summarize(article['summary'])}</p>"

        result = blog.post(
            title=title,
            content=content,
            tags=[category, "뉴스픽"],
            image_url=article.get("image", ""),
            category_no=cat_no,
        )
        if result.success:
            published += 1
            if result.url:
                last_url = result.url
                from common.publish_queue import add_url as _add_url
                _add_url(result.url, platform="naver_blog", title=title)
        time.sleep(random.uniform(15, 30))

    log(f"네이버 블로그 완료: {published}/{count}건", "step")

    from common.notifier import notify_pipeline_result
    notify_pipeline_result("뉴스픽→네이버블로그", published, count, url=last_url)


def run_cafe(category: str = "추천", count: int = 1) -> None:
    """뉴스픽 → 네이버 카페."""
    cafe_id  = os.getenv("NAVER_CAFE_ID", "")
    username = os.getenv("NAVER_USERNAME", "")
    password = os.getenv("NAVER_PASSWORD", "")
    menu_id  = os.getenv("NAVER_CAFE_MENU_ID", "")
    if not all([cafe_id, username, password]):
        raise ValueError("환경변수 NAVER_CAFE_ID, NAVER_USERNAME, NAVER_PASSWORD 필요")

    newspick = NewspickSource(referral_code=os.getenv("NEWSPICK_REFERRAL", ""))
    cafe     = NaverCafePublisher(cafe_id, username, password)
    gemini   = GeminiGenerator()

    if not cafe.login():
        log("네이버 카페 로그인 실패", "error")
        return
    if not newspick.ensure_session():
        log("뉴스픽 세션 없음", "error")
        return

    articles  = newspick.fetch_with_links(category=category, count=count)
    published = 0
    last_url = ""
    for article in articles:
        title   = article["title"]
        content = f'<p><a href="{article["short_url"]}">{title}</a></p>'
        if article.get("summary"):
            content += f"\n<p>{gemini.summarize(article['summary'])}</p>"

        result = cafe.post(
            title=title,
            content=content,
            tags=[category],
            image_url=article.get("image", ""),
            menu_id=menu_id,
        )
        if result.success:
            published += 1
            if result.url:
                last_url = result.url
                from common.publish_queue import add_url as _add_url
                _add_url(result.url, platform="naver_cafe", title=title)
        time.sleep(random.uniform(15, 30))

    log(f"네이버 카페 완료: {published}/{count}건", "step")

    from common.notifier import notify_pipeline_result
    notify_pipeline_result("뉴스픽→네이버카페", published, count, url=last_url)


if __name__ == "__main__":
    target = os.getenv("NAVER_TARGET", "blog")  # 'blog' | 'cafe'
    cat    = os.getenv("NEWSPICK_CATEGORY", "추천")
    cnt    = int(os.getenv("POST_COUNT", "1"))

    if target == "cafe":
        run_cafe(cat, cnt)
    else:
        run_blog(cat, cnt)
