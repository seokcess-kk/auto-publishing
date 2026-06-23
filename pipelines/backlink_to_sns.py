"""
파이프라인: 워드프레스 URL 수집 → Twitter/Threads 백링크 발행

목적:
    자체 워드프레스(또는 티스토리·블로그 등) 포스트 URL 을 수집하여
    SNS 에 백링크 트윗/스레드를 반복 발행. SEO 및 노출 보조.

흐름:
    1) BACKLINK_SITES (.env, 콤마 구분) → sources.sitemap_crawler.collect_backlink_urls
    2) common.backlink_state.BacklinkState 로 이력 저장/중복 제거
    3) 플랫폼별로 pending URL N개 선택 → TwitterPublisher / ThreadsPublisher
    4) 플랫폼별 throttle (기본 300초 — 계정 잠김 방지)

실행:
    python -m pipelines.backlink_to_sns

환경변수 (.env):
    BACKLINK_SITES           = https://a.mycafe24.com,https://b.tistory.com
    BACKLINK_COUNT           = 5             # 1회 실행당 플랫폼별 발행 수
    BACKLINK_TARGETS         = twitter,threads
    BACKLINK_THROTTLE        = 300           # 발행 사이 대기(초)
    BACKLINK_STRATEGIES      = jetpack,rest  # sitemap 전략
    BACKLINK_MESSAGE_PREFIX  = "오늘의 정보"  # 링크 앞에 붙일 문구 (선택)

참조:
    wordpress_twitter_auto_backlink/ch05_semi_final/
    00.Old_Source/backlink/backlink_tistory_wordpress_naver_link_upload_ver8.py
"""
import html
import os
import random
import time
from typing import Iterable, Optional

from dotenv import load_dotenv
load_dotenv()

from common.backlink_state import BacklinkState
from common.logger import log
from sources.sitemap_crawler import collect_backlink_urls


SCHEDULE = {
    "env":  "SCHEDULE_BACKLINK_SNS",
    "func": "run",
}


def _parse_env_list(name: str, default: str = "") -> list:
    raw = os.getenv(name, default)
    return [x.strip() for x in raw.split(",") if x.strip()]


def refresh_url_pool(sites: Iterable[str],
                     strategies: Iterable[str],
                     state: BacklinkState) -> int:
    """sitemap/REST 크롤 → 상태 저장소에 신규 URL 추가. 추가된 건수 반환."""
    records = collect_backlink_urls(sites, strategies=strategies)
    added = state.add_urls(records)
    log(f"신규 URL {added}개 추가 (누적 {len(state._data)}개)", "ok")
    return added


def _build_tweet_text(url: str, title: str, prefix: str) -> str:
    """트윗 본문 — prefix + 제목 + URL. 280자 제한 대응."""
    # 제목에 남아있을 수 있는 HTML 엔티티(&ldquo; &rdquo; &hellip; 등)를 디코딩.
    # 추출 시점에서도 처리하지만, 과거 backlink_state 에 저장된 제목이나 다른
    # 소스를 위해 발행 직전에도 한 번 더 정리한다(html.unescape 는 멱등).
    title = html.unescape(title or "")
    parts = []
    if prefix:
        parts.append(prefix)
    if title:
        parts.append(title)
    parts.append(url)
    text = "\n".join(parts)
    if len(text) > 270:
        keep = 270 - len(url) - 2
        if keep > 0 and title:
            title_trim = title[:keep] + "..."
            text = f"{prefix}\n{title_trim}\n{url}" if prefix else f"{title_trim}\n{url}"
        else:
            text = url
    return text


def _post_to_platform(platform: str, pub, record: dict,
                      prefix: str, state: BacklinkState) -> bool:
    url = record["url"]
    title = record.get("title", "")
    text = _build_tweet_text(url, title, prefix)

    try:
        result = pub.post(title="", content=text, tags=[])
    except Exception as e:
        log(f"[{platform}] 발행 예외: {e}", "error")
        state.mark_posted(url, platform, status="fail")
        return False

    if result and result.success:
        state.mark_posted(url, platform, post_url=result.url, status="ok")
        log(f"[{platform}] ✅ {url} → {result.url}", "ok")
        return True
    else:
        state.mark_posted(url, platform, status="fail")
        msg = getattr(result, "message", "") if result else ""
        log(f"[{platform}] ❌ {url} ({msg[:100]})", "warn")
        return False


def run(count: Optional[int] = None,
        targets: Optional[list] = None,
        sites: Optional[list] = None,
        throttle: Optional[int] = None,
        strategies: Optional[list] = None,
        prefix: Optional[str] = None,
        refresh_pool: bool = True) -> dict:
    """백링크 파이프라인 1회 실행.

    Returns:
        {"twitter": 3, "threads": 2, "total_expected": 10}
    """
    count       = count      if count      is not None else int(os.getenv("BACKLINK_COUNT", "5"))
    targets     = targets    or _parse_env_list("BACKLINK_TARGETS", "twitter")
    sites       = sites      or _parse_env_list("BACKLINK_SITES")
    throttle    = throttle   if throttle   is not None else int(os.getenv("BACKLINK_THROTTLE", "300"))
    strategies  = strategies or _parse_env_list("BACKLINK_STRATEGIES", "jetpack,rest")
    prefix      = prefix     if prefix     is not None else os.getenv("BACKLINK_MESSAGE_PREFIX", "")

    if not sites:
        log("BACKLINK_SITES 미설정 — 수집 대상 없음", "error")
        return {}

    state = BacklinkState()

    if refresh_pool:
        log("=== 1단계: URL 풀 갱신 ===", "step")
        refresh_url_pool(sites, strategies, state)

    # 플랫폼 로그인
    log("=== 2단계: 플랫폼 로그인 ===", "step")
    publishers = {}
    if "twitter" in targets:
        from publishers.twitter import TwitterPublisher
        tw = TwitterPublisher()
        if tw.login():
            publishers["twitter"] = tw
        else:
            log("트위터 로그인 실패 — 스킵", "warn")

    if "threads" in targets:
        from publishers.threads import ThreadsPublisher
        th = ThreadsPublisher()
        if th.login():
            publishers["threads"] = th
        else:
            log("스레드 로그인 실패 — 스킵", "warn")

    if not publishers:
        log("활성화된 발행 플랫폼 없음", "error")
        return {}

    # 발행
    log(f"=== 3단계: 백링크 발행 (count={count} 플랫폼당, throttle={throttle}s) ===", "step")
    published = {plat: 0 for plat in publishers}

    for platform, pub in publishers.items():
        pending = state.pending_urls(platform, limit=count)
        if not pending:
            log(f"[{platform}] 발행 대기 URL 없음", "info")
            continue

        log(f"[{platform}] {len(pending)}개 발행 시작", "step")
        for idx, rec in enumerate(pending, 1):
            if _post_to_platform(platform, pub, rec, prefix, state):
                published[platform] += 1

            if idx < len(pending):
                wait = throttle + random.randint(0, 30)
                log(f"[{platform}] 다음 발행까지 {wait}s 대기", "info")
                time.sleep(wait)

        log(f"[{platform}] {published[platform]}/{len(pending)}건 성공", "step")

    # 알림
    from common.notifier import notify_pipeline_result
    total = sum(published.values())
    expected = count * len(publishers)
    details = ", ".join(f"{k}:{v}" for k, v in published.items())
    notify_pipeline_result("백링크→SNS", total, expected, details=details)

    stats = state.stats()
    log(f"상태 통계: {stats}", "info")
    return {**published, "total_expected": expected}


if __name__ == "__main__":
    run()
