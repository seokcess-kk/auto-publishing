"""
주간 시스템 점검 파이프라인.

매일 새벽 5시에 트리거되지만 월요일에만 실제 점검 수행 (schedule 라이브러리의
'매일' 패턴과 호환성 유지). 점검 항목:

1. 스케줄러 프로세스 생존 확인 (pipelines.scheduler_runner)
2. data/scheduler.log 지난 7일치에서 실패/부분실패 집계
3. .sessions/ 티스토리 세션 파일 mtime — 14일 이상 경고
4. 뉴스픽 ensure_session 실행 → True 여부 확인
5. 결과 텔레그램/카카오 알림 발송

복구 시도는 하지 않고 상태 리포트만.
"""
import os
import re
import subprocess
import time
from datetime import datetime, timedelta
from pathlib import Path

from common.logger import log
from common.notifier import _notify


SCHEDULE = {
    "env":  "SCHEDULE_HEALTHCHECK",
    "func": "run",
}


ROOT = Path(__file__).resolve().parent.parent
SCHEDULER_LOG = ROOT / "data" / "scheduler.log"
SESSIONS_DIR = ROOT / ".sessions"
SESSION_WARN_DAYS = 14


def _weekday_name(d: datetime) -> str:
    return ["월", "화", "수", "목", "금", "토", "일"][d.weekday()]


def _check_scheduler_alive() -> tuple[bool, str]:
    """pipelines.scheduler_runner 프로세스가 떠 있는지 pgrep 으로 확인."""
    try:
        res = subprocess.run(
            ["pgrep", "-f", "pipelines.scheduler_runner"],
            capture_output=True, text=True, timeout=5,
        )
        pids = [p for p in res.stdout.strip().splitlines() if p]
        if pids:
            return True, f"PID {', '.join(pids)}"
        return False, "프로세스 없음"
    except Exception as e:
        return False, f"확인 실패: {e}"


def _aggregate_log_failures(days: int = 7) -> dict:
    """scheduler.log 에서 최근 N일치 '캘린더 이벤트 등록: [❌/⚠️ ...]' 라인 집계."""
    if not SCHEDULER_LOG.exists():
        return {"exists": False}

    cutoff = datetime.now() - timedelta(days=days)
    pattern = re.compile(r"캘린더 이벤트 등록:\s*\[([❌⚠️])\s*([^\]]+)\]\s*(\S+)")
    error_pattern = re.compile(r"\[ERROR\]")

    fail_count = 0
    partial_count = 0
    error_count = 0
    by_pipeline: dict[str, dict] = {}

    try:
        with open(SCHEDULER_LOG, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                # 시각 추출 (HH:MM:SS 포맷이라 날짜 없음 — 파일 mtime 기준으로 이후 7일 필터 어려움)
                # 대신 파일 전체 스캔하되 라인 수준 집계만.
                clean = re.sub(r"\x1b\[[0-9;]*m", "", line)
                m = pattern.search(clean)
                if m:
                    emoji, name, kind = m.group(1), m.group(2).strip(), m.group(3)
                    entry = by_pipeline.setdefault(name, {"실패": 0, "부분실패": 0})
                    if "❌" in emoji:
                        fail_count += 1
                        entry["실패"] += 1
                    elif "⚠" in emoji:
                        partial_count += 1
                        entry["부분실패"] += 1
                elif error_pattern.search(clean):
                    error_count += 1
    except Exception as e:
        return {"exists": True, "error": str(e)}

    # 파일 mtime 으로 커버 기간 추정
    mtime = datetime.fromtimestamp(SCHEDULER_LOG.stat().st_mtime)
    ctime = datetime.fromtimestamp(SCHEDULER_LOG.stat().st_ctime)
    covered_since = max(ctime, cutoff)

    return {
        "exists": True,
        "fail": fail_count,
        "partial": partial_count,
        "errors": error_count,
        "by_pipeline": by_pipeline,
        "log_mtime": mtime,
        "log_covered_since": covered_since,
    }


def _check_tistory_sessions() -> list[dict]:
    """티스토리 세션 파일 mtime 점검. 14일 이상 경고 대상 반환."""
    warnings = []
    if not SESSIONS_DIR.exists():
        return warnings
    cutoff = time.time() - SESSION_WARN_DAYS * 86400
    for f in SESSIONS_DIR.glob("tistory_*.pkl"):
        mtime = f.stat().st_mtime
        age_days = (time.time() - mtime) / 86400
        warnings.append({
            "file": f.name,
            "age_days": round(age_days, 1),
            "stale": mtime < cutoff,
            "mtime": datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M"),
        })
    return warnings


def _check_newspick_session() -> tuple[bool, str]:
    """NewspickSource.ensure_session() 호출."""
    try:
        from sources.newspick import NewspickSource
        ok = NewspickSource().ensure_session()
        return ok, "OK" if ok else "세션 확보 실패"
    except Exception as e:
        return False, f"예외: {e}"


def _format_report(
    today: datetime,
    scheduler_alive: bool,
    scheduler_info: str,
    log_stats: dict,
    tistory_sessions: list[dict],
    newspick_ok: bool,
    newspick_msg: str,
) -> str:
    lines = []
    lines.append("🔍 <b>[Auto Publishing 주간 점검]</b>")
    lines.append(f"📅 {today.strftime('%Y-%m-%d %H:%M')} ({_weekday_name(today)})")
    lines.append("━━━━━━━━━━━━━━━━━━━━")

    # 1. 스케줄러
    status_icon = "✅" if scheduler_alive else "🚨"
    lines.append(f"{status_icon} 스케줄러: {scheduler_info}")

    # 2. 로그 집계
    if not log_stats.get("exists"):
        lines.append("⚠️ scheduler.log 없음")
    elif "error" in log_stats:
        lines.append(f"⚠️ 로그 읽기 실패: {log_stats['error']}")
    else:
        fail = log_stats["fail"]
        partial = log_stats["partial"]
        mtime = log_stats["log_mtime"].strftime("%m-%d %H:%M")
        lines.append(
            f"📊 로그 집계 (mtime {mtime}): "
            f"❌실패 {fail} / ⚠️부분 {partial} / ERROR {log_stats['errors']}"
        )
        if log_stats["by_pipeline"]:
            for name, counts in sorted(log_stats["by_pipeline"].items()):
                parts = [f"{k} {v}" for k, v in counts.items() if v > 0]
                if parts:
                    lines.append(f"  • {name}: {', '.join(parts)}")

    # 3. 티스토리 세션
    if tistory_sessions:
        lines.append("📂 티스토리 세션:")
        for s in tistory_sessions:
            icon = "⚠️" if s["stale"] else "✅"
            lines.append(f"  {icon} {s['file']} — {s['age_days']}일전 ({s['mtime']})")
    else:
        lines.append("⚠️ 티스토리 세션 파일 없음")

    # 4. 뉴스픽
    icon = "✅" if newspick_ok else "🚨"
    lines.append(f"{icon} 뉴스픽 세션: {newspick_msg}")

    lines.append("━━━━━━━━━━━━━━━━━━━━")
    lines.append("ℹ️ 자동 복구는 하지 않음 — 문제 발견 시 수동 조치 필요")

    return "\n".join(lines)


def run() -> None:
    """월요일에만 실제 점검 수행. 그 외 요일은 즉시 종료."""
    today = datetime.now()
    if today.weekday() != 0:  # 0 = 월요일
        log(f"healthcheck 건너뜀 (오늘 {_weekday_name(today)}요일)", "info")
        return

    log("=== 주간 시스템 점검 시작 ===", "step")

    scheduler_alive, scheduler_info = _check_scheduler_alive()
    log(f"스케줄러: {scheduler_info}", "info")

    log_stats = _aggregate_log_failures(days=7)
    log(f"로그 집계: {log_stats.get('fail', 0)}실패 / {log_stats.get('partial', 0)}부분", "info")

    tistory_sessions = _check_tistory_sessions()
    stale_count = sum(1 for s in tistory_sessions if s["stale"])
    log(f"티스토리 세션: {len(tistory_sessions)}개 중 {stale_count}개 경고", "info")

    newspick_ok, newspick_msg = _check_newspick_session()
    log(f"뉴스픽: {newspick_msg}", "ok" if newspick_ok else "error")

    report = _format_report(
        today, scheduler_alive, scheduler_info,
        log_stats, tistory_sessions, newspick_ok, newspick_msg,
    )

    _notify(report)
    log("=== 주간 점검 완료 ===", "step")


if __name__ == "__main__":
    # 수동 실행 시에도 월요일 체크 스킵하고 바로 점검
    log("=== 수동 healthcheck 실행 ===", "step")
    scheduler_alive, scheduler_info = _check_scheduler_alive()
    log_stats = _aggregate_log_failures(days=7)
    tistory_sessions = _check_tistory_sessions()
    newspick_ok, newspick_msg = _check_newspick_session()
    report = _format_report(
        datetime.now(), scheduler_alive, scheduler_info,
        log_stats, tistory_sessions, newspick_ok, newspick_msg,
    )
    print(report)
    _notify(report)
