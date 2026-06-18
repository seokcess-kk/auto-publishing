"""
파이프라인: 알리익스프레스 상품 1건 → Threads (Meta Graph API) 발행

발행 모드 (THREADS_MODE 환경변수 / --mode 인자):
    single (기본) — 단일 게시물 한 번에 발행 (간결, 가독성 우선)
    chain         — 후킹 → 디테일(reply) → 링크+CTA(reply) 3개 reply chain

- 어필리에이트: AliexpressSource 의 affiliate_url 사용 (Playwright 기반)
- 톤:       반말 SNS 스타일 (generate_threads_chain / _caption 참고)
- 글자 수:  편당 150자 (Threads above-the-fold 노출 최적)
- 해시태그: 사용 안 함 (Threads 톤상 본문에 자연스럽게 녹이는 게 적합)
- 링크:     마지막 게시물 본문에 어필리에이트 단축링크 자동 추가
- 의무 고지: 마지막 게시물 끝에 '※ 알리익스프레스 파트너스 활동으로 수수료 받을 수 있음' 자동 삽입

실행:
    python -m pipelines.aliexpress_to_threads                       # single (기본)
    python -m pipelines.aliexpress_to_threads --mode chain
    python -m pipelines.aliexpress_to_threads --keyword 무선이어폰
"""
from __future__ import annotations

import os
import sys
import threading
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
from sources.aliexpress import AliexpressSource

from pipelines.coupang_to_wordpress import get_keywords  # 폴백용

# 알리에 매칭 잘 되는 카테고리만 — 식품/여가생활편의/도서/면세점/기타 제외.
# (예: '경주월드' = 여가생활편의 → 알리 매칭 0, '다이소' = 기타 등은 제외)
ALIEXPRESS_CATEGORIES = [
    "패션의류", "패션잡화", "화장품/미용", "디지털/가전",
    "가구/인테리어", "출산/육아", "스포츠/레저", "생활/건강",
]


def get_ali_keywords(n: int = 1) -> list:
    """알리 적합 카테고리만 화이트리스트로 필터한 키워드 풀에서 추출."""
    from sources.itemscout_keywords import get_next_keywords
    try:
        kws = get_next_keywords(n=n, refill_threshold=50, categories=ALIEXPRESS_CATEGORIES)
        if kws:
            return kws
    except Exception as e:
        log(f"ItemScout 알리 필터 키워드 실패 ({e}), fallback 전체 풀", "warn")
    # 폴백 — 전체 풀에서
    return get_keywords(n=n)


SCHEDULE = {
    "env":  "SCHEDULE_ALIEXPRESS_THREADS",
    "func": "run",
}


_DISCLOSURE = "※ 알리익스프레스 파트너스 활동으로 수수료 받을 수 있음"


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
    """단일 게시물 발행 (기본 모드)."""
    caption = generate_threads_caption(kw, product, short_url=short_link, max_chars=230)
    if not caption:
        caption = (product.get("name", "") or "")[:60]

    body = caption
    if short_link:
        body = f"{caption}\n\n👉 {short_link}"
    body = f"{body}\n\n{_DISCLOSURE}"

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

    chain_parts = chain_parts[:3]

    if short_link:
        chain_parts[-1] = f"{chain_parts[-1]}\n\n👉 {short_link}"
    chain_parts[-1] = f"{chain_parts[-1]}\n\n{_DISCLOSURE}"

    log(f"[chain 1/{len(chain_parts)}] 후킹 게시물 발행", "step")
    first = pub.post(
        title="", content=chain_parts[0],
        tags=[],
        image_url=product.get("image", "") or "",
    )
    if not first.success or not first.post_id:
        return first

    parent_id = first.post_id
    last_result = first

    for idx, body in enumerate(chain_parts[1:], start=2):
        time.sleep(3)
        log(f"[chain {idx}/{len(chain_parts)}] reply 발행 (parent={parent_id[:12]}...)", "step")
        reply = pub.post_reply(parent_id, body)
        if reply.success and reply.post_id:
            last_result = reply
            parent_id = reply.post_id
        else:
            log(f"reply {idx} 실패 (계속): {reply.message}", "warn")

    from publishers.base import PostResult
    return PostResult(
        success=True,
        url=first.url,
        post_id=first.post_id,
        message=f"chain {len(chain_parts)}편 발행",
    )


def _kill_child_chromiums() -> None:
    """현재 프로세스의 자식 프로세스 트리(Playwright node driver→Chromium)를 정리.

    알리는 persistent profile 이 아니라 비-persistent launch(고유 temp dir)라
    common.browser_profile 의 profile-name 매칭 orphan 청소가 잡지 못한다.
    검색 timeout 으로 워커 스레드를 버릴 때 직접 자식 트리를 끊어 Chromium
    좀비를 막는다. (스케줄러가 파이프라인을 직렬 실행하므로 이 시점의
    ms-playwright Chromium 은 이 실행분뿐 — 광범위 종료 위험 없음.)
    """
    if sys.platform != "win32":
        return
    try:
        import subprocess as _sp
        # 자식들(node driver 등)을 /T 로 트리째 종료. 이 명령을 수행하는
        # powershell 자신($PID)은 제외해 명령이 중간에 죽지 않도록 한다.
        ps_cmd = (
            f"$pp={os.getpid()}; "
            "Get-CimInstance Win32_Process | "
            "Where-Object { $_.ParentProcessId -eq $pp -and $_.ProcessId -ne $PID } | "
            "ForEach-Object { taskkill /F /T /PID $_.ProcessId 2>$null }"
        )
        _sp.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_cmd],
                capture_output=True, timeout=15)
    except Exception as e:
        log(f"자식 Chromium 정리 실패 (무시): {e}", "warn")


def _search_with_timeout(source, keyword: str, *, count: int,
                         require_affiliate: bool, timeout_sec: int) -> list:
    """source.search() 를 벽시계 상한 내에서 실행. 초과 시 브라우저 강제 정리 후 [].

    알리 검색/브라우저 launch 가 드물게 무한 대기에 빠져(06-16 1800s timeout)
    스케줄러의 무딘 30분 timeout 까지 메인 루프를 잡아먹는 일을 차단한다.
    Windows 엔 SIGALRM 이 없어 데몬 스레드 + join(timeout) 으로 구현한다.
    Playwright sync API 는 스레드 친화적이라 search 와 close 를 같은 워커
    스레드에서 수행한다. 초과 시 워커는 버려지고(daemon), 남은 Chromium 은
    명시적으로 정리한다.
    """
    box: dict = {"products": [], "error": None}

    def _worker() -> None:
        try:
            box["products"] = source.search(
                keyword, count=count, require_affiliate=require_affiliate)
        except Exception as e:  # noqa: BLE001
            box["error"] = e
        finally:
            try:
                source.close()
            except Exception:
                pass

    t = threading.Thread(target=_worker, name="ali-search", daemon=True)
    t.start()
    t.join(timeout_sec)
    if t.is_alive():
        log(f"알리 검색/브라우저가 {timeout_sec}s 초과 — 자식 Chromium 정리 후 중단", "error")
        _kill_child_chromiums()
        return []
    if box["error"] is not None:
        raise box["error"]
    return box["products"] or []


def run(keyword: "str | None" = None, mode: "str | None" = None) -> None:
    """알리 1건 크롤링 → Threads 발행."""
    from sources.itemscout_keywords import get_pool_status, mark_keywords_used

    mode = (mode or os.getenv("THREADS_MODE", "single")).lower()
    log(f"[알리→Threads] 시작 (mode={mode})", "step")

    # 1) 키워드 — 알리 적합 카테고리 화이트리스트 사용
    if keyword:
        kw = keyword
        log(f"단일 키워드 모드: {kw}", "info")
    else:
        log(get_pool_status(), "info")
        kws = get_ali_keywords(n=1)
        if not kws:
            log("키워드 추출 실패", "error")
            return
        kw = kws[0]

    # 2) 알리 상품 1개 — sync_playwright 직렬화 위해 publisher 호출 전에 close.
    #    무한 대기(06-16 1800s timeout) 방지 위해 벽시계 상한 가드로 감싼다.
    #    search 내부에서 close 까지 수행하므로 여기선 별도 close 불필요.
    tracking_id = os.getenv("ALIEXPRESS_TRACKING_ID", "wordpress")
    try:
        search_timeout = int(os.getenv("ALIEXPRESS_SEARCH_TIMEOUT_SEC", "240"))
    except ValueError:
        search_timeout = 240
    source = AliexpressSource(tracking_id=tracking_id)
    products = _search_with_timeout(
        source, kw, count=1, require_affiliate=True, timeout_sec=search_timeout)

    if not products:
        log(f"'{kw}' 상품/링크 수집 실패 또는 키워드 매칭 부족", "warn")
        # 강제 키워드가 아니면 풀에서 점진 제외 — 알리 부적합 키워드 누적 방지
        if not keyword:
            try:
                mark_keywords_used([kw])
                log(f"풀 제외: {kw}", "info")
            except Exception as e:
                log(f"키워드 풀 제외 실패 ({e})", "warn")
        from common.notifier import notify_pipeline_result
        notify_pipeline_result("알리→Threads", 0, 1, details=f"수집 실패 ({kw})")
        return
    product = products[0]

    # 3) 어필리에이트 단축링크
    aff_url = product.get("affiliate_url", "") or product.get("url", "") or ""
    short_link = _shorten(aff_url) if aff_url else ""

    # 4) Threads 인증
    pub = ThreadsPublisher()
    if not pub.login():
        log("Threads API 인증 실패 — .env 의 토큰 확인", "error")
        from common.notifier import notify_pipeline_result
        notify_pipeline_result("알리→Threads", 0, 1, details="인증 실패")
        return

    # 5) 모드별 발행
    if mode == "single":
        result = _publish_single(pub, kw, product, short_link)
    else:
        result = _publish_chain(pub, kw, product, short_link)

    if result.success:
        log(f"발행 완료: {result.url}", "ok")
        if not keyword:
            try:
                mark_keywords_used([kw])
            except Exception as e:
                log(f"키워드 기록 실패 ({e})", "warn")
        if result.url:
            try:
                from common.publish_queue import add_url as _add_url
                _add_url(
                    result.url, platform="threads", title=kw,
                    keyword=kw, source="aliexpress",
                    affiliate_url=short_link or aff_url,
                )
            except Exception:
                pass

        from common.notifier import notify_pipeline_result
        notify_pipeline_result(
            "알리→Threads", 1, 1,
            details=f"키워드: {kw} (mode={mode})",
            url=result.url or "",
        )
    else:
        log(f"발행 실패: {result.message}", "error")
        from common.notifier import notify_pipeline_result
        notify_pipeline_result("알리→Threads", 0, 1,
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
