"""
파이프라인: 쿠팡 상품 크롤링 → GitHub Pages 발행

- Jekyll _posts/ 에 Markdown 파일 생성 후 git push
- 쿠팡 파트너스 AF코드 직접링크
- AI 소개 글 생성 (Claude / Gemini)
- Minimal Mistakes 테마 호환 마크다운

실행:
    python -m pipelines.coupang_to_github                  # 기본 실행 (3건)
    python -m pipelines.coupang_to_github --count 5        # 5건 발행
    python -m pipelines.coupang_to_github --no-push        # push 없이 md 파일만 생성
"""
import os
import sys
import re
import random
import time
from datetime import datetime

from dotenv import load_dotenv
load_dotenv()

from common.logger import log
from sources.coupang import CoupangSource, FAKE_LINK


SCHEDULE = {
    "env":  "SCHEDULE_COUPANG_GITHUB",
    "func": "run",
}


# ─── 설정 ────────────────────────────────────────────────────────────────────

GITHUB_PAGES_REPO = os.getenv("GITHUB_PAGES_REPO", "")
GITHUB_PAGES_AUTHOR = os.getenv("GITHUB_PAGES_AUTHOR", "Author")


GITHUB_PAGES_CHANNEL_ID = os.getenv("COUPANG_CHANNEL_ID_GITHUB", "") or os.getenv("COUPANG_CHANNEL_ID", "")

# repo 폴더명(*.github.io)에서 절대 사이트 URL 도출 — result.url 에 절대경로 보장
GITHUB_PAGES_SITE_URL = os.getenv("GITHUB_PAGES_SITE_URL") or (
    f"https://{os.path.basename(GITHUB_PAGES_REPO.rstrip('/'))}"
)

DEFAULT_KEYWORDS = [
    "인기상품", "베스트셀러", "추천상품", "주방용품", "생활용품",
    "건강식품", "뷰티", "스포츠용품", "디지털가전", "패션잡화",
]


# ─── 키워드 수집 ─────────────────────────────────────────────────────────────

def get_keywords(n: int = 3) -> list:
    """ItemScout 키워드 풀에서 미사용 키워드 반환."""
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


# ─── AI 소개 글 (common.ai_intro 재사용) ─────────────────────────────────────

def generate_intro(keyword: str, products: list) -> str:
    """AI 소개 글 생성 - common.ai_intro 공통 모듈 사용."""
    try:
        from common.ai_intro import generate_product_intro
        return generate_product_intro(keyword, products)
    except ImportError:
        return ""


# ─── Markdown 콘텐츠 빌드 ───────────────────────────────────────────────────

def build_markdown(keyword: str, products: list) -> tuple:
    """마크다운 포스트 본문 생성.

    Returns:
        (title, markdown_body, category, tags)
    """
    if not products:
        return "", "", "", []

    title = f"[{keyword}] TOP{len(products)} 추천 - {products[0]['name'][:69]}"
    category = "shopping"
    tags = ["Top10", "shopping", keyword]

    # 도입부
    intro_text = generate_intro(keyword, products)

    body_parts = []

    # 메타 설명
    body_parts.append(
        f"해당 게시물에서는 데이터 분석 도구를 이용하여 "
        f"**{keyword}** 인기/추천 상품 리스트 TOP{len(products)}을 "
        f"추천해 드리고 있습니다.\n"
    )

    # AI 소개 글
    if intro_text:
        body_parts.append(f"> {intro_text}\n")

    body_parts.append("---\n")

    # 상품 카드
    for idx, p in enumerate(products):
        name = p.get("name", "")
        price = p.get("price", "")
        discount = p.get("discount_rate", "")
        rating = p.get("rating", "")
        review = p.get("review_count", "")
        image = p.get("image", "")
        aff_url = p.get("affiliate_url", FAKE_LINK)

        # 좌우 정렬 (Old Source 패턴)
        align = "left" if (idx + 1) % 2 == 1 else "right"

        card = f"### [{idx + 1}] {keyword} 판매 순위\n\n"

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

        card += f'\n<a href="{aff_url}" target="_blank" rel="nofollow" style="display:inline-block;padding:8px 18px;background:#e4000f;color:#fff;border-radius:6px;text-decoration:none;font-weight:bold;font-size:14px;">👉 상품 보기</a>\n\n'
        card += '<div style="clear:both;"></div>\n\n---\n'

        body_parts.append(card)

    # 파트너스 고지
    body_parts.append(
        f"\n> 💦 파트너스 활동을 통해 일정액의 수수료를 제공받을 수 있습니다.\n"
    )

    markdown_body = "\n".join(body_parts)
    return title, markdown_body, category, tags


# ─── 메인 ────────────────────────────────────────────────────────────────────

def run(count: int = 1, auto_push: bool = True,
        count_per_keyword: int = 10) -> None:
    """쿠팡 크롤링 → GitHub Pages 발행."""
    from sources.itemscout_keywords import mark_keywords_used, get_pool_status
    from publishers.github_pages import GitHubPagesPublisher

    publisher = GitHubPagesPublisher(
        repo_path=GITHUB_PAGES_REPO,
        author=GITHUB_PAGES_AUTHOR,
        site_url=GITHUB_PAGES_SITE_URL,
    )

    if not publisher.login():
        return

    log(f"GitHub Pages 파이프라인 시작 ({count}건)", "step")
    log(get_pool_status(), "info")

    coupang = CoupangSource(channel_id=GITHUB_PAGES_CHANNEL_ID)
    keywords = get_keywords(n=count)
    published = 0
    published_keywords = []
    created_files = []

    for keyword in keywords[:count]:
        log(f"키워드 처리: {keyword}", "step")

        products = coupang.search(keyword, count=count_per_keyword)
        if not products:
            log(f"'{keyword}' 상품 없음, 건너뜀", "warn")
            continue

        title, body, category, tags = build_markdown(keyword, products)

        result = publisher.post(
            title=title,
            content=body,
            tags=tags,
            category=category,
            keyword=keyword,
            auto_push=False,  # 일괄 push를 위해 개별 push 비활성화
        )

        if result.success:
            published += 1
            published_keywords.append(keyword)
            created_files.append(
                os.path.join(publisher.posts_dir, result.post_id)
            )
            if result.url:
                from common.publish_queue import add_url as _add_url
                _add_url(result.url, platform="github", title=title)

        time.sleep(random.uniform(10, 20))

    # 일괄 git push
    if auto_push and created_files:
        msg = f"포스트 {len(created_files)}건 발행: {', '.join(published_keywords)}"
        publisher.batch_push(created_files, msg)
    elif not auto_push and created_files:
        log(f"MD 파일 {len(created_files)}건 생성 완료 (push 건너뜀)", "info")

    if published_keywords:
        mark_keywords_used(published_keywords)

    log(f"GitHub Pages 완료: {published}/{count}건 발행", "step")

    from common.notifier import notify_pipeline_result
    notify_pipeline_result(
        "쿠팡→GitHub Pages",
        published, count,
        details=f"키워드: {', '.join(published_keywords)}" if published_keywords else "",
    )


if __name__ == "__main__":
    post_count = int(os.getenv("POST_COUNT", "1"))
    product_count = int(os.getenv("COUPANG_PRODUCT_COUNT", "10"))

    # --count 인자
    if "--count" in sys.argv:
        idx = sys.argv.index("--count")
        if idx + 1 < len(sys.argv):
            post_count = int(sys.argv[idx + 1])

    # --no-push 인자
    auto_push = "--no-push" not in sys.argv

    run(count=post_count, auto_push=auto_push, count_per_keyword=product_count)
