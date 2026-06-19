"""티스토리 브릿지 큐 — Chrome Extension 이 polling 으로 가져가는 발행 대기열.

data/tistory_queue.json 스키마 (list of dict):
    {
        "id":             "uuid4 hex",
        "blog_name":      "kkkseok",
        "title":          "...",
        "content":        "<html>...</html>",
        "tags":           ["..."],
        "category":       "",
        "visibility":     20,           # 0|15|20 (publishers/tistory.py 와 동일)
        "image_url":      "https://...",
        "image_html":     "<img src='...'>",  # extension 이 content 앞에 prepend
        "source":         "coupang",
        "keyword":        "...",
        "affiliate_url":  "...",
        "queued_at":      "2026-05-21T10:30:00",
        "status":         "pending",   # pending | claimed | done | failed
        "claimed_at":     null,
        "result_url":     "",
        "result_post_id": "",
        "error":          ""
    }

retention: 'done' 상태는 7일 후 자동 prune. 'pending'/'failed' 는 보존.
"""
from __future__ import annotations

import json
import os
import threading
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

_BASE_DIR = Path(__file__).resolve().parent.parent
QUEUE_PATH = _BASE_DIR / "data" / "tistory_queue.json"

# 다중 프로세스 (스케줄러 + bridge server + 파이프라인 subprocess) 가 동시에
# 큐 파일을 만지므로, file-level 락 대신 atomic replace + 스레드 락 조합.
_LOCK = threading.Lock()
_RETENTION_DAYS = 7

# 동일 항목이 claim → (미완료) → stale_reset 재pending → 재claim 으로 무한 루프
# 도는 것을 막는 상한. claim 마다 claim_count 를 올리고, 이 횟수를 넘으면 서빙하지
# 않고 영구 'failed' 처리한다. (확장이 캡차 미해결 등으로 완료 못 한 항목이 같은
# 글쓰기를 반복 요청하던 문제 방지.)
try:
    _MAX_CLAIMS = int(os.getenv("TISTORY_MAX_CLAIMS", "3"))
except ValueError:
    _MAX_CLAIMS = 3

# ─── DKAPTCHA 답안 in-memory store ────────────────────────────────────────────
# 캡차 답안은 ephemeral 하므로 파일 영속 불필요. bridge 프로세스 메모리만 사용.
# {item_id: {tg_message_id, sent_at}} — 텔레그램에 보낸 캡차 메시지 매핑
_CAPTCHA_PENDING: dict[str, dict] = {}
# {item_id: answer_text} — 본인이 답글로 보낸 답안 저장 (content.js 가 poll 해서 가져감)
_CAPTCHA_ANSWERS: dict[str, str] = {}
_CAPTCHA_LOCK = threading.Lock()


def set_captcha_pending(item_id: str, tg_message_id: int) -> None:
    """캡차 요청 등록 — Telegram message_id 저장 후 답글 매칭 대기."""
    with _CAPTCHA_LOCK:
        _CAPTCHA_PENDING[item_id] = {
            "tg_message_id": tg_message_id,
            "sent_at": time.time(),
        }


def find_item_by_tg_message_id(tg_msg_id: int) -> Optional[str]:
    """텔레그램 답글의 reply_to_message_id 로 item_id 역참조."""
    with _CAPTCHA_LOCK:
        for iid, info in _CAPTCHA_PENDING.items():
            if info["tg_message_id"] == tg_msg_id:
                return iid
        return None


def set_captcha_answer(item_id: str, answer: str) -> None:
    """본인이 텔레그램 답글로 보낸 답안 저장. pending 에서 제거."""
    with _CAPTCHA_LOCK:
        _CAPTCHA_ANSWERS[item_id] = answer
        _CAPTCHA_PENDING.pop(item_id, None)


def pop_captcha_answer(item_id: str) -> Optional[str]:
    """content.js 가 가져갈 때 한 번만 반환 + 제거 (one-shot)."""
    with _CAPTCHA_LOCK:
        return _CAPTCHA_ANSWERS.pop(item_id, None)


def reset_stale_captcha(stale_minutes: int = 10) -> int:
    """오래 답변 없는 캡차 pending 정리."""
    cutoff = time.time() - stale_minutes * 60
    n = 0
    with _CAPTCHA_LOCK:
        for iid in list(_CAPTCHA_PENDING.keys()):
            if _CAPTCHA_PENDING[iid]["sent_at"] < cutoff:
                _CAPTCHA_PENDING.pop(iid)
                n += 1
    return n


def _load() -> list[dict]:
    if not QUEUE_PATH.exists():
        return []
    try:
        with open(QUEUE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _save(items: list[dict]) -> None:
    QUEUE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = QUEUE_PATH.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)
    os.replace(tmp, QUEUE_PATH)


def _prune(items: list[dict]) -> list[dict]:
    cutoff = (datetime.now() - timedelta(days=_RETENTION_DAYS)).isoformat()
    out = []
    for it in items:
        if it.get("status") == "done" and (it.get("queued_at") or "") < cutoff:
            continue
        out.append(it)
    return out


def enqueue(*, blog_name: str, title: str, content: str,
            tags: Optional[list[str]] = None, category: str = "",
            visibility: int = 20, image_url: str = "", image_html: str = "",
            source: str = "", keyword: str = "", affiliate_url: str = "") -> str:
    """발행 대기열에 항목 추가. id 반환."""
    item_id = uuid.uuid4().hex
    item = {
        "id":             item_id,
        "blog_name":      blog_name,
        "title":          title,
        "content":        content,
        "tags":           tags or [],
        "category":       category,
        "visibility":     visibility,
        "image_url":      image_url,
        "image_html":     image_html,
        "source":         source,
        "keyword":        keyword,
        "affiliate_url":  affiliate_url,
        "queued_at":      datetime.now().isoformat(timespec="seconds"),
        "status":         "pending",
        "claimed_at":     None,
        "claim_count":    0,
        "result_url":     "",
        "result_post_id": "",
        "error":          "",
    }
    with _LOCK:
        items = _prune(_load())
        items.append(item)
        _save(items)
    return item_id


def claim_next() -> Optional[dict]:
    """다음 pending 항목을 'claimed' 로 마킹하고 반환. extension 이 호출.

    claim_count 가 _MAX_CLAIMS 이상인 항목(반복 claim 후에도 발행 미완료)은
    서빙하지 않고 'failed' 로 영구 처리해 무한 루프를 끊는다.
    """
    with _LOCK:
        items = _load()
        dirty = False
        for it in items:
            if it.get("status") != "pending":
                continue
            claims = int(it.get("claim_count", 0) or 0)
            if claims >= _MAX_CLAIMS:
                it["status"] = "failed"
                it["claimed_at"] = None
                it["error"] = f"{_MAX_CLAIMS}회 claim 후 발행 미완료 — 루프 방지로 자동 중단"
                dirty = True
                continue
            it["claim_count"] = claims + 1
            it["status"] = "claimed"
            it["claimed_at"] = datetime.now().isoformat(timespec="seconds")
            _save(items)
            return dict(it)
        if dirty:
            _save(items)
        return None


def mark_done(item_id: str, url: str, post_id: str = "") -> bool:
    """발행 성공으로 마킹."""
    return _update_status(item_id, "done", url=url, post_id=post_id)


def mark_failed(item_id: str, error: str = "") -> bool:
    """발행 실패로 마킹 — 'pending' 으로 되돌려서 재시도 가능하게 할지는 정책 결정."""
    return _update_status(item_id, "failed", error=error)


def _update_status(item_id: str, status: str, *,
                    url: str = "", post_id: str = "", error: str = "") -> bool:
    with _LOCK:
        items = _load()
        for it in items:
            if it.get("id") == item_id:
                it["status"] = status
                if url:
                    it["result_url"] = url
                if post_id:
                    it["result_post_id"] = post_id
                if error:
                    it["error"] = error[:500]
                _save(items)
                return True
        return False


def get(item_id: str) -> Optional[dict]:
    """id 로 조회 (파이프라인이 결과 polling 할 때)."""
    for it in _load():
        if it.get("id") == item_id:
            return dict(it)
    return None


def wait_done(item_id: str, timeout_sec: int = 600, poll_sec: float = 5.0) -> Optional[dict]:
    """item 이 done|failed 상태가 될 때까지 polling. 타임아웃 시 None.

    파이프라인이 sync 동작을 원할 때 사용. timeout 안에 처리 안 되면 publish_queue
    기록 못 하므로 ROI 추적 누락. 너무 짧게 잡지 말 것.
    """
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        it = get(item_id)
        if it and it.get("status") in ("done", "failed"):
            return it
        time.sleep(poll_sec)
    return None


def list_all(status: Optional[str] = None) -> list[dict]:
    """전체 또는 특정 status 항목 반환."""
    items = _load()
    if status:
        return [it for it in items if it.get("status") == status]
    return items


def reset_stale_claimed(stale_minutes: int = 30) -> int:
    """오래 'claimed' 상태로 갇힌 항목을 'pending' 으로 되돌림.

    extension 이 crash 했거나 사용자가 브라우저 닫은 경우 회수. bridge server
    가 주기적으로 호출.
    Returns: 되돌린 개수
    """
    cutoff = (datetime.now() - timedelta(minutes=stale_minutes)).isoformat()
    n = 0
    with _LOCK:
        items = _load()
        for it in items:
            if it.get("status") == "claimed" and (it.get("claimed_at") or "") < cutoff:
                it["status"] = "pending"
                it["claimed_at"] = None
                n += 1
        if n:
            _save(items)
    return n


def to_extension_payload(item: dict) -> dict:
    """Chrome extension 이 쓰기 좋은 형태로 변환 (필드 최소화)."""
    return {
        "id":         item.get("id", ""),
        "blog_name":  item.get("blog_name", ""),
        "title":      item.get("title", ""),
        # extension 이 image_html (있으면) 을 content 앞에 prepend
        "content":    (item.get("image_html", "") or "") + (item.get("content", "") or ""),
        "tags":       item.get("tags", []),
        "category":   item.get("category", ""),
        "visibility": item.get("visibility", 20),
    }
