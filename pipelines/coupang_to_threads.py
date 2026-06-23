"""
파이프라인: 쿠팡 상품 1건 → Threads (Meta Graph API) 발행

발행 모드 (THREADS_MODE 환경변수 / --mode 인자):
    single (기본) — 단일 게시물 한 번에 발행 (간결, 가독성 우선)
    chain         — 후킹 → 디테일(reply) → 링크+CTA(reply) 3개 reply chain

- 채널 ID:  COUPANG_CHANNEL_ID_THREADS > COUPANG_CHANNEL_ID 폴백
- 톤:       반말 SNS 스타일 (generate_threads_chain / _caption 참고)
- 글자 수:  편당 150자 (Threads above-the-fold 노출 최적)
- 해시태그: 사용 안 함 (Threads 톤상 본문에 자연스럽게 녹이는 게 적합)
- 링크:     마지막 reply 본문에 어필리에이트 단축링크 자동 추가
- 의무 고지: 마지막 reply 끝에 '※ 쿠팡 파트너스 활동으로 수수료 받을 수 있음' 자동 삽입

실행:
    python -m pipelines.coupang_to_threads                       # chain (기본)
    python -m pipelines.coupang_to_threads --mode single
    python -m pipelines.coupang_to_threads --keyword 무선이어폰
"""
from __future__ import annotations

import os
import sys
import time

from dotenv import load_dotenv
load_dotenv()

from common.ai_intro import (
    generate_threads_caption,
    generate_threads_chain,
)
from common.logger import log
from common.url_shortener import shorten as shorten_url
from publishers.threads import ThreadsPublisher
from sources.coupang import CoupangSource

from pipelines.coupang_to_wordpress import get_keywords


SCHEDULE = {
    "env":  "SCHEDULE_COUPANG_THREADS",
    "func": "run",
}


def _shorten(url: str) -> str:
    """url_shortener 호출 + 실패 시 원본 반환."""
    if not url:
        return ""
    try:
        s = shorten_url(url)
        return s or url
    except Exception as e:
        log(f"단축 실패 (원본 사용): {e}", "warn")
        return url


def _publish_single(pub: ThreadsPublisher, kw: str, product: dict,
                    short_link: str) -> "object":
    """단일 게시물 발행 (기본 모드).

    한 게시물에 후킹 + 디테일 + 마무리 모두 담기므로 chain 보다 글자 수
    여유 (~230). 링크와 의무 고지는 publisher 직전에 부착.
    """
    caption = generate_threads_caption(kw, product, short_url=short_link, max_chars=230)
    if not caption:
        caption = (product.get("name", "") or "")[:60]

    body = caption
    if short_link:
        body = f"{caption}\n\n👉 {short_link}"
    # 공정거래위 의무 고지 (단축형)
    body = f"{body}\n\n※ 쿠팡 파트너스 활동으로 수수료 받을 수 있음"

    # 해시태그 제외 (Threads 톤상 본문에 자연스럽게 녹이는 게 더 적합).
    return pub.post(
        title="", content=body, tags=[],
        image_url=product.get("image", "") or "",
    )


def _publish_chain(pub: ThreadsPublisher, kw: str, product: dict,
                    short_link: str) -> "object":
    """3편 reply chain 발행 — Threads 알고리즘 우호 패턴."""
    chain_parts = generate_threads_chain(
        kw, product, short_url=short_link, max_chars_each=150)

    if not chain_parts or len(chain_parts) < 2:
        log("AI chain 생성 실패 — single 모드로 폴백", "warn")
        return _publish_single(pub, kw, product, short_link)

    # 최대 3편까지만 사용
    chain_parts = chain_parts[:3]

    # 마지막 편에 링크 + 의무 고지 부착 (해시태그는 Threads 톤상 제외)
    if short_link:
        chain_parts[-1] = f"{chain_parts[-1]}\n\n👉 {short_link}"

    # 공정거래위 의무 고지 (단축형) — 어필리에이트 활동 명시는 필수
    chain_parts[-1] = f"{chain_parts[-1]}\n\n※ 쿠팡 파트너스 활동으로 수수료 받을 수 있음"

    # 1편: post() — 이미지는 첫 번째 게시물에만 부착
    log(f"[chain 1/{len(chain_parts)}] 후킹 게시물 발행", "step")
    first = pub.post(
        title="", content=chain_parts[0],
        tags=[],  # 1편엔 해시태그 안 넣음 (마지막에만)
        image_url=product.get("image", "") or "",
    )
    if not first.success or not first.post_id:
        return first

    # 트위터 스타일 진짜 thread chain: 1 ← 2 ← 3 (각 reply 가 직전 게시물의 답글)
    # 평면 구조 (모두 1편 자식) 보다 가독성 좋고 사용자가 답글이 중복 표시되어
    # 보이는 문제 해소.
    parent_id = first.post_id
    last_result = first

    for idx, body in enumerate(chain_parts[1:], start=2):
        time.sleep(3)  # API 권장 인터벌
        log(f"[chain {idx}/{len(chain_parts)}] reply 발행 (parent={parent_id[:12]}...)", "step")
        reply = pub.post_reply(parent_id, body)
        if reply.success and reply.post_id:
            last_result = reply
            parent_id = reply.post_id   # 다음 reply 는 이 reply 의 답글로
        else:
            log(f"reply {idx} 실패 (계속): {reply.message}", "warn")
            # 실패 시 parent_id 유지 — 다음 reply 는 마지막 성공한 게시물에 매달림

    from publishers.base import PostResult
    return PostResult(
        success=True,
        url=first.url,         # 진입점 = 첫 게시물 (스레드 시작점)
        post_id=first.post_id,
        message=f"chain {len(chain_parts)}편 발행",
    )


def run(keyword: "str | None" = None, mode: "str | None" = None) -> None:
    """쿠팡 1건 크롤링 → Threads 발행."""
    from sources.itemscout_keywords import get_pool_status, mark_keywords_used

    mode = (mode or os.getenv("THREADS_MODE", "single")).lower()
    log(f"[쿠팡→Threads] 시작 (mode={mode})", "step")

    # 1) 키워드
    if keyword:
        kw = keyword
        log(f"단일 키워드 모드: {kw}", "info")
    else:
        log(get_pool_status(), "info")
        kws = get_keywords(n=1)
        if not kws:
            log("키워드 추출 실패", "error")
            return
        kw = kws[0]

    # 2) 쿠팡 상품 1개
    channel_id = (os.getenv("COUPANG_CHANNEL_ID_THREADS", "")
                  or os.getenv("COUPANG_CHANNEL_ID", ""))
    source = CoupangSource(channel_id=channel_id)
    products = source.search(kw, count=1)
    if not products:
        log(f"'{kw}' 상품 수집 실패", "warn")
        from common.notifier import notify_pipeline_result
        notify_pipeline_result("쿠팡→Threads", 0, 1, details=f"수집 실패 ({kw})")
        return
    product = products[0]
    # 골드박스 등으로 상품 테마가 키워드와 다를 수 있다 → 상품의 테마를 우선 사용
    # (검색 상품은 product["keyword"]==kw 라 동일, 골드박스는 categoryName).
    theme = product.get("keyword") or kw
    is_goldbox = product.get("source_mode") == "goldbox"

    # 3) 어필리에이트 단축링크
    aff_url = product.get("affiliate_url", "") or product.get("url", "") or ""
    short_link = _shorten(aff_url) if aff_url else ""

    # 4) Threads 인증
    pub = ThreadsPublisher()
    if not pub.login():
        log("Threads API 인증 실패 — .env 의 토큰 확인", "error")
        from common.notifier import notify_pipeline_result
        notify_pipeline_result("쿠팡→Threads", 0, 1, details="인증 실패")
        return

    # 5) 모드별 발행 (테마는 상품 기준 — 골드박스면 categoryName)
    if mode == "single":
        result = _publish_single(pub, theme, product, short_link)
    else:
        result = _publish_chain(pub, theme, product, short_link)

    if result.success:
        log(f"발행 완료: {result.url}", "ok")
        # 골드박스는 풀 키워드를 소비하지 않았으므로 used 기록 skip.
        if not keyword and not is_goldbox:
            try:
                mark_keywords_used([kw])
            except Exception as e:
                log(f"키워드 기록 실패 ({e})", "warn")
        if result.url:
            try:
                from common.publish_queue import add_url as _add_url
                _add_url(
                    result.url, platform="threads", title=kw,
                    keyword=kw, source="coupang",
                    affiliate_url=short_link or aff_url,
                )
            except Exception:
                pass

        from common.notifier import notify_pipeline_result
        notify_pipeline_result(
            "쿠팡→Threads", 1, 1,
            details=f"키워드: {kw} (mode={mode})",
            url=result.url or "",
        )
    else:
        log(f"발행 실패: {result.message}", "error")
        from common.notifier import notify_pipeline_result
        notify_pipeline_result("쿠팡→Threads", 0, 1,
                               details=str(result.message)[:200])


if __name__ == "__main__":
    forced_keyword: "str | None" = None
    forced_mode: "str | None" = None
    if "--keyword" in sys.argv:
        idx = sys.argv.index("--keyword")
        if idx + 1 < len(sys.argv):
            forced_keyword = sys.argv[idx + 1]
    if "--mode" in sys.argv:
        idx = sys.argv.index("--mode")
        if idx + 1 < len(sys.argv):
            forced_mode = sys.argv[idx + 1]
    run(keyword=forced_keyword, mode=forced_mode)
