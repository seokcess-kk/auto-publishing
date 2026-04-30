"""
파이프라인 공통 실행 골격 (base_runner)

표준 fetch → publish → notify 루프를 캡슐화한다.
단순한 1:1 소스→퍼블리셔 파이프라인은 run_pipeline() 한 줄로 대체 가능.

사용 예::

    from pipelines._kernel.base_runner import run_pipeline

    def run():
        run_pipeline(
            pipeline_name="쿠팡→WordPress",
            fetch_fn=lambda: CoupangSource().fetch(count=3),
            publish_fn=lambda item: WordPressPublisher().post(
                title=item["name"],
                content=item["html"],
            ),
            count=3,
        )
"""
import random
import time
from typing import Callable, Optional

from common.logger import log
from common.notifier import notify_pipeline_result
from publishers.base import PostResult


def run_pipeline(
    pipeline_name: str,
    fetch_fn: Callable[[], list],
    publish_fn: Callable[[dict], PostResult],
    *,
    count: int = 1,
    sleep_range: tuple[float, float] = (3.0, 10.0),
    on_success: Optional[Callable[[PostResult, dict], None]] = None,
    on_failure: Optional[Callable[[PostResult, dict], None]] = None,
) -> int:
    """표준 fetch → publish → notify 루프.

    Args:
        pipeline_name: 알림·로그에 쓸 파이프라인 이름.
        fetch_fn:      아이템 목록을 반환하는 함수 (인자 없음).
        publish_fn:    아이템 dict를 받아 PostResult를 반환하는 함수.
        count:         목표 발행 수 (알림 메시지에 사용).
        sleep_range:   아이템 간 대기 범위(초). 기본 3~10초.
        on_success:    발행 성공 시 추가 콜백 (PostResult, item).
        on_failure:    발행 실패 시 추가 콜백 (PostResult, item).

    Returns:
        실제 발행 성공 수.
    """
    log(f"[{pipeline_name}] 시작", "step")

    items = fetch_fn()
    if not items:
        log(f"[{pipeline_name}] 수집된 아이템 없음", "warn")
        notify_pipeline_result(pipeline_name, 0, count, details="수집 실패")
        return 0

    published = 0
    for idx, item in enumerate(items[:count], start=1):
        try:
            result = publish_fn(item)
        except Exception as exc:
            log(f"[{pipeline_name}] {idx}번째 발행 예외: {exc}", "error")
            result = PostResult(success=False, message=str(exc))

        if result.success:
            published += 1
            log(f"[{pipeline_name}] [{published}/{count}] 발행 완료: {result.url}", "ok")
            if on_success:
                on_success(result, item)
        else:
            log(f"[{pipeline_name}] 발행 실패: {result.message}", "error")
            if on_failure:
                on_failure(result, item)

        if idx < min(len(items), count):
            time.sleep(random.uniform(*sleep_range))

    log(f"[{pipeline_name}] 완료: {published}/{count}건", "step")
    notify_pipeline_result(pipeline_name, published, count)
    return published
