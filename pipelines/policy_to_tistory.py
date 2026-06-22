"""
파이프라인: 정책브리핑(korea.kr) RSS → 티스토리

기존 RSS 수집 자산(sources/korea_policy.py)을 재사용하여 이미 수집된
정책뉴스를 다양한 플랫폼으로 확산한다 (현재는 티스토리 전용).

실행:
    python -m pipelines.policy_to_tistory
    python -m pipelines.policy_to_tistory --count 5

환경변수:
    TISTORY_BLOG_POLICY   티스토리 블로그 ID (미설정 시 TISTORY_BLOG_NAME 폴백)
    POLICY_FEEDS          수집 피드명 콤마 구분 (기본 "정책뉴스,보도자료,이슈인사이트")
    POLICY_POST_COUNT     1회 실행당 발행 글 수 (기본 1)
    TISTORY_CATEGORY      카테고리명 (선택)
"""
import os
import sys
import random
import time

from dotenv import load_dotenv
load_dotenv()

from common.logger import log
from common.product_card import (
    GENERIC_DEFAULT_KEYWORDS,
    fetch_recommend_product,
    render_product_card,
)
from common.tistory_blogs import resolve_blog_name, make_publisher


SCHEDULE = {
    "env":  "SCHEDULE_POLICY_TISTORY",
    "func": "run",
}


# feed_name → KoreaPolicySource 의 fetch_* 메서드
_FEED_FETCHERS = {
    "정책뉴스":        "fetch_policy_news",
    "국민이말하는정책": "fetch_reporter",
    "정책칼럼":        "fetch_column",
    "이슈인사이트":    "fetch_insight",
    "보도자료":        "fetch_pressrelease",
    "사실은이렇습니다": "fetch_fact",
    "부처브리핑":      "fetch_ebriefing",
    "청와대브리핑":    "fetch_president",
    "국무회의브리핑":  "fetch_cabinet",
    "연설문":          "fetch_speech",
}

DEFAULT_FEEDS = ["정책뉴스", "보도자료", "이슈인사이트"]


# ─── 상태: 중복 발행 방지 ────────────────────────────────────────────────────

_STATE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data", "policy_posted.json",
)


def _load_posted() -> set:
    import json
    if not os.path.exists(_STATE_PATH):
        return set()
    try:
        with open(_STATE_PATH, "r", encoding="utf-8") as f:
            return set(json.load(f))
    except Exception:
        return set()


def _save_posted(urls: set) -> None:
    import json
    os.makedirs(os.path.dirname(_STATE_PATH), exist_ok=True)
    with open(_STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(sorted(urls), f, ensure_ascii=False, indent=2)


# ─── 콘텐츠 빌드 ─────────────────────────────────────────────────────────────

def _build_html(item: dict) -> tuple:
    """RSS 항목 → (title, html, tags)."""
    title   = item.get("title", "").strip()
    link    = item.get("link", "").strip()
    summary = item.get("summary", "").strip()
    image   = item.get("image", "")
    cat     = item.get("category", "정책")
    pub     = item.get("pub_date", "")

    img_html = (
        f'<p style="text-align:center;"><img src="{image}" alt="{title}" '
        f'style="max-width:680px;border-radius:10px;"/></p>'
        if image else ""
    )

    summary_html = f"<blockquote>{summary}</blockquote>" if summary else ""

    body = (
        f'<div style="max-width:680px;margin:0 auto;font-family:-apple-system,\'Noto Sans KR\',sans-serif;line-height:1.7;">'
        f'<h2 style="font-size:20px;color:#222;margin-bottom:12px;">{title}</h2>'
        f'<p style="font-size:12px;color:#888;margin-bottom:18px;">📅 {pub} · 분류: {cat}</p>'
        f'{img_html}'
        f'{summary_html}'
        f'<p style="margin-top:24px;">📎 원문 전체 보기: '
        f'<a href="{link}" target="_blank" rel="noopener">{link}</a></p>'
        f'<p style="text-align:center;font-size:11px;color:#bbb;margin-top:24px;">'
        f'※ 본 게시물은 공공데이터(korea.kr RSS)를 기반으로 재구성되었습니다.</p>'
        f'</div>'
    )

    tags = ["정책", "정책뉴스", cat]
    if "보도자료" in cat:
        tags.append("보도자료")

    return title, body, tags[:8]


# ─── 메인 ────────────────────────────────────────────────────────────────────

def run(count: int = 1, feeds: list = None) -> None:
    """korea.kr RSS 수집 → 티스토리 발행."""
    from sources.korea_policy import KoreaPolicySource

    blog_name = resolve_blog_name("policy")
    is_bridge = os.getenv("TISTORY_PUBLISHER", "web").strip().lower() == "bridge"

    if feeds is None:
        env_feeds = os.getenv("POLICY_FEEDS", "").strip()
        feeds = [f.strip() for f in env_feeds.split(",") if f.strip()] or DEFAULT_FEEDS

    log(f"정책→티스토리 파이프라인 시작 (blog={blog_name}, feeds={feeds}, count={count})", "step")

    # 1) RSS 수집 (피드들에서 골고루 모아 섞음)
    source = KoreaPolicySource()
    pool: list = []
    for fname in feeds:
        method = _FEED_FETCHERS.get(fname)
        if not method or not hasattr(source, method):
            log(f"피드 매핑 없음: {fname} — 건너뜀", "warn")
            continue
        try:
            pool.extend(getattr(source, method)(count=10))
        except Exception as e:
            log(f"RSS '{fname}' 수집 예외: {e}", "warn")

    if not pool:
        log("RSS 수집 결과 없음. 종료.", "error")
        from common.notifier import notify_pipeline_result
        notify_pipeline_result("정책→티스토리", 0, count, details="RSS 수집 실패")
        return

    # 2) 중복 제거 + 이미 발행한 URL 제외
    posted = _load_posted()
    seen: set = set()
    candidates: list = []
    for item in pool:
        url = item.get("link", "")
        if not url or url in posted or url in seen:
            continue
        seen.add(url)
        candidates.append(item)

    if not candidates:
        log("신규 발행 대상 없음(모두 이미 발행됨).", "warn")
        from common.notifier import notify_pipeline_result
        notify_pipeline_result("정책→티스토리", 0, count, details="신규 글 없음", reason="empty")
        return

    random.shuffle(candidates)
    targets = candidates[:count]

    # 3) 추천 상품을 publisher 로그인 전에 미리 수집
    # (티스토리 publisher 가 sync_playwright 켠 상태에서 쿠팡 source 가 또
    # sync_playwright 호출하면 'inside asyncio loop' 충돌)
    channel_id = (
        os.getenv("COUPANG_CHANNEL_ID_POLICY")
        or os.getenv("COUPANG_CHANNEL_ID", "")
    )
    products: list[dict | None] = []
    for _ in targets:
        products.append(fetch_recommend_product(
            GENERIC_DEFAULT_KEYWORDS, channel_id=channel_id,
        ))

    # 4) 티스토리 publisher (web=Playwright / bridge=큐) 로그인
    pub = make_publisher(blog_name)
    if not pub.login():
        log(f"티스토리 로그인 실패 (blog={blog_name})", "error")
        from common.notifier import notify_pipeline_result
        notify_pipeline_result("정책→티스토리", 0, count, details="로그인 실패")
        _close(pub)
        return

    # 5) 발행
    published = 0
    last_url = ""
    try:
        for item, product in zip(targets, products):
            title, body, tags = _build_html(item)
            if not title:
                continue

            content = body + render_product_card(product)

            result = pub.post(
                title=title,
                content=content,
                tags=tags,
                image_url=item.get("image", ""),
                category=os.getenv("TISTORY_CATEGORY", ""),
            )
            if result.success:
                published += 1
                posted.add(item["link"])
                log(f"[{published}/{len(targets)}] {'큐 등록' if is_bridge else '발행 완료'}: {result.url or result.message}", "ok")
                _save_posted(posted)
                if result.url:
                    last_url = result.url
                    from common.publish_queue import add_url as _add_url
                    _add_url(result.url, platform="tistory", title=title)
            else:
                log(f"발행 실패: {result.message}", "error")

            time.sleep(random.uniform(10, 20))
    finally:
        _close(pub)

    verb = "큐 등록" if is_bridge else "발행"
    log(f"정책→티스토리 완료: {published}/{len(targets)}건 {verb}", "step")

    # bridge 모드 + 성공 → 파이프라인 알림 skip. 실제 발행 완료 텔레그램 알림은
    # bridge server 가 /done 처리 시 보낸다 (false positive "발행 성공" 방지).
    if is_bridge and published > 0:
        log("정책→티스토리 bridge 모드 — 파이프라인 알림 skip", "info")
        return

    from common.notifier import notify_pipeline_result
    notify_pipeline_result("정책→티스토리", published, len(targets), url=last_url)


def _close(pub) -> None:
    close = getattr(pub, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            pass


if __name__ == "__main__":
    post_count = int(os.getenv("POLICY_POST_COUNT", os.getenv("POST_COUNT", "1")))
    if "--count" in sys.argv:
        idx = sys.argv.index("--count")
        if idx + 1 < len(sys.argv):
            post_count = int(sys.argv[idx + 1])
    run(count=post_count)
