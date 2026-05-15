"""
파이프라인 실행 ledger — 슬롯별 시작/종료/결과를 JSON 으로 영속화.

publish_queue.json 은 _성공_ 발행만 기록하므로 "어느 슬롯이 실패했나"를
사후에 알 길이 없었다. 이 ledger 는 scheduler_runner 의 _safe_subprocess_call
이 매 호출마다 append 해 daily_summary 가 슬롯 검증에 활용한다.

저장 위치: data/pipeline_runs.json
스키마 (list of records):
    {
        "module":      "pipelines.riseset_to_tistory",
        "started_at":  "2026-05-15T18:30:00",
        "finished_at": "2026-05-15T18:30:42",
        "exit_code":   1,            # int | "timeout" | "exception"
        "status":      "failure",    # "success" | "empty" | "failure" | "timeout" | "exception"
        "stderr_tail": "...",        # 최근 N 줄 (4KB 제한)
        "error":       "..."         # exception 메시지 (있을 때만)
    }

retention: 기본 7일 — 오래된 항목은 append 시 자동 prune.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from pathlib import Path


_BASE_DIR = Path(__file__).resolve().parent.parent
LEDGER_PATH = _BASE_DIR / "data" / "pipeline_runs.json"

# stderr 마지막 capture 한도 (텔레그램 4096자 제한 + ledger 크기 고려)
_STDERR_TAIL_BYTES = 4000
# 보존 기간 (일)
_RETENTION_DAYS = 7


def _load() -> list[dict]:
    if not LEDGER_PATH.exists():
        return []
    try:
        with open(LEDGER_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _save(records: list[dict]) -> None:
    LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = LEDGER_PATH.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=2)
    os.replace(tmp, LEDGER_PATH)


def _prune(records: list[dict], retention_days: int = _RETENTION_DAYS) -> list[dict]:
    cutoff = (datetime.now() - timedelta(days=retention_days)).isoformat()
    return [r for r in records if (r.get("started_at") or "") >= cutoff]


def _tail_text(text: str | bytes | None, limit: int = _STDERR_TAIL_BYTES) -> str:
    """문자열/바이트의 마지막 limit 바이트만 안전하게 반환."""
    if not text:
        return ""
    if isinstance(text, bytes):
        text = text.decode("utf-8", errors="replace")
    if len(text) <= limit:
        return text
    return "...[truncated]...\n" + text[-limit:]


def append_run(*, module: str, started_at: datetime, finished_at: datetime,
               exit_code, status: str,
               stderr_tail: str | bytes | None = None,
               error: str | None = None) -> None:
    """한 슬롯의 실행 결과를 ledger 에 기록.

    exit_code: int | "timeout" | "exception"
    status:    "success" | "empty" | "failure" | "timeout" | "exception"
    """
    record = {
        "module":      module,
        "started_at":  started_at.isoformat(timespec="seconds"),
        "finished_at": finished_at.isoformat(timespec="seconds"),
        "exit_code":   exit_code,
        "status":      status,
        "stderr_tail": _tail_text(stderr_tail),
    }
    if error:
        record["error"] = str(error)[:500]

    records = _load()
    records.append(record)
    records = _prune(records)
    _save(records)


def list_today(module: str | None = None) -> list[dict]:
    """오늘 시작한 실행 기록만 반환. module 필터 가능."""
    today = datetime.now().date().isoformat()
    rows = [r for r in _load() if (r.get("started_at") or "").startswith(today)]
    if module:
        rows = [r for r in rows if r.get("module") == module]
    return rows


def list_recent(days: int = 7) -> list[dict]:
    """최근 N 일의 모든 기록."""
    cutoff = (datetime.now() - timedelta(days=days)).isoformat()
    return [r for r in _load() if (r.get("started_at") or "") >= cutoff]
