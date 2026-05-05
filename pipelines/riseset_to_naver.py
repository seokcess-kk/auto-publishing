"""
파이프라인: 일출/일몰 정보 + 쿠팡 상품 1개 → 네이버 블로그 "일출일몰" 카테고리

실행:
    python -m pipelines.riseset_to_naver

환경변수:
    NAVER_BLOG_ID       네이버 블로그 ID
    NAVER_USERNAME      네이버 아이디
    NAVER_PASSWORD      네이버 비밀번호
    DATA_GO_KR_KEY      한국천문연구원 공공데이터 API 키
    NAVER_RISESET_CATEGORY_NO  카테고리 번호 (선택, 기본 0)
    AI_PROVIDER         AI 제공자 (claude | gemini, 기본 claude)
"""
import os
import time
import random

from dotenv import load_dotenv
load_dotenv()

from common.logger import log
from sources.riseset import RiseSetSource
from sources.coupang import CoupangSource
from publishers.naver_blog import NaverBlogPublisher

from pipelines._riseset_common import (
    _LOCATIONS,
    _SUNRISE_KEYWORDS,
    build_content,
    generate_intro,
)


SCHEDULE = {
    "env":  "SCHEDULE_RISESET_NAVER",
    "func": "run",
}


# ─── 메인 ────────────────────────────────────────────────────────────────────

def run() -> None:
    """일출/일몰 + 쿠팡 상품 1개 → 네이버 블로그 발행."""
    blog_id  = os.getenv("NAVER_BLOG_ID", "")
    username = os.getenv("NAVER_USERNAME", "")
    password = os.getenv("NAVER_PASSWORD", "")
    cat_no   = int(os.getenv("NAVER_RISESET_CATEGORY_NO", "1"))  # 1 = 일출일몰

    if not all([blog_id, username, password]):
        raise ValueError("환경변수 NAVER_BLOG_ID, NAVER_USERNAME, NAVER_PASSWORD 필요")

    # ── 일출/일몰 수집
    riseset = RiseSetSource()
    info_list = riseset.get_multi_location(_LOCATIONS)
    if not info_list:
        log("일출/일몰 데이터 수집 실패. 종료.", "error")
        from common.notifier import notify_pipeline_result
        notify_pipeline_result("일출일몰→네이버블로그", 0, 1, details="일출 API 실패")
        return

    # ── 쿠팡 상품 1개 수집 (검색 결과 마지막 1개)
    keyword  = random.choice(_SUNRISE_KEYWORDS)
    channel_id = os.getenv("COUPANG_CHANNEL_ID_NAVERBLOG", "naverblog")
    coupang  = CoupangSource(channel_id=channel_id)
    products = coupang.search(keyword, count=10)
    product  = None
    if products:
        product = products[-1]          # 마지막(하단) 1개
        product["_keyword"] = keyword
        log(f"쿠팡 상품 선택: {product['name']}", "ok")
    else:
        log("쿠팡 상품 없음, 상품 섹션 제외", "warn")

    # ── AI 도입부 생성
    today     = info_list[0]["date"]
    today_kor = f"{today[:4]}년 {today[4:6]}월 {today[6:]}일"
    intro_text = generate_intro(today_kor, info_list, keyword)

    # ── 콘텐츠 빌드 (HTML 폴백용 + 제목/태그 추출).
    # 네이버 블로그는 inline <script> 를 필터링하므로 난독화 비활성.
    title, content, tags = build_content(info_list, product, obfuscate_product=False)
    if not title:
        log("콘텐츠 빌드 실패. 종료.", "error")
        return

    # ── 대표 이미지 (첫 번째 상품 이미지 사용, 없으면 빈 문자열)
    image_url = product.get("image", "") if product else ""

    # ── 네이버 블로그 로그인 & 발행
    blog = NaverBlogPublisher(blog_id, username, password)
    if not blog.login():
        log("네이버 블로그 로그인 실패. 종료.", "error")
        from common.notifier import notify_pipeline_result
        notify_pipeline_result("일출일몰→네이버블로그", 0, 1, details="로그인 실패")
        return

    result = blog.post(
        title=title,
        content=content,
        tags=tags,
        image_url=image_url,
        category_no=cat_no,
        riseset_data=info_list,
        product=product,
        intro=intro_text,
    )

    success = 1 if result.success else 0
    log(f"발행 결과: {'성공' if result.success else '실패'} — {result.url or result.message}", "step")

    # ── 댓글로 쿠팡 링크 작성
    if result.success and result.post_id and product:
        aff_url = product.get("affiliate_url", "")
        if aff_url:
            today_info = f"{today_kor} 지역별 해당 출몰시각정보 (일출, 일몰, 월출, 월몰)"
            comment_text = (
                f"구입 링크 ▶ ▶ {aff_url} ◀ ◀\n\n"
                f"{today_info}\n\n"
                f"🐾🐾🐾 파트너스 활동을 통해 일정액의 수수료를 제공받을 수 있습니다. "
                f"행운이 가득한 하루 되세요."
            )
            time.sleep(3)
            blog.post_comment(result.post_id, comment_text)

    from common.notifier import notify_pipeline_result
    notify_pipeline_result(
        "일출일몰→네이버블로그",
        success, 1,
        details=f"{title[:40]}" if result.success else result.message,
        url=result.url or "",
    )

    if result.success and result.url:
        from common.publish_queue import add_url as _add_url
        _add_url(result.url, platform="naver_blog", title=title)


if __name__ == "__main__":
    run()
