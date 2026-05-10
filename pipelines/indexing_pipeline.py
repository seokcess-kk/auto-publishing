"""
파이프라인: 색인 등록 (Google + Naver)

publish_queue.json 의 미색인 URL 을 Google Indexing API + Naver Search Advisor 에 제출.
백링크는 별도 파이프라인(backlink_pipeline.py)에서 처리.

실행:
    python -m pipelines.indexing_pipeline

환경변수 (.env):
    SCHEDULE_INDEX                 = 22:00
    GOOGLE_INDEXING_SA_JSON        = /path/to/sa.json   (또는 도메인별 SA 키)
    NAVER_SEARCHADVISOR_USERNAME   = ...
    NAVER_SEARCHADVISOR_PASSWORD   = ...
"""
import os

from dotenv import load_dotenv
load_dotenv()

from common.logger import log
from common.publish_queue import get_pending, mark_done_bulk, mark_status_bulk, stats
from common.notifier import notify_pipeline_result


SCHEDULE = {
    "env":  "SCHEDULE_INDEX",
    "func": "run",
}


def _submit_google(pending: list) -> list:
    """Google 색인 제출. 성공한 URL 목록 반환.

    실패 케이스(no_permission/limit/error)는 publish_queue 에 상세 상태로
    저장되어 dashboard 에서 사유 노출.
    """
    if not pending:
        log("[색인] Google 미제출 URL 없음", "info")
        return []
    try:
        from common.indexing_google import submit_urls
        urls = [item["url"] for item in pending[:200]]
        results = submit_urls(urls)
        # 모든 결과를 publish_queue 에 기록 (성공/실패 모두) — 사유 보존
        mark_status_bulk(results, "google_indexed")
        ok = [u for u, s in results.items() if s == "ok"]
        log(f"[색인] Google: {len(ok)}/{len(urls)}건 성공", "step")
        return ok
    except Exception as e:
        log(f"[색인] Google 오류: {e}", "error")
        return []


def _submit_naver(pending: list) -> list:
    """Naver 색인 제출. 성공한 URL 목록 반환."""
    if not pending:
        log("[색인] Naver 미제출 URL 없음", "info")
        return []
    try:
        from common.indexing_naver import submit_urls
        urls = [item["url"] for item in pending[:50]]
        results = submit_urls(urls)
        mark_status_bulk(results, "naver_indexed")
        ok = [u for u, s in results.items() if s == "ok"]
        log(f"[색인] Naver: {len(ok)}/{len(urls)}건 성공", "step")
        return ok
    except Exception as e:
        log(f"[색인] Naver 오류: {e}", "error")
        return []


def run() -> None:
    """색인 등록 파이프라인 1회 실행."""
    log("=== 색인 등록 파이프라인 시작 ===", "step")

    log("=== 1단계: Google 색인 제출 ===", "step")
    google_pending = get_pending("google_indexed")
    google_ok = _submit_google(google_pending)

    log("=== 2단계: Naver 색인 제출 ===", "step")
    naver_pending = get_pending("naver_indexed")
    naver_ok = _submit_naver(naver_pending)

    log(f"큐 통계: {stats()}", "info")

    total_attempted = len(google_pending) + len(naver_pending)
    total_succeeded = len(google_ok) + len(naver_ok)
    reason = "empty" if total_attempted == 0 else "failure"
    notify_pipeline_result(
        "색인 등록",
        total_succeeded,
        total_attempted,
        details=(
            f"Google {len(google_ok)}/{len(google_pending)} · "
            f"Naver {len(naver_ok)}/{len(naver_pending)}"
        ),
        reason=reason,
    )
    log("=== 색인 등록 파이프라인 완료 ===", "step")


if __name__ == "__main__":
    run()
