"""티스토리 발행기 — Chrome Extension 브릿지 모드.

publishers/tistory.py 가 DKAPTCHA 로 막힌 상태에서 사용자의 평소 Chrome 에
extension 을 설치해 우회. 본 publisher 는 queue 에 push 만 하고 extension 이
실제 발행을 담당.

동기/비동기:
  TISTORY_BRIDGE_WAIT_SEC (기본 600 = 10분) 동안 polling 해서 done/failed
  결과를 기다린다. 0 으로 두면 즉시 'queued' success 반환 (fire-and-forget).

스케줄러가 매 슬롯마다 짧게 실행되므로 fire-and-forget 이 안전 (extension 처리
시간이 사람 캡차 풀이 때문에 가변). 단, publish_queue.json 의 ROI 기록은
bridge server 가 /done 받을 때 직접 갱신하므로 fire-and-forget 이어도 누락 없음.
"""
from __future__ import annotations

import os
from typing import Optional

from common.logger import log
from common.tistory_queue import enqueue, wait_done
from .base import Publisher, PostResult


class TistoryBridgePublisher(Publisher):
    """평소 Chrome + extension 으로 발행하는 publisher.

    post() 는 큐에 enqueue 만 하므로 외부 인터넷/플레이라이트 의존 없음.
    """

    def __init__(self, blog_name: str):
        self.blog_name = blog_name
        self.blog_url = f"https://{blog_name}.tistory.com"

    def login(self) -> bool:
        """브릿지 publisher 는 로그인 개념 없음 — extension 이 평소 Chrome 세션 사용."""
        return True

    def post(self, title: str, content: str,
             tags: Optional[list[str]] = None, category: str = "",
             image_url: str = "", **kwargs) -> PostResult:
        # 이미지: extension 은 Tistory editor 내 첨부 API 직접 호출 어렵고
        # DKAPTCHA 영향 없이 가능한 image_url 그대로 <img src> 로 prepend.
        image_html = f'<p><img src="{image_url}" alt=""></p>\n' if image_url else ""

        visibility = int(kwargs.get("visibility", 20))

        item_id = enqueue(
            blog_name=self.blog_name,
            title=title,
            content=content,
            tags=tags or [],
            category=category,
            visibility=visibility,
            image_url=image_url,
            image_html=image_html,
            source=str(kwargs.get("source", "")),
            keyword=str(kwargs.get("keyword", "")),
            affiliate_url=str(kwargs.get("affiliate_url", "")),
        )
        log(f"[tistory_bridge:{self.blog_name}] 큐 등록: {title[:40]} id={item_id[:8]}", "step")

        wait_sec = int(os.getenv("TISTORY_BRIDGE_WAIT_SEC", "0"))
        if wait_sec <= 0:
            # fire-and-forget — extension 처리 결과는 bridge server 가
            # publish_queue.json 에 직접 push (color 통해 색인/백링크 동작)
            return PostResult(
                success=True,
                url="",
                post_id=item_id,
                message=f"queued (id={item_id[:8]}, extension 처리 대기)",
            )

        # 동기 대기 모드 — 사람 캡차 풀이 시간 고려해 wait_sec 큼직하게
        log(f"[tistory_bridge] {wait_sec}s 동안 결과 polling...", "info")
        result = wait_done(item_id, timeout_sec=wait_sec, poll_sec=5.0)
        if result is None:
            return PostResult(
                success=False,
                post_id=item_id,
                message=f"timeout ({wait_sec}s) — extension 처리 미완 (큐에 남음)",
            )
        if result.get("status") == "done":
            url = result.get("result_url", "") or ""
            return PostResult(
                success=True,
                url=url,
                post_id=str(result.get("result_post_id", "") or item_id),
            )
        return PostResult(
            success=False,
            post_id=item_id,
            message=f"failed: {result.get('error', '')[:200]}",
        )

    def close(self) -> None:
        """브릿지 publisher 는 자원 보유 안 함."""
        pass
