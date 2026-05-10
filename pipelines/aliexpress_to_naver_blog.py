"""
파이프라인: 알리익스프레스 상품 크롤링 → 네이버 블로그 발행

- 카테고리:  NAVER_ALIEXPRESS_CATEGORY_NO > NAVER_COUPANG_CATEGORY_NO > 1
            (네이버 RabbitWrite API 는 categoryId=0 이면 invalid parameter 반환)
- ItemScout 키워드 풀 공유
- HTML/SE 카드: ALIEXPRESS_THEME + AI 도입부 + build_aliexpress_naver_document
- 의무 고지: 카드 푸터에 알리 파트너스 문구 자동 부착

⚠️ 주의: 네이버 블로그는 외부 어필리에이트 링크에 정책상 제약이 있어 글이
       비공개 처리되거나 노출이 제한될 수 있다. 알리 파트너스 승인용보다는
       자체 트래픽/SEO 목적으로 사용 권장.

⚠️ 순서 주의: AliexpressSource(Playwright) 의 sync_playwright 와
       NaverBlogPublisher(Playwright) 가 충돌하지 않도록 source.close()
       이후 publisher.login() 을 호출한다 (aliexpress_to_tistory 와 동일 패턴).

실행:
    python -m pipelines.aliexpress_to_naver_blog                    # 자동 키워드 1건
    python -m pipelines.aliexpress_to_naver_blog --count 5          # 1글당 상품 수 5
    python -m pipelines.aliexpress_to_naver_blog --keyword 무선이어폰  # 키워드 강제
"""
import os
import random
import sys
import time

from dotenv import load_dotenv
load_dotenv()

from common.ai_intro import (
    generate_product_intro,
    generate_product_pick_reasons,
    generate_related_tags,
)
from common.logger import log
from common.product_html import ALIEXPRESS_THEME, render_product_post
from publishers.naver_blog import NaverBlogPublisher
from sources.aliexpress import AliexpressSource

from pipelines.coupang_to_wordpress import get_keywords


SCHEDULE = {
    "env":  "SCHEDULE_ALIEXPRESS_NAVER_BLOG",
    "func": "run",
}


def _build_content(keyword: str, products: list) -> tuple:
    """(title, content, excerpt, slug, intro_text, pick_reasons) — ALIEXPRESS_THEME + AI.

    네이버 블로그 publisher 는 'content' 인자의 HTML 을 strip 하므로 실제
    본문은 'aliexpress_products' / 'pick_reasons' kwargs 로 따로 전달한다.
    """
    if not products:
        return "", "", "", "", "", []
    intro_text   = generate_product_intro(keyword, products)
    pick_reasons = generate_product_pick_reasons(keyword, products)
    title, content, excerpt, slug = render_product_post(
        keyword, products, ALIEXPRESS_THEME,
        intro_text=intro_text, pick_reasons=pick_reasons)
    return title, content, excerpt, slug, intro_text, pick_reasons


def _resolve_category_no() -> int:
    """네이버 RabbitWrite categoryId 결정. 0 이면 invalid → 1 로 폴백."""
    raw = (os.getenv("NAVER_ALIEXPRESS_CATEGORY_NO")
           or os.getenv("NAVER_COUPANG_CATEGORY_NO")
           or "1")
    try:
        n = int(raw)
        return n if n > 0 else 1
    except ValueError:
        return 1


def run(count_per_keyword: "int | None" = None,
        keyword: "str | None" = None) -> None:
    """알리 크롤링 → 네이버 블로그 발행."""
    from sources.itemscout_keywords import get_pool_status, mark_keywords_used

    blog_id  = os.getenv("NAVER_BLOG_ID", "")
    username = os.getenv("NAVER_USERNAME", "")
    password = os.getenv("NAVER_PASSWORD", "")
    if not all([blog_id, username, password]):
        raise ValueError("NAVER_BLOG_ID, NAVER_USERNAME, NAVER_PASSWORD 필요")

    log(f"[알리→네이버블로그] 시작 (blog={blog_id})", "step")

    if count_per_keyword is None:
        count_per_keyword = int(os.getenv("ALIEXPRESS_PRODUCT_COUNT", "10"))

    # 1) 키워드
    if keyword:
        keywords = [keyword]
        post_count = 1
        log(f"단일 키워드 모드: {keyword}", "info")
    else:
        log(get_pool_status(), "info")
        post_count = int(os.getenv("ALIEXPRESS_POST_COUNT", "1"))
        keywords = get_keywords(n=post_count)

    # 2) 알리 상품 수집 (publisher 호출 전에 source 종료 — sync_playwright 직렬화)
    tracking_id = os.getenv("ALIEXPRESS_TRACKING_ID", "wordpress")
    source = AliexpressSource(tracking_id=tracking_id)

    collected: list[tuple[str, list]] = []
    skipped_keywords: list[str] = []
    try:
        for kw in keywords[:post_count]:
            log(f"키워드 처리: {kw}", "step")
            products = source.search(kw, count=count_per_keyword,
                                      require_affiliate=True)
            if not products:
                log(f"'{kw}' 상품/링크 수집 실패 또는 매칭 부족 — 건너뜀", "warn")
                skipped_keywords.append(kw)
                continue
            collected.append((kw, products))
    finally:
        source.close()

    # 알리에 적합하지 않은 키워드는 풀에서 점진 제외 (mismatch 누적 방지)
    if skipped_keywords and not keyword:
        try:
            mark_keywords_used(skipped_keywords)
            log(f"풀 제외 ({len(skipped_keywords)}개): {skipped_keywords}", "info")
        except Exception as e:
            log(f"키워드 풀 제외 실패 ({e})", "warn")

    if not collected:
        log("수집된 상품 없음", "warn")
        from common.notifier import notify_pipeline_result
        notify_pipeline_result("알리→네이버블로그", 0, post_count,
                               details="수집 실패")
        return

    # 3) 네이버 블로그 로그인
    blog = NaverBlogPublisher(blog_id, username, password)
    if not blog.login():
        log("네이버 블로그 로그인 실패", "error")
        from common.notifier import notify_pipeline_result
        notify_pipeline_result("알리→네이버블로그", 0, post_count,
                               details="로그인 실패")
        return

    cat_no = _resolve_category_no()
    log(f"카테고리 번호: {cat_no}", "info")

    # 4) 발행
    published = 0
    published_keywords: list[str] = []
    last_url = ""

    for kw, products in collected:
        title, content, _excerpt, _slug, intro_text, pick_reasons = _build_content(kw, products)
        image_url = products[0].get("image", "")
        ai_tags = generate_related_tags(
            title, context=f"알리익스프레스 상품 / 키워드 {kw}", n=3,
            exclude=[kw, "알리", "알리익스프레스", "해외직구", "추천상품"],
        )
        tags = [kw, "알리익스프레스", "해외직구"] + ai_tags + ["추천상품"]

        result = blog.post(
            title=title,
            content=content,
            tags=tags,
            image_url=image_url,
            category_no=cat_no,
            aliexpress_products=products,
            intro=intro_text,
            keyword=kw,
            pick_reasons=pick_reasons,
        )
        if result.success:
            published += 1
            published_keywords.append(kw)
            if result.url:
                last_url = result.url
                log(f"발행 완료: {result.url}", "ok")
                try:
                    from common.publish_queue import add_url as _add_url
                    _add_url(
                        result.url, platform="naver_blog", title=title,
                        keyword=kw, source="aliexpress",
                        affiliate_url=(products[0].get("affiliate_url", "") or
                                        products[0].get("url", "")),
                    )
                except Exception:
                    pass
        else:
            log(f"발행 실패: {result.message}", "error")

        time.sleep(random.uniform(15, 30))

    if published_keywords and not keyword:
        try:
            mark_keywords_used(published_keywords)
        except Exception as e:
            log(f"키워드 기록 실패 ({e})", "warn")

    total = min(post_count, len(keywords))
    log(f"[알리→네이버블로그] 완료: {published}/{total}건 발행", "step")

    from common.notifier import notify_pipeline_result
    notify_pipeline_result(
        "알리→네이버블로그", published, total,
        details=f"키워드: {', '.join(published_keywords)}" if published_keywords else "",
        url=last_url,
    )


if __name__ == "__main__":
    count = int(os.getenv("ALIEXPRESS_PRODUCT_COUNT", "10"))
    forced_keyword: "str | None" = None

    if "--count" in sys.argv:
        idx = sys.argv.index("--count")
        if idx + 1 < len(sys.argv):
            count = int(sys.argv[idx + 1])

    if "--keyword" in sys.argv:
        idx = sys.argv.index("--keyword")
        if idx + 1 < len(sys.argv):
            forced_keyword = sys.argv[idx + 1]

    run(count_per_keyword=count, keyword=forced_keyword)
