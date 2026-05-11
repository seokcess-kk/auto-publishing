"""
스케줄러 헬스체크용 heartbeat 파일.

scheduler_runner 가 매 루프(30초)마다 `.runtime/scheduler_heartbeat` 의
mtime 을 갱신한다. 별도 워치독 (`tools/watchdog.py`) 이 이 파일의 mtime 을
검사해 5분 이상 stale 이면 텔레그램 알림 + 재기동을 트리거한다.

heartbeat 는 단일 라인 JSON 으로 부가 정보 보유:
    {"pid": 12345, "registered": 20, "started_at": "2026-05-11T15:48:00"}

이 정보는 워치독 알림 메시지에 컨텍스트로 첨부된다.
"""
import json
from datetime import datetime
from pathlib import Path

_BASE_DIR = Path(__file__).resolve().parent.parent
_RUNTIME_DIR = _BASE_DIR / ".runtime"
HEARTBEAT_FILE = _RUNTIME_DIR / "scheduler_heartbeat"


def write(pid: int, registered: int, started_at: str) -> None:
    """heartbeat 파일 쓰기. 호출은 매 루프마다 (~30초)."""
    _RUNTIME_DIR.mkdir(exist_ok=True)
    payload = {
        "pid": pid,
        "registered": registered,
        "started_at": started_at,
        "last_beat": datetime.now().isoformat(timespec="seconds"),
    }
    try:
        HEARTBEAT_FILE.write_text(json.dumps(payload), encoding="utf-8")
    except OSError:
        pass


def read() -> dict | None:
    """heartbeat 읽기. 파일 없거나 손상되면 None."""
    if not HEARTBEAT_FILE.exists():
        return None
    try:
        return json.loads(HEARTBEAT_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def age_seconds() -> float | None:
    """heartbeat mtime 으로부터 경과 초. 파일 없으면 None."""
    if not HEARTBEAT_FILE.exists():
        return None
    return (datetime.now().timestamp() - HEARTBEAT_FILE.stat().st_mtime)


def clear() -> None:
    """heartbeat 파일 제거 (스케줄러 정상 종료 시 호출)."""
    try:
        HEARTBEAT_FILE.unlink(missing_ok=True)
    except OSError:
        pass
