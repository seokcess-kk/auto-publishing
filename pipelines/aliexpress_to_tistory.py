"""
파이프라인: 알리익스프레스 상품 크롤링 → 티스토리

- 역할 매핑: TISTORY_BLOG_ALIEXPRESS (미설정 시 TISTORY_BLOG_NAME 폴백)
- AliexpressSource 로 상품 + 제휴 단축링크 수집
- ItemScout 키워드 풀 사용 (쿠팡·WP와 공유)
- build_content 는 aliexpress_to_wordpress 의 HTML 빌더 재사용 (동일 레이아웃)

실행:
    python -m pipelines.aliexpress_to_tistory
    python -m pipelines.aliexpress_to_tistory --count 5
"""
import os
import random
import sys
import time

from dotenv import load_dotenv
load_dotenv()

from common.logger import log
from common.tistory_blogs import resolve_blog_name
from publishers.tistory import TistoryPublisher
from sources.aliexpress import AliexpressSource

from pipelines.aliexpress_to_wordpress import build_content
from pipelines.coupang_to_wordpress import get_keywords  # 폴백용
from pipelines.aliexpress_to_threads import get_ali_keywords


SCHEDULE = {
    "env":  "SCHEDULE_ALIEXPRESS_TISTORY",
    "func": "run",
}


def run(count_per_keyword: int = 10,
        keyword: "str | None" = None) -> None:
    """알리 크롤링 → 티스토리 발행.

    ⚠️ 순서 주의: AliexpressSource 와 TistoryPublisher 가 모두 sync_playwright
    를 쓰므로 동시 사용 시 'Playwright Sync API inside the asyncio loop'
    충돌. 상품 수집을 모두 끝낸 뒤 source.close() → publisher.login() 순서로
    sync_playwright 인스턴스를 직렬화한다.

    Args:
        count_per_keyword: 1글당 상품 수
        keyword:           강제 키워드 1개 (지정 시 풀 우회)
    """
    from sources.itemscout_keywords import mark_keywords_used, get_pool_status

    blog_name = resolve_blog_name("aliexpress")
    log(f"[알리→티스토리] 시작 (blog={blog_name})", "step")

    # 1) 알리 source 로 상품 먼저 수집 (publisher 로그인 전에)
    tracking_id = os.getenv("ALIEXPRESS_TRACKING_ID", "wordpress")
    source = AliexpressSource(tracking_id=tracking_id)

    if keyword:
        keywords = [keyword]
        post_count = 1
        log(f"단일 키워드 모드: {keyword}", "info")
    else:
        log(get_pool_status(), "info")
        post_count = int(os.getenv("ALIEXPRESS_POST_COUNT", "1"))
        # 알리 적합 카테고리 화이트리스트로 필터된 키워드
        keywords   = get_ali_keywords(n=post_count)

    # collected: [(keyword, products), ...]
    collected: list[tuple[str, list]] = []
    skipped_keywords: list[str] = []
    try:
        for keyword in keywords[:post_count]:
            log(f"키워드 처리: {keyword}", "step")
            products = source.search(keyword, count=count_per_keyword, require_affiliate=True)
            if not products:
                log(f"'{keyword}' 상품/링크 수집 실패 또는 매칭 부족 — 건너뜀", "warn")
                skipped_keywords.append(keyword)
                continue
            collected.append((keyword, products))
    finally:
        # source 의 sync_playwright 를 명시적으로 종료해야 publisher 가 재사용 가능
        source.close()

    # 제휴 세션 만료면 키워드 잘못이 아니므로 풀에서 제외하면 안 된다
    # (정상 키워드가 만료 기간 동안 통째로 소실되는 부작용 방지).
    session_expired = getattr(source, "session_expired", False)

    # 알리에 적합하지 않은 키워드는 풀에서 점진 제외 (mismatch 누적 방지)
    if skipped_keywords and not session_expired:
        try:
            mark_keywords_used(skipped_keywords)
            log(f"풀 제외 ({len(skipped_keywords)}개): {skipped_keywords}", "info")
        except Exception as e:
            log(f"키워드 풀 제외 실패 ({e})", "warn")

    if not collected:
        if session_expired:
            log("알리 제휴 세션 만료 — 발행 불가, 수동 로그인 필요: "
                "python tools/aliexpress_manual_login.py → 'Continue with Google'", "error")
        else:
            _kws = ", ".join(skipped_keywords) if skipped_keywords else "?"
            log(f"알리 상품 수집 0건 — 발행 불가 (키워드 부적합: {_kws})", "error")
        from common.notifier import notify_pipeline_result
        notify_pipeline_result("알리→티스토리", 0, post_count, details="수집 실패")
        return

    # 2) 티스토리 로그인 — TISTORY_PUBLISHER (web|bridge) 에 따라 선택
    from common.tistory_blogs import make_publisher
    pub = make_publisher(blog_name)
    if not pub.login():
        log(f"티스토리 로그인 실패 (blog={blog_name}). 종료.", "error")
        from common.notifier import notify_pipeline_result
        notify_pipeline_result("알리→티스토리", 0, post_count, details="로그인 실패")
        _close_pub(pub)
        return

    published = 0
    published_keywords = []
    last_url = ""
    try:
        for keyword, products in collected:
            title, content, _excerpt, _slug = build_content(keyword, products)
            image_url = products[0].get("image", "")
            tags = [keyword, "알리익스프레스", "해외직구", "추천상품"]

            result = pub.post(
                title=title,
                content=content,
                tags=tags,
                image_url=image_url,
                category=os.getenv("TISTORY_CATEGORY", ""),
            )
            if result.success:
                published += 1
                published_keywords.append(keyword)
                log(f"발행 완료: {result.url}", "ok")
                if result.url:
                    last_url = result.url
                    from common.publish_queue import add_url as _add_url
                    _add_url(
                        result.url, platform="tistory", title=title,
                        keyword=keyword, source="aliexpress",
                        affiliate_url=(products[0].get("affiliate_url", "") or
                                        products[0].get("url", "")),
                    )
            else:
                log(f"발행 실패: {result.message}", "error")

            time.sleep(random.uniform(10, 20))
    finally:
        _close_pub(pub)

    if published_keywords:
        mark_keywords_used(published_keywords)

    total = min(post_count, len(keywords))
    is_bridge = os.getenv("TISTORY_PUBLISHER", "web").strip().lower() == "bridge"
    verb = "큐 등록" if is_bridge else "발행"
    log(f"[알리→티스토리] 완료: {published}/{total}건 {verb}", "step")

    if is_bridge and published > 0:
        log("[알리→티스토리] bridge 모드 — 파이프라인 알림 skip", "info")
        return

    from common.notifier import notify_pipeline_result
    notify_pipeline_result(
        "알리→티스토리", published, total,
        details=f"키워드: {', '.join(published_keywords)}" if published_keywords else "",
        url=last_url,
    )


def _close_pub(pub) -> None:
    close = getattr(pub, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            pass


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
