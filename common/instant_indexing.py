"""
발행 직후 즉시 색인 — bridge /done 훅용 fire-and-forget 제출기.

야간 22:00 indexing_pipeline 은 PC 가동 공백으로 자주 누락되어(2026-07 실측
1주+ 미실행) 신규 글이 검색에 노출되기까지 하루 이상 걸렸다. 발행 완료가
확정되는 지점(bridge /done)에서 바로 제출하면 색인 지연이 분 단위로 줄고,
실패해도 publish_queue 필드가 "X"로 남아 야간 파이프라인 백스톱이 수습한다.

스레드 모델: 단일 daemon worker 가 queue 를 직렬 소비한다.
- /done HTTP 핸들러는 put 만 하고 즉시 반환 — extension 응답 지연 없음.
- Naver 제출은 Playwright(서치어드바이저 로그인) 경유라 비-메인 스레드에서
  돌아야 하고, 동시 실행 시 `.sessions` 프로필 잠금이 충돌한다 — 단일
  worker 직렬화가 두 문제를 함께 해결한다. 22:00 indexing_pipeline
  subprocess 와의 경합만 남는데, 그쪽은 예외 → skip → 백스톱으로 흡수.

환경변수:
    INSTANT_INDEXING     = true   전체 on/off
    INSTANT_INDEX_NAVER  = true   Naver 제출만 off 가능 (Playwright 문제 시 강등)
"""
import os
import queue
import threading

from common.logger import log

_QUEUE: "queue.Queue[str]" = queue.Queue()
_WORKER_LOCK = threading.Lock()
_worker_started = False


def _flag(name: str, default: str = "true") -> bool:
    return os.getenv(name, default).strip().lower() in ("1", "true", "yes")


def submit_async(url: str) -> None:
    """URL 을 즉시 색인 큐에 넣고 바로 반환. worker 는 첫 호출 시 lazy 기동."""
    if not url or not _flag("INSTANT_INDEXING"):
        return
    _ensure_worker()
    _QUEUE.put(url)


def _ensure_worker() -> None:
    global _worker_started
    with _WORKER_LOCK:
        if _worker_started:
            return
        threading.Thread(target=_worker_loop, daemon=True,
                         name="instant-indexing").start()
        _worker_started = True


def _worker_loop() -> None:
    while True:
        url = _QUEUE.get()
        try:
            _submit_one(url)
        except Exception as e:
            log(f"[즉시색인] 처리 오류 (무시 — 야간 indexing_pipeline 이 수습): {e}",
                "warn")
        finally:
            _QUEUE.task_done()


def _submit_one(url: str) -> None:
    from common.publish_queue import mark_status_bulk

    try:
        from common.indexing_google import submit_urls as _google_submit
        results = _google_submit([url])
        mark_status_bulk(results, "google_indexed")
        status = results.get(url, "?")
        log(f"[즉시색인] Google {status}: {url}",
            "ok" if status == "ok" else "warn")
    except Exception as e:
        log(f"[즉시색인] Google 오류 (무시): {e}", "warn")

    if not _flag("INSTANT_INDEX_NAVER"):
        return
    try:
        from common.indexing_naver import submit_urls as _naver_submit
        results = _naver_submit([url])
        mark_status_bulk(results, "naver_indexed")
        status = results.get(url, "?")
        log(f"[즉시색인] Naver {status}: {url}",
            "ok" if status == "ok" else "warn")
    except Exception as e:
        log(f"[즉시색인] Naver 오류 (무시): {e}", "warn")
