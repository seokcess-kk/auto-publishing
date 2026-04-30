"""
파이프라인: 뉴스픽 → SNS (트위터 + 스레드)

실행:
    python -m pipelines.newspick_to_sns
"""
import os
import random
import time

from dotenv import load_dotenv
load_dotenv()

from common.logger import log
from sources.newspick import NewspickSource
from sources.gemini_generator import GeminiGenerator
from publishers.twitter import TwitterPublisher
from publishers.threads import ThreadsPublisher


SCHEDULE = {
    "env":  "SCHEDULE_NEWSPICK_SNS",
    "func": "run",
    "args_from_env": ("NEWSPICK_CATEGORY:추천", "POST_COUNT:1:int"),
}


def run(category: str = "추천", count: int = 1,
        targets: list[str] = None) -> None:
    """뉴스픽 → 트위터 + 스레드 동시 발행.

    Args:
        category: 뉴스픽 카테고리
        count:    발행 글 수
        targets:  ['twitter', 'threads'] (기본: 둘 다)
    """
    if targets is None:
        targets = ["twitter", "threads"]

    newspick = NewspickSource(referral_code=os.getenv("NEWSPICK_REFERRAL", ""))
    gemini   = GeminiGenerator()

    publishers = {}
    if "twitter" in targets:
        tw = TwitterPublisher()
        if tw.login():
            publishers["twitter"] = tw
        else:
            log("트위터 세션 없음 — login_with_driver() 필요", "warn")

    if "threads" in targets:
        th = ThreadsPublisher()
        if th.login():
            publishers["threads"] = th
        else:
            log("스레드 세션 없음 — login_with_driver() 필요", "warn")

    if not publishers:
        log("발행 가능한 SNS 없음", "error")
        return

    if not newspick.ensure_session():
        log("뉴스픽 세션 없음", "error")
        return

    articles  = newspick.fetch_with_links(category=category, count=count)
    published = {k: 0 for k in publishers}

    for article in articles:
        title     = article["title"]
        short_url = article["short_url"]

        # SNS용 짧은 설명 생성
        if article.get("summary"):
            desc = gemini.summarize(article["summary"], max_sentences=2)
        else:
            desc = title

        # 해시태그
        hashtags = gemini.generate_hashtags(title, count=5)

        tweet_text = f"{desc}\n\n{short_url}"

        for platform, pub in publishers.items():
            result = pub.post(
                title=title,
                content=tweet_text,
                tags=hashtags,
                image_url=article.get("image", ""),
            )
            if result.success:
                published[platform] += 1
                log(f"[{platform}] {result.url}", "ok")
            time.sleep(random.uniform(3, 8))

        time.sleep(random.uniform(10, 20))

    for platform, cnt in published.items():
        log(f"{platform}: {cnt}/{count}건 발행", "step")

    from common.notifier import notify_pipeline_result
    total_published = sum(published.values())
    total_expected = count * len(publishers)
    platforms_str = ", ".join(f"{k}:{v}/{count}" for k, v in published.items())
    notify_pipeline_result("뉴스픽→SNS", total_published, total_expected, details=platforms_str)


if __name__ == "__main__":
    run(
        category=os.getenv("NEWSPICK_CATEGORY", "추천"),
        count=int(os.getenv("POST_COUNT", "1")),
        targets=os.getenv("SNS_TARGETS", "twitter,threads").split(","),
    )
