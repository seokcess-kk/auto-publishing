"""
파이프라인: 백링크 발행 (4채널)

publish_queue.json 의 백링크 미발행 URL 을 Twitter / Threads / Tistory(백링크용) /
WordPress 4채널에 묶음 포스트로 발행.

색인 파이프라인(indexing_pipeline.py)과 분리. 색인 30분 후 실행하면 같은 날
색인된 URL 이 우선 픽업됨. 색인이 안 돌았어도 단독 실행 가능 (미백링크 폴백).

실행:
    python -m pipelines.backlink_pipeline

환경변수 (.env):
    SCHEDULE_BACKLINK              = 22:30
    TISTORY_BLOG_BACKLINK          = <본인 티스토리 블로그 ID>
    TISTORY_BACKLINK_CATEGORY_ID   = <카테고리 ID>
    WP_BACKLINK_CATEGORY_ID        = <카테고리 ID>
    WP_BACKLINK_TAG_ID             = <태그 ID>
"""
import os
from datetime import datetime

from dotenv import load_dotenv
load_dotenv()

from common.logger import log
from common.publish_queue import (
    get_newly_indexed_today,
    get_pending_backlink,
    mark_done_bulk,
    stats,
)
from common.notifier import notify_pipeline_result


SCHEDULE = {
    "env":  "SCHEDULE_BACKLINK",
    "func": "run",
}

BACKLINK_MAX_URLS = 30  # 백링크 포스트에 담을 최대 URL 수
TWEET_MAX = 270         # SNS 백링크 트윗 최대 글자 수


# ─── HTML / 텍스트 빌더 ──────────────────────────────────────────────────────

def _build_backlink_html(items: list) -> tuple:
    """백링크 포스트 HTML + 제목 생성."""
    today = datetime.now().strftime("%Y년 %m월 %d일")
    title = f"{today} 관심 목록 TOP {len(items)}+"

    li_tags = "\n".join(
        f'  <li><a href="{item["url"]}">{item.get("title", "") or item["url"]}</a></li>'
        for item in items
    )
    html_body = f"<p>관심 가는 링크:</p>\n<ul>\n{li_tags}\n</ul>"
    return title, html_body


def _build_sns_text(items: list, prefix: str = "오늘의 추천 링크 🔗") -> str:
    """Twitter/Threads용 텍스트. 280자 제한 대응."""
    lines = [prefix]
    for item in items:
        url = item["url"]
        title = item.get("title", "")
        line = f"• {title} {url}" if title else f"• {url}"
        candidate = "\n".join(lines + [line])
        if len(candidate) > TWEET_MAX:
            remaining = len(items) - (len(lines) - 1)
            if remaining > 0:
                lines.append(f"외 {remaining}건 더")
            break
        lines.append(line)
    return "\n".join(lines)


# ─── 채널별 발행 ─────────────────────────────────────────────────────────────

def _post_sns(items: list) -> dict:
    """Twitter + Threads 백링크 포스트. {'twitter': 0|1, 'threads': 0|1} 반환."""
    published = {}
    text = _build_sns_text(items)

    for platform, PublisherClass in [
        ("twitter",  "publishers.twitter.TwitterPublisher"),
        ("threads",  "publishers.threads.ThreadsPublisher"),
    ]:
        try:
            module, cls = PublisherClass.rsplit(".", 1)
            import importlib
            pub = getattr(importlib.import_module(module), cls)()
            if not pub.login():
                log(f"[백링크] {platform} 로그인 실패", "warn")
                published[platform] = 0
                continue
            result = pub.post(title="", content=text, tags=[])
            if result and result.success:
                log(f"[백링크] {platform} 발행 성공: {result.url}", "ok")
                published[platform] = 1
            else:
                msg = getattr(result, "message", "") if result else ""
                log(f"[백링크] {platform} 발행 실패: {msg}", "warn")
                published[platform] = 0
        except Exception as e:
            log(f"[백링크] {platform} 예외: {e}", "error")
            published[platform] = 0

    return published


def _post_tistory_backlink(items: list) -> bool:
    """Tistory 백링크 블로그에 관심 링크 목록 글 발행."""
    try:
        from publishers.tistory import TistoryPublisher
        blog_name = os.getenv("TISTORY_BLOG_BACKLINK", "")
        if not blog_name:
            log("[백링크] TISTORY_BLOG_BACKLINK 미설정 — Tistory 발행 skip", "warn")
            return False

        title, html_body = _build_backlink_html(items)

        pub = TistoryPublisher(blog_name=blog_name)
        if not pub.login():
            log("[백링크] Tistory 로그인 실패", "warn")
            return False

        result = pub.post(
            title=title,
            content=html_body,
            tags=["백링크", "관심링크", "추천"],
            category="",
        )
        pub.close()

        if result and result.success:
            log(f"[백링크] Tistory 발행 성공: {result.url}", "ok")
            return True
        msg = getattr(result, "message", "") if result else ""
        log(f"[백링크] Tistory 발행 실패: {msg}", "warn")
        return False
    except Exception as e:
        log(f"[백링크] Tistory 예외: {e}", "error")
        return False


def _post_wp_backlink(items: list) -> bool:
    """WordPress 에 관심 링크 목록 글 발행."""
    try:
        from publishers.wordpress import WordPressPublisher

        site_url     = os.getenv("WP_SITE_URL", "")
        jwt_token    = os.getenv("WP_JWT_TOKEN", "")
        username     = os.getenv("WP_USERNAME", "")
        app_password = os.getenv("WP_APP_PASSWORD", "")
        category_id  = int(os.getenv("WP_BACKLINK_CATEGORY_ID", "19"))
        tag_id       = int(os.getenv("WP_BACKLINK_TAG_ID", "18"))

        title, html_body = _build_backlink_html(items)

        if jwt_token:
            pub = WordPressPublisher(site_url=site_url, jwt_token=jwt_token)
        else:
            pub = WordPressPublisher(
                site_url=site_url,
                username=username,
                app_password=app_password,
            )
        if not pub.login():
            log("[백링크] WordPress 로그인 실패", "warn")
            return False

        result = pub.post_with_ids(
            title=title,
            content=html_body,
            category_id=category_id,
            tag_id=tag_id,
        )

        if result and result.success:
            log(f"[백링크] WordPress 발행 성공: {result.url}", "ok")
            return True
        msg = getattr(result, "message", "") if result else ""
        log(f"[백링크] WordPress 발행 실패: {msg}", "warn")
        return False
    except Exception as e:
        log(f"[백링크] WordPress 예외: {e}", "error")
        return False


def _post_backlinks(items: list) -> dict:
    """4채널 백링크 발행 후 backlinked 상태 업데이트.

    Returns:
        {'twitter': 0|1, 'threads': 0|1, 'tistory': 0|1, 'wp': 0|1}
    """
    items = items[:BACKLINK_MAX_URLS]
    if not items:
        return {"twitter": 0, "threads": 0, "tistory": 0, "wp": 0}

    log(f"[백링크] {len(items)}개 URL 백링크 발행 시작", "step")

    sns_result   = _post_sns(items)
    tistory_ok   = _post_tistory_backlink(items)
    wp_ok        = _post_wp_backlink(items)

    results = {
        "twitter": sns_result.get("twitter", 0),
        "threads": sns_result.get("threads", 0),
        "tistory": int(tistory_ok),
        "wp":      int(wp_ok),
    }

    # 하나라도 성공하면 backlinked 처리
    if any(results.values()):
        urls = [item["url"] for item in items]
        mark_done_bulk(urls, "backlinked")
        log(f"[백링크] {len(urls)}건 backlinked 완료 처리", "ok")

    log(
        f"[백링크] 결과 — Twitter:{results['twitter']} Threads:{results['threads']} "
        f"Tistory:{results['tistory']} WP:{results['wp']}",
        "step",
    )
    return results


# ─── 메인 ────────────────────────────────────────────────────────────────────

def run() -> None:
    """백링크 발행 파이프라인 1회 실행."""
    log("=== 백링크 발행 파이프라인 시작 ===", "step")

    backlink_items = get_newly_indexed_today()
    if not backlink_items:
        # 오늘 색인 완료 항목 없음 → 미백링크 항목 전체에서 폴백
        log("[백링크] 오늘 색인 완료 URL 없음 — 미백링크 항목으로 폴백", "info")
        backlink_items = get_pending_backlink()

    if backlink_items:
        results = _post_backlinks(backlink_items)
    else:
        log("[백링크] 발행 대상 URL 없음", "info")
        results = {"twitter": 0, "threads": 0, "tistory": 0, "wp": 0}

    log(f"큐 통계: {stats()}", "info")

    channels_attempted = 4 if backlink_items else 0
    channels_succeeded = sum(1 for v in results.values() if v)
    reason = "empty" if not backlink_items else "failure"
    notify_pipeline_result(
        "백링크 발행",
        channels_succeeded,
        channels_attempted,
        details=(
            f"Twitter:{results['twitter']} Threads:{results['threads']} "
            f"Tistory:{results['tistory']} WP:{results['wp']} "
            f"({len(backlink_items)} URLs)"
        ),
        reason=reason,
    )
    log("=== 백링크 발행 파이프라인 완료 ===", "step")


if __name__ == "__main__":
    run()
