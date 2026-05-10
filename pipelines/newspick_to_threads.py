"""
파이프라인: 뉴스픽 기사 1건 → Threads (Meta Graph API) 발행

- 본문 톤:   반말 SNS 스타일 (generate_newspick_threads_caption 참고)
- 글자 수:   230자 이내 (above-the-fold 노출 최적)
- 해시태그:  사용 안 함 (Threads 톤상 부적합)
- 링크:      본문에 뉴스픽 단축 어필리에이트 링크 자동 추가
- 의무 고지: 사용 안 함 (뉴스픽 추천인 링크는 광고성 표시 의무 미적용 — 사용자 결정)

실행:
    python -m pipelines.newspick_to_threads
    python -m pipelines.newspick_to_threads --category 경제
"""
from __future__ import annotations

import os
import sys

from dotenv import load_dotenv
load_dotenv()

from common.ai_intro import generate_newspick_threads_caption
from common.logger import log
from publishers.threads import ThreadsPublisher
from sources.newspick import NewspickSource


SCHEDULE = {
    "env":  "SCHEDULE_NEWSPICK_THREADS",
    "func": "run",
    "args_from_env": ("NEWSPICK_CATEGORY:추천",),
}


def run(category: str = "추천") -> None:
    """뉴스픽 기사 1건 크롤링 → Threads 발행."""
    log(f"[뉴스픽→Threads] 시작 (category={category})", "step")

    # 1) 뉴스픽 세션 + 기사 1건
    newspick = NewspickSource(referral_code=os.getenv("NEWSPICK_REFERRAL", ""))
    if not newspick.ensure_session():
        log("뉴스픽 세션 없음", "error")
        from common.notifier import notify_pipeline_result
        notify_pipeline_result("뉴스픽→Threads", 0, 1, details="세션 없음")
        return

    # fetch 가 추천+일반 두 소스에서 가져오므로 명시적 절단
    articles = newspick.fetch_with_links(category=category, count=1)[:1]
    if not articles:
        log("기사 0건", "warn")
        from common.notifier import notify_pipeline_result
        notify_pipeline_result("뉴스픽→Threads", 0, 1, details="기사 없음")
        return

    article = articles[0]
    title = article["title"]
    short_url = article.get("short_url", "") or article.get("url", "")
    log(f"기사: {title[:50]}", "info")

    # 2) AI Threads 캡션
    caption = generate_newspick_threads_caption(title, category=category, max_chars=230)
    if not caption:
        # 폴백 — 단순 제목 + 안내
        caption = f"{title}\n\n👇 자세한 내용은 아래에서"

    # 3) 본문 = 캡션 + 빈 줄 + 단축링크 (뉴스픽 고지는 미부착)
    body = caption
    if short_url:
        body = f"{caption}\n\n👉 {short_url}"

    # 4) Threads 발행
    pub = ThreadsPublisher()
    if not pub.login():
        log("Threads API 인증 실패 — .env 토큰 확인", "error")
        from common.notifier import notify_pipeline_result
        notify_pipeline_result("뉴스픽→Threads", 0, 1, details="인증 실패")
        return

    result = pub.post(
        title="", content=body, tags=[],
        image_url=article.get("image", "") or "",
    )

    if result.success:
        log(f"발행 완료: {result.url}", "ok")
        if result.url:
            try:
                from common.publish_queue import add_url as _add_url
                _add_url(result.url, platform="threads", title=title)
            except Exception:
                pass

        from common.notifier import notify_pipeline_result
        notify_pipeline_result(
            "뉴스픽→Threads", 1, 1,
            details=f"{category} / {title[:40]}",
            url=result.url or "",
        )
    else:
        log(f"발행 실패: {result.message}", "error")
        from common.notifier import notify_pipeline_result
        notify_pipeline_result("뉴스픽→Threads", 0, 1,
                               details=str(result.message)[:200])


if __name__ == "__main__":
    cat = os.getenv("NEWSPICK_CATEGORY", "추천")
    if "--category" in sys.argv:
        idx = sys.argv.index("--category")
        if idx + 1 < len(sys.argv):
            cat = sys.argv[idx + 1]
    run(category=cat)
