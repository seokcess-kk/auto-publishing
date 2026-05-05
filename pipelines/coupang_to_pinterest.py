"""
파이프라인: 쿠팡 상품 크롤링 → Pinterest 핀 발행

- ItemScout 키워드 풀에서 미사용 키워드 수집
- 쿠팡에서 키워드별 상위 상품 추출
- 각 상품 이미지 다운로드 → Pinterest 핀 발행 (affiliate link)
- Playwright 자동화 (Google 로그인)
- 파이프라인 완료 시 텔레그램 알림

실행:
    python -m pipelines.coupang_to_pinterest
    python -m pipelines.coupang_to_pinterest --count 5
    python -m pipelines.coupang_to_pinterest --pins-per-keyword 3
"""
import os
import sys
import random
import tempfile
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()

from common.logger import log
from sources.coupang import CoupangSource


SCHEDULE = {
    "env":  "SCHEDULE_COUPANG_PINTEREST",
    "func": "run",
}


BOARD_NAME = os.getenv("PINTEREST_BOARD_NAME", "How RU")
IMAGE_TMP_DIR = Path(os.getenv(
    "PINTEREST_TMP_DIR",
    os.path.join(tempfile.gettempdir(), "pinterest_pipeline"),
))

DEFAULT_KEYWORDS = [
    "인기상품", "베스트셀러", "추천상품", "주방용품", "생활용품",
    "건강식품", "뷰티", "스포츠용품", "디지털가전", "패션잡화",
]

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0.0.0 Safari/537.36"
)


def get_keywords(n: int) -> list:
    """ItemScout 키워드 풀에서 미사용 키워드 반환. 실패 시 기본 키워드 사용."""
    from sources.itemscout_keywords import get_next_keywords

    try:
        keywords = get_next_keywords(n=n, refill_threshold=50)
        if keywords:
            return keywords
    except Exception as e:
        log(f"ItemScout 키워드 풀 실패 ({e}), 기본 키워드 사용", "warn")

    return random.sample(DEFAULT_KEYWORDS, k=min(n, len(DEFAULT_KEYWORDS)))


def download_image(url: str, dest: Path) -> bool:
    try:
        if url.startswith("//"):
            url = "https:" + url
        r = requests.get(url, timeout=15, headers={"User-Agent": USER_AGENT})
        r.raise_for_status()
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(r.content)
        return True
    except Exception as e:
        log(f"이미지 다운로드 실패: {e}", "error")
        return False


def build_pin_meta(product: dict, keyword: str) -> dict:
    """쿠팡 상품 → Pinterest 핀 메타데이터."""
    title = product.get("name", "")[:100]
    price = product.get("price", "")
    rating = product.get("rating", "")
    review = product.get("review_count", "")

    parts = []
    if price:
        parts.append(f"💰 {price}")
    if rating and rating != "No data":
        parts.append(f"⭐ {rating} ({review} 리뷰)")
    parts.append("👉 자세히 보기")
    description = "\n".join(parts)

    return {
        "title": title,
        "description": description,
        "tags": ["쿠팡", "추천템", "가성비", keyword],
        "link": product.get("affiliate_url", ""),
    }


def run(count: int = 1, pins_per_keyword: int = 1) -> None:
    """쿠팡 크롤링 → Pinterest 발행 파이프라인.

    Args:
        count            : 처리할 키워드 수
        pins_per_keyword : 키워드당 발행할 핀 개수
    """
    from sources.itemscout_keywords import mark_keywords_used, get_pool_status
    from publishers.pinterest_playwright import PinterestPlaywrightPublisher

    log(f"Pinterest 파이프라인 시작 (키워드 {count}개, 핀/키워드 {pins_per_keyword})", "step")
    try:
        log(get_pool_status(), "info")
    except Exception:
        pass

    channel_id = os.getenv("COUPANG_CHANNEL_ID_PINTEREST", "pinterest")
    coupang = CoupangSource(channel_id=channel_id)
    keywords = get_keywords(n=count)

    publisher = PinterestPlaywrightPublisher()
    log(f"Pinterest 로그인 시작 (method={publisher.login_method})", "step")
    if not publisher.login():
        log("Pinterest 로그인 실패로 중단", "error")
        publisher.close()

        from common.notifier import notify_pipeline_result
        notify_pipeline_result(
            "쿠팡→Pinterest", 0, count, details="로그인 실패",
        )
        return

    total_expected = 0
    total_published = 0
    published_keywords = []

    try:
        for keyword in keywords[:count]:
            log(f"키워드 처리: {keyword}", "step")
            products = coupang.search(keyword, count=10)
            if not products:
                log(f"'{keyword}' 상품 없음, 건너뜀", "warn")
                continue

            # 이미지 + affiliate_url 보유 상품 필터
            valid = [p for p in products
                     if p.get("image") and p.get("affiliate_url")]
            targets = valid[:pins_per_keyword]
            if not targets:
                log(f"'{keyword}' 유효 상품 없음", "warn")
                continue

            keyword_success = 0
            for idx, product in enumerate(targets):
                total_expected += 1
                image_path = IMAGE_TMP_DIR / f"{keyword}_{idx}.jpg"
                if not download_image(product["image"], image_path):
                    continue

                meta = build_pin_meta(product, keyword)
                result = publisher.post(
                    title=meta["title"],
                    content=meta["description"],
                    tags=meta["tags"],
                    media_path=str(image_path),
                    link=meta["link"],
                    board_name=BOARD_NAME,
                )
                if result.success:
                    total_published += 1
                    keyword_success += 1
                    log(f"  ✅ 핀 발행: {meta['title'][:40]}", "ok")
                else:
                    log(f"  ❌ 핀 실패: {result.message}", "error")

                # 핀 사이 대기 (레이트 리밋 회피)
                time.sleep(random.uniform(5, 10))

            if keyword_success > 0:
                published_keywords.append(keyword)

            # 키워드 사이 대기
            time.sleep(random.uniform(10, 20))
    finally:
        publisher.close()

    if published_keywords:
        try:
            mark_keywords_used(published_keywords)
        except Exception as e:
            log(f"키워드 사용 마킹 실패: {e}", "warn")

    log(f"Pinterest 파이프라인 완료: {total_published}/{total_expected}건 발행", "step")

    from common.notifier import notify_pipeline_result
    details = f"키워드: {', '.join(published_keywords)}" if published_keywords else ""
    notify_pipeline_result(
        "쿠팡→Pinterest",
        total_published, total_expected or count,
        details=details,
    )


if __name__ == "__main__":
    post_count = int(os.getenv("POST_COUNT", "1"))
    pins_per_keyword = int(os.getenv("PINTEREST_PINS_PER_KEYWORD", "1"))

    if "--count" in sys.argv:
        i = sys.argv.index("--count")
        if i + 1 < len(sys.argv):
            post_count = int(sys.argv[i + 1])

    if "--pins-per-keyword" in sys.argv:
        i = sys.argv.index("--pins-per-keyword")
        if i + 1 < len(sys.argv):
            pins_per_keyword = int(sys.argv[i + 1])

    run(count=post_count, pins_per_keyword=pins_per_keyword)
