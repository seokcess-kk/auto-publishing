"""
통합 발행 큐 관리자

파이프라인이 URL을 성공적으로 발행할 때마다 add_url()로 등록한다.
매일 PM 10시 index_and_backlink_pipeline이 큐를 읽어 색인 + 백링크를 수행한다.

저장 위치: data/publish_queue.json
스키마:
[
  {
    "queued_at": "2026-04-27T14:30:00",
    "url": "https://linkmaker.tistory.com/12345",
    "title": "쿠팡 주방용품 TOP10",
    "platform": "tistory",
    "google_indexed": "X",
    "naver_indexed": "X",
    "backlinked": "X"
  }
]

platform 값: "wordpress" | "tistory" | "github"
상태 값: "X" = 미완료, "O" = 완료
"""
import json
import os
from datetime import datetime, date
from typing import Optional

from common.logger import log


_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_QUEUE_PATH = os.path.join(_BASE_DIR, "data", "publish_queue.json")

_FIELDS = ("google_indexed", "naver_indexed", "backlinked")


def _load(path: str) -> list:
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception as e:
        log(f"publish_queue 로드 실패: {e}", "warn")
        return []


def _save(data: list, path: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def add_url(url: str, platform: str, title: str = "",
            queue_path: str = DEFAULT_QUEUE_PATH) -> bool:
    """발행 성공 URL을 큐에 추가. 중복·비절대 URL 거부. 추가 여부 반환."""
    url = url.strip()
    if not url:
        return False
    # 절대 URL 가드 — Google Indexing API 가 'not in standard URL format' 으로
    # 거절하므로 큐 진입 시점에 차단해 추후 색인 단계 오염 방지.
    from urllib.parse import urlparse
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        log(f"[publish_queue] 절대 URL 아님, 무시: {url}", "warn")
        return False

    data = _load(queue_path)
    existing = {item["url"] for item in data}
    if url in existing:
        return False

    data.append({
        "queued_at": datetime.now().isoformat(timespec="seconds"),
        "url": url,
        "title": title,
        "platform": platform,
        "google_indexed": "X",
        "naver_indexed": "X",
        "backlinked": "X",
    })
    _save(data, queue_path)
    log(f"[publish_queue] 추가: {url} ({platform})", "ok")
    return True


def get_pending(field: str,
                queue_path: str = DEFAULT_QUEUE_PATH) -> list:
    """field 기준 미완료("X") 항목 반환.

    field: "google_indexed" | "naver_indexed" | "backlinked"
    """
    if field not in _FIELDS:
        raise ValueError(f"알 수 없는 field: {field}. 허용값: {_FIELDS}")
    data = _load(queue_path)
    return [item for item in data if item.get(field, "X") == "X"]


def mark_done(url: str, field: str,
              queue_path: str = DEFAULT_QUEUE_PATH) -> bool:
    """URL의 field를 "O"로 갱신. 변경 여부 반환."""
    if field not in _FIELDS:
        raise ValueError(f"알 수 없는 field: {field}")
    data = _load(queue_path)
    changed = False
    for item in data:
        if item["url"] == url and item.get(field) != "O":
            item[field] = "O"
            changed = True
            break
    if changed:
        _save(data, queue_path)
    return changed


def mark_done_bulk(urls: list, field: str,
                   queue_path: str = DEFAULT_QUEUE_PATH) -> int:
    """여러 URL의 field를 한 번에 "O"로 갱신. 변경된 건수 반환."""
    if field not in _FIELDS:
        raise ValueError(f"알 수 없는 field: {field}")
    url_set = set(urls)
    data = _load(queue_path)
    count = 0
    for item in data:
        if item["url"] in url_set and item.get(field) != "O":
            item[field] = "O"
            count += 1
    if count:
        _save(data, queue_path)
    return count


def get_newly_indexed_today(queue_path: str = DEFAULT_QUEUE_PATH) -> list:
    """오늘 날짜에 queued_at이 기록되어 있고 google_indexed="O"인 항목 반환.

    백링크 소스로 사용: 오늘 색인 완료된 URL들.
    """
    today = date.today().isoformat()
    data = _load(queue_path)
    return [
        item for item in data
        if item.get("queued_at", "").startswith(today)
        and item.get("google_indexed") == "O"
        and item.get("backlinked") == "X"
    ]


def get_pending_backlink(queue_path: str = DEFAULT_QUEUE_PATH) -> list:
    """backlinked="X"인 전체 항목 반환 (날짜 무관 폴백용)."""
    return get_pending("backlinked", queue_path)


def stats(queue_path: str = DEFAULT_QUEUE_PATH) -> dict:
    """큐 통계 반환."""
    data = _load(queue_path)
    total = len(data)
    result = {"total": total}
    for field in _FIELDS:
        done = sum(1 for item in data if item.get(field) == "O")
        result[field] = {"done": done, "pending": total - done}
    return result
