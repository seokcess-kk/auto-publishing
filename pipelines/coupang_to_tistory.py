"""
파이프라인: 쿠팡 상품 크롤링 → 티스토리 발행

- 역할 매핑: TISTORY_BLOG_COUPANG (미설정 시 TISTORY_BLOG_NAME 폴백)
- 채널 ID:   COUPANG_CHANNEL_ID_TISTORY > COUPANG_CHANNEL_ID 폴백
- ItemScout 키워드 풀 공유 (쿠팡·WP·알리와 동일)
- HTML 빌더는 _kernel.product_wp 와 동일하게 COUPANG_THEME + AI 도입부 사용

쿠팡 파트너스 승인 신청용 첫 글 작성에도 사용 가능 — AF 코드만 채워두면
파트너스 직접링크가 자동 생성된다 (별도 API 필요 없음).

실행:
    python -m pipelines.coupang_to_tistory                # POST_COUNT 만큼 발행
    python -m pipelines.coupang_to_tistory --count 5      # 1글당 상품 수 5개
    python -m pipelines.coupang_to_tistory --keyword 무선이어폰  # 단일 키워드 강제
"""
import os
import random
import sys
import time

from dotenv import load_dotenv
load_dotenv()

from common.ai_intro import generate_product_intro, generate_product_pick_reasons
from common.logger import log
from common.product_html import COUPANG_THEME, render_product_post
from common.tistory_blogs import resolve_blog_name
from publishers.tistory import TistoryPublisher
from sources.coupang import CoupangSource

from pipelines.coupang_to_wordpress import get_keywords


SCHEDULE = {
    "env":  "SCHEDULE_COUPANG_TISTORY",
    "func": "run",
}


def _build_content(keyword: str, products: list) -> tuple:
    """(title, content, excerpt, slug) — COUPANG_THEME + AI 도입부 + 카드 픽 이유."""
    if not products:
        return "", "", "", ""
    intro_text   = generate_product_intro(keyword, products)
    pick_reasons = generate_product_pick_reasons(keyword, products)
    return render_product_post(keyword, products, COUPANG_THEME,
                                intro_text=intro_text,
                                pick_reasons=pick_reasons)


def _close_pub(pub) -> None:
    close = getattr(pub, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            pass


def run(count_per_keyword: "int | None" = None, keyword: "str | None" = None) -> None:
    """쿠팡 크롤링 → 티스토리 발행.

    Args:
        count_per_keyword: 1글당 상품 수 (기본 COUPANG_PRODUCT_COUNT)
        keyword: 강제 키워드 1개 (지정 시 ItemScout 풀 사용 안 함)
    """
    from sources.itemscout_keywords import get_pool_status, mark_keywords_used

    blog_name = resolve_blog_name("coupang")
    log(f"[쿠팡→티스토리] 시작 (blog={blog_name})", "step")

    if count_per_keyword is None:
        count_per_keyword = int(os.getenv("COUPANG_PRODUCT_COUNT", "10"))

    # 1) 키워드 결정 — 명시 키워드가 있으면 풀 우회
    if keyword:
        keywords = [keyword]
        post_count = 1
        log(f"단일 키워드 모드: {keyword}", "info")
    else:
        log(get_pool_status(), "info")
        post_count = int(os.getenv("COUPANG_POST_COUNT", os.getenv("POST_COUNT", "1")))
        keywords = get_keywords(n=post_count)

    # 2) 쿠팡 상품 수집 — 채널 ID 는 티스토리 전용 우선
    channel_id = (os.getenv("COUPANG_CHANNEL_ID_TISTORY", "")
                  or os.getenv("COUPANG_CHANNEL_ID", ""))
    source = CoupangSource(channel_id=channel_id)

    collected: list[tuple[str, list]] = []
    for kw in keywords[:post_count]:
        log(f"키워드 처리: {kw}", "step")
        products = source.search(kw, count=count_per_keyword)
        if not products:
            log(f"'{kw}' 상품 수집 실패, 건너뜀", "warn")
            continue
        collected.append((kw, products))

    if not collected:
        log("수집된 상품 없음", "warn")
        from common.notifier import notify_pipeline_result
        notify_pipeline_result("쿠팡→티스토리", 0, post_count, details="수집 실패")
        return

    # 3) 티스토리 로그인 + 발행 — TISTORY_PUBLISHER (web|bridge) 에 따라 선택
    from common.tistory_blogs import make_publisher
    pub = make_publisher(blog_name)
    if not pub.login():
        log(f"티스토리 로그인 실패 (blog={blog_name}). 종료.", "error")
        from common.notifier import notify_pipeline_result
        notify_pipeline_result("쿠팡→티스토리", 0, post_count, details="로그인 실패")
        _close_pub(pub)
        return

    published = 0
    published_keywords: list[str] = []
    last_url = ""
    from common.ai_intro import generate_related_tags

    try:
        for kw, products in collected:
            title, content, _excerpt, _slug = _build_content(kw, products)
            image_url = products[0].get("image", "")
            ai_tags = generate_related_tags(
                title, context=f"쿠팡 상품 / 키워드 {kw}", n=3,
                exclude=[kw, "쿠팡", "쿠팡파트너스", "추천상품"],
            )
            tags = [kw, "쿠팡파트너스"] + ai_tags + ["추천상품"]

            result = pub.post(
                title=title,
                content=content,
                tags=tags,
                image_url=image_url,
                category=os.getenv("TISTORY_CATEGORY", ""),
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
                            result.url, platform="tistory", title=title,
                            keyword=kw, source="coupang",
                            affiliate_url=(products[0].get("affiliate_url", "") or
                                            products[0].get("url", "")),
                        )
                    except Exception:
                        pass
            else:
                log(f"발행 실패: {result.message}", "error")

            time.sleep(random.uniform(10, 20))
    finally:
        _close_pub(pub)

    # 4) 사용 키워드 기록 — 단일 키워드 모드에서는 풀에 없을 수 있어 try
    if published_keywords and not keyword:
        try:
            mark_keywords_used(published_keywords)
        except Exception as e:
            log(f"키워드 기록 실패 ({e})", "warn")

    total = min(post_count, len(keywords))
    is_bridge = os.getenv("TISTORY_PUBLISHER", "web").strip().lower() == "bridge"
    verb = "큐 등록" if is_bridge else "발행"
    log(f"[쿠팡→티스토리] 완료: {published}/{total}건 {verb}", "step")

    # bridge 모드 + 성공 (큐 등록만 됨) → 파이프라인 알림 skip.
    # 실제 발행 완료 텔레그램 알림은 bridge server 가 /done 처리 시 보낸다.
    # (false positive "발행 성공" 알림 방지)
    if is_bridge and published > 0:
        log("[쿠팡→티스토리] bridge 모드 — 파이프라인 알림 skip", "info")
        return

    from common.notifier import notify_pipeline_result
    notify_pipeline_result(
        "쿠팡→티스토리", published, total,
        details=f"키워드: {', '.join(published_keywords)}" if published_keywords else "",
        url=last_url,
    )


if __name__ == "__main__":
    count = int(os.getenv("COUPANG_PRODUCT_COUNT", "10"))
    forced_keyword: str | None = None

    if "--count" in sys.argv:
        idx = sys.argv.index("--count")
        if idx + 1 < len(sys.argv):
            count = int(sys.argv[idx + 1])

    if "--keyword" in sys.argv:
        idx = sys.argv.index("--keyword")
        if idx + 1 < len(sys.argv):
            forced_keyword = sys.argv[idx + 1]

    run(count_per_keyword=count, keyword=forced_keyword)
