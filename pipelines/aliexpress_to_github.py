"""
파이프라인: 알리익스프레스 상품 크롤링 → GitHub Pages 발행

- AliexpressSource 로 상품 + 제휴 단축링크 수집
- ItemScout 키워드 풀 사용 (WP·티스토리와 공유)
- Jekyll _posts/ 에 Markdown 파일 생성 후 git push

환경변수:
    GITHUB_PAGES_ALIEXPRESS_REPO    GitHub Pages 로컬 repo 경로
    GITHUB_PAGES_ALIEXPRESS_AUTHOR  포스트 author 이름
    GITHUB_PAGES_ALIEXPRESS_SITE    사이트 URL (예: https://yourname.github.io)

실행:
    python -m pipelines.aliexpress_to_github
    python -m pipelines.aliexpress_to_github --count 3
    python -m pipelines.aliexpress_to_github --no-push
"""
import os
import random
import sys
import time
from datetime import datetime

from dotenv import load_dotenv
load_dotenv()

from common.logger import log
from sources.aliexpress import AliexpressSource
from publishers.github_pages import GitHubPagesPublisher


# ─── 스케줄러 메타 ───────────────────────────────────────────────────────────

SCHEDULE = {
    "env":  "SCHEDULE_ALIEXPRESS_GITHUB",
    "func": "run",
}


# ─── 설정 ────────────────────────────────────────────────────────────────────

REPO_PATH = os.getenv("GITHUB_PAGES_ALIEXPRESS_REPO", "")
AUTHOR = os.getenv("GITHUB_PAGES_ALIEXPRESS_AUTHOR", "Author")
SITE_URL = os.getenv("GITHUB_PAGES_ALIEXPRESS_SITE", "")
TRACKING_ID = os.getenv("ALIEXPRESS_TRACKING_ID", "wordpress")

DEFAULT_KEYWORDS = [
    "인기상품", "가성비", "추천상품", "생활용품", "주방용품",
    "뷰티", "패션", "전자제품", "스포츠", "반려동물용품",
]


# ─── 키워드 수집 ─────────────────────────────────────────────────────────────

def _get_keywords(n: int) -> list:
    from sources.itemscout_keywords import get_next_keywords
    try:
        kws = get_next_keywords(n=n, refill_threshold=50)
        if kws:
            return kws
    except Exception as e:
        log(f"ItemScout 키워드 풀 실패 ({e}), 기본 키워드 사용", "warn")
    return random.sample(DEFAULT_KEYWORDS, k=min(n, len(DEFAULT_KEYWORDS)))


# ─── Markdown 콘텐츠 빌드 ───────────────────────────────────────────────────

def _build_markdown(keyword: str, products: list) -> tuple:
    """알리 상품 마크다운 포스트 생성.

    Returns:
        (title, body, category, tags)
    """
    if not products:
        return "", "", "", []

    from common.product_html import make_product_title
    title = make_product_title(keyword, products)
    category = "aliexpress"
    tags = ["알리익스프레스", "추천상품", keyword]

    # AI 소개 글
    intro_text = ""
    try:
        from common.ai_intro import generate_product_intro
        intro_text = generate_product_intro(keyword, products)
    except Exception:
        pass

    body_parts = []
    body_parts.append(
        f"알리익스프레스에서 **{keyword}** 인기 상품 TOP{len(products)}를 소개합니다.\n"
    )
    if intro_text:
        body_parts.append(f"> {intro_text}\n")
    body_parts.append("---\n")

    for idx, p in enumerate(products):
        name      = p.get("name", "")
        price     = p.get("price", "")
        discount  = p.get("discount_rate", "")
        rating    = p.get("rating", "")
        review    = p.get("review_count", "")
        image     = p.get("image", "")
        aff_url   = p.get("affiliate_url", "#")
        align     = "left" if (idx + 1) % 2 == 1 else "right"

        card = f"### [{idx + 1}] {keyword} 추천 상품\n\n"
        if image:
            card += (
                f'<a href="{aff_url}" target="_blank" rel="nofollow">'
                f'<img src="{image}" alt="{keyword} TOP{idx+1}" '
                f'style="max-width:280px;height:auto;float:{align};margin:0 16px 12px 0;border-radius:8px;">'
                f'</a>\n\n'
            )
        card += f"[**{name}**]({aff_url})\n\n"
        if discount:
            card += f"- 할인율: {discount}\n"
        if price:
            card += f"- 가격: **{price}**\n"
        if rating and rating != "No data":
            card += f"- 평점: ⭐ {rating}\n"
        if review and review != "0":
            card += f"- 리뷰수: {review}\n"
        card += (
            f'\n<a href="{aff_url}" target="_blank" rel="nofollow" '
            f'style="display:inline-block;padding:8px 18px;background:#e4000f;'
            f'color:#fff;border-radius:6px;text-decoration:none;font-weight:bold;font-size:14px;">'
            f'👉 알리익스프레스에서 보기</a>\n\n'
            f'<div style="clear:both;"></div>\n\n---\n'
        )
        body_parts.append(card)

    body_parts.append(
        "\n> 💡 이 포스트는 알리익스프레스 파트너스 활동의 일환으로 수수료를 받을 수 있습니다.\n"
    )

    return title, "\n".join(body_parts), category, tags


# ─── 메인 ────────────────────────────────────────────────────────────────────

def run(count: int = 1, auto_push: bool = True, count_per_keyword: int = 10) -> None:
    """알리 크롤링 → GitHub Pages 발행.

    제휴 링크 확보 전략:
    - 키워드당 count_per_keyword * 2 개를 먼저 수집
    - 제휴 링크 생성 성공한 상품만 필터링
    - 필터링 후 count_per_keyword 개로 제한 (부족하면 있는 만큼)
    """
    from sources.itemscout_keywords import mark_keywords_used, get_pool_status

    log(f"[알리→GitHub] 시작 (repo={REPO_PATH}, {count}건)", "step")
    log(get_pool_status(), "info")

    # 제휴 링크 확보를 위해 수집 배수 설정 (2배 수집 후 필터링)
    fetch_count = count_per_keyword * 2

    # 1) 알리 수집 (playwright sync — publisher login 전에)
    source = AliexpressSource(tracking_id=TRACKING_ID)
    keywords = _get_keywords(n=count)
    collected: list[tuple[str, list]] = []
    try:
        for kw in keywords[:count]:
            log(f"알리 수집: {kw} ({fetch_count}개 시도)", "step")
            products = source.search(kw, count=fetch_count)
            if not products:
                log(f"'{kw}' 상품 없음, 건너뜀", "warn")
                continue
            # 제휴 링크 성공 상품만 필터링 후 count_per_keyword 개 제한
            affiliate_products = [p for p in products if p.get("affiliate_url") and p["affiliate_url"] != "#"]
            log(f"'{kw}' 제휴링크 확보: {len(affiliate_products)}/{len(products)}", "info")
            if affiliate_products:
                collected.append((kw, affiliate_products[:count_per_keyword]))
            else:
                log(f"'{kw}' 제휴링크 0개, 건너뜀", "warn")
            time.sleep(random.uniform(5, 10))
    finally:
        source.close()

    if not collected:
        log("[알리→GitHub] 수집된 상품 없음, 종료", "warn")
        return

    # 2) GitHub Pages 발행
    publisher = GitHubPagesPublisher(repo_path=REPO_PATH, author=AUTHOR, site_url=SITE_URL)
    if not publisher.login():
        return

    published_keywords = []
    created_files = []
    last_url = ""

    for keyword, products in collected:
        title, body, category, tags = _build_markdown(keyword, products)
        result = publisher.post(
            title=title,
            content=body,
            tags=tags,
            category=category,
            keyword=keyword,
            auto_push=False,
        )
        if result.success:
            published_keywords.append(keyword)
            created_files.append(
                os.path.join(publisher.posts_dir, result.post_id)
            )
            if result.url:
                last_url = result.url
                from common.publish_queue import add_url as _add_url
                _add_url(result.url, platform="github", title=title)
        time.sleep(random.uniform(5, 10))

    # 3) 일괄 push
    if auto_push and created_files:
        msg = f"알리 포스트 {len(created_files)}건: {', '.join(published_keywords)}"
        publisher.batch_push(created_files, msg)
    elif not auto_push and created_files:
        log(f"MD 파일 {len(created_files)}건 생성 완료 (push 건너뜀)", "info")

    if published_keywords:
        mark_keywords_used(published_keywords)

    log(f"[알리→GitHub] 완료: {len(published_keywords)}/{count}건", "step")

    from common.notifier import notify_pipeline_result
    notify_pipeline_result(
        "알리→GitHub Pages",
        len(published_keywords), count,
        details=f"키워드: {', '.join(published_keywords)}" if published_keywords else "",
        url=last_url,
    )


if __name__ == "__main__":
    post_count = int(os.getenv("ALIEXPRESS_POST_COUNT", "1"))
    product_count = int(os.getenv("ALIEXPRESS_PRODUCT_COUNT", "10"))

    if "--count" in sys.argv:
        idx = sys.argv.index("--count")
        if idx + 1 < len(sys.argv):
            post_count = int(sys.argv[idx + 1])

    auto_push = "--no-push" not in sys.argv

    run(count=post_count, auto_push=auto_push, count_per_keyword=product_count)
