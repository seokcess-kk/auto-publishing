"""
Instagram 카드 이미지 발행 공통 헬퍼.

`*_to_image` 파이프라인들이 카드 이미지를 만든 뒤 InstagramPublisher 로
실제 발행하는 코드를 한 군데로 모았다. dryrun(env), 로그인 실패 시 알림,
브라우저 정리, notify_pipeline_result 까지 표준화.

사용 예:
    from common.instagram_publish import publish_card

    publish_card(
        pipeline_name="명언→Instagram",
        image_path=path,
        caption=caption,
        hashtags=tags,
        dryrun_env="QUOTE_DRYRUN",
        details_summary="오늘의 명언",
    )
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from common.logger import log
from common.notifier import notify_pipeline_result


def publish_card(
    *,
    pipeline_name: str,
    image_path,
    caption: str = "",
    hashtags: Optional[list[str]] = None,
    dryrun_env: Optional[str] = None,
    details_summary: str = "",
) -> bool:
    """카드 이미지(들)를 Instagram 에 발행하고 알림까지 보낸다.

    Args:
        pipeline_name:   알림용 이름 (예: '명언→Instagram')
        image_path:      발행할 png 절대경로 — str/Path 또는 list[str|Path] (캐러셀)
        caption:         캡션 본문 (해시태그 제외)
        hashtags:        '#' 없는 태그 리스트
        dryrun_env:      이 env 가 '1' 이면 발행 생략 (테스트 용)
        details_summary: 알림에 포함할 짧은 요약

    Returns:
        실제 발행 성공 시 True. dryrun / 실패 시 False.
    """
    # str / Path / list 모두 정규화
    if isinstance(image_path, (list, tuple)):
        media = [str(p) for p in image_path]
        primary_path = media[0] if media else ""
    else:
        primary_path = str(image_path)
        media = primary_path
    hashtags = hashtags or []

    if dryrun_env and os.getenv(dryrun_env, "0") == "1":
        count_str = (f" ({len(media)}장)" if isinstance(media, list) else "")
        log(f"[DRYRUN] {pipeline_name} 발행 생략 — 카드만 저장됨{count_str}", "warn")
        notify_pipeline_result(
            pipeline_name, 1, 1,
            details=f"DRYRUN · {details_summary} · {primary_path}",
        )
        return False

    from publishers.instagram import InstagramPublisher
    pub = InstagramPublisher()

    if not pub.login():
        log(f"{pipeline_name} Instagram 로그인 실패", "error")
        try:
            pub.close()
        except Exception:
            pass
        notify_pipeline_result(
            pipeline_name, 0, 1,
            details=f"로그인 실패 · 카드는 저장됨: {image_path}",
        )
        return False

    try:
        result = pub.post(
            title="",
            content=caption,
            tags=hashtags,
            media_type="image",
            media_path=media,
        )
    finally:
        try:
            pub.close()
        except Exception:
            pass

    if result.success:
        notify_pipeline_result(
            pipeline_name, 1, 1,
            details=details_summary or "발행 완료",
        )
        log(f"{pipeline_name} 발행 완료", "ok")
        return True

    notify_pipeline_result(
        pipeline_name, 0, 1,
        details=f"발행 실패: {result.message}",
    )
    log(f"{pipeline_name} 발행 실패: {result.message}", "error")
    return False
