"""
파이프라인: 일출/일몰 정보 + 쿠팡 상품 1개 → 티스토리

실행:
    python -m pipelines.riseset_to_tistory

환경변수:
    TISTORY_BLOG_RISESET  티스토리 블로그 ID (미설정 시 TISTORY_BLOG_NAME 폴백)
    TISTORY_CATEGORY      카테고리명 (선택)
    DATA_GO_KR_KEY        한국천문연구원 공공데이터 API 키
    AI_PROVIDER           claude | gemini (본문 생성)
    COUPANG_CHANNEL_ID_TISTORY   쿠팡 파트너스 채널 ID (선택)

riseset_to_naver 의 콘텐츠 빌더를 재사용하여 동일한 HTML 을 티스토리에도 발행한다.
"""
import os
import random

from dotenv import load_dotenv
load_dotenv()

from common.logger import log
from common.tistory_blogs import resolve_blog_name
from sources.riseset import RiseSetSource
from sources.coupang import CoupangSource
from publishers.tistory import TistoryPublisher

from pipelines._riseset_common import (
    _LOCATIONS,
    _SUNRISE_KEYWORDS,
    build_content,
    generate_intro,
)


SCHEDULE = {
    "env":  "SCHEDULE_RISESET_TISTORY",
    "func": "run",
}


def run() -> None:
    """일출/일몰 + 쿠팡 상품 1개 → 티스토리 발행."""
    blog_name = resolve_blog_name("riseset")

    # ── 일출/일몰 수집
    riseset = RiseSetSource()
    info_list = riseset.get_multi_location(_LOCATIONS)
    if not info_list:
        log("일출/일몰 데이터 수집 실패. 종료.", "error")
        from common.notifier import notify_pipeline_result
        notify_pipeline_result("일출일몰→티스토리", 0, 1, details="일출 API 실패")
        return

    # ── 쿠팡 상품 1개 수집
    keyword    = random.choice(_SUNRISE_KEYWORDS)
    channel_id = os.getenv("COUPANG_CHANNEL_ID_TISTORY", "tistoryriseset")
    coupang    = CoupangSource(channel_id=channel_id)
    product    = None
    try:
        products = coupang.search(keyword, count=10)
    except Exception as e:
        log(f"쿠팡 크롤링 실패(무시): {e}", "warn")
        products = []
    if products:
        product = products[-1]
        product["_keyword"] = keyword
        log(f"쿠팡 상품 선택: {product['name']}", "ok")
    else:
        log("쿠팡 상품 없음, 상품 섹션 제외", "warn")

    # ── AI 도입부 (HTML 빌더 내부에서 참조하지는 않지만 로그용으로 호출)
    today     = info_list[0]["date"]
    today_kor = f"{today[:4]}년 {today[4:6]}월 {today[6:]}일"
    try:
        _ = generate_intro(today_kor, info_list, keyword)
    except Exception as e:
        log(f"AI 도입부 생성 실패(무시): {e}", "warn")

    # ── 콘텐츠 빌드
    title, content, tags = build_content(info_list, product)
    if not title:
        log("콘텐츠 빌드 실패. 종료.", "error")
        from common.notifier import notify_pipeline_result
        notify_pipeline_result("일출일몰→티스토리", 0, 1, details="콘텐츠 빌드 실패")
        return

    image_url = product.get("image", "") if product else ""

    # ── 티스토리 로그인 & 발행
    pub = TistoryPublisher(blog_name)
    if not pub.login():
        log(f"티스토리 로그인 실패 (blog={blog_name}). 종료.", "error")
        from common.notifier import notify_pipeline_result
        notify_pipeline_result("일출일몰→티스토리", 0, 1, details="로그인 실패")
        _close(pub)
        return

    try:
        result = pub.post(
            title=title,
            content=content,
            tags=tags,
            image_url=image_url,
            category=os.getenv("TISTORY_CATEGORY", ""),
        )
    finally:
        _close(pub)

    from common.notifier import notify_pipeline_result
    if result.success:
        log(f"발행 완료: {result.url}", "ok")
        notify_pipeline_result("일출일몰→티스토리", 1, 1, details=title, url=result.url or "")
        if result.url:
            from common.publish_queue import add_url as _add_url
            _add_url(result.url, platform="tistory", title=title)
    else:
        log(f"발행 실패: {result.message}", "error")
        notify_pipeline_result("일출일몰→티스토리", 0, 1, details=str(result.message))


def _close(pub) -> None:
    close = getattr(pub, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            pass


if __name__ == "__main__":
    run()
