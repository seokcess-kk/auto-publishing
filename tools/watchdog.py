"""
스케줄러 워치독.

용도: Windows 작업 스케줄러로 5분 단위 실행하며 scheduler_runner heartbeat
검사. 5분 (WATCHDOG_STALE_SEC, 기본 300) 이상 갱신이 없으면:
  1. 텔레그램 즉시 알림
  2. WATCHDOG_AUTO_RESTART=true 면 백그라운드로 scheduler_runner 재기동

heartbeat 가 정상이면 조용히 종료 — 사용자는 텔레그램 알림으로만 인지.

수동 실행:
    python tools/watchdog.py

작업 스케줄러 등록은 tools/install_task_scheduler.ps1 참조.
"""
import os
import subprocess
import sys
from pathlib import Path

_BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BASE_DIR))

from dotenv import load_dotenv  # noqa: E402
load_dotenv()

from common.heartbeat import read as hb_read, age_seconds  # noqa: E402
from common.logger import log  # noqa: E402


STALE_SEC = int(os.getenv("WATCHDOG_STALE_SEC", "300"))  # 5분
AUTO_RESTART = os.getenv("WATCHDOG_AUTO_RESTART", "true").lower() == "true"


def _notify(msg: str) -> None:
    """텔레그램 알림 — 환경변수 없으면 stdout 만."""
    try:
        from common.notifier import _send_telegram
        _send_telegram(msg)
    except Exception as e:
        log(f"텔레그램 발송 실패: {e}", "warn")


def _restart_scheduler() -> bool:
    """백그라운드로 scheduler_runner 기동. PowerShell Start-Process 사용."""
    try:
        # pythonw 가 있으면 콘솔 없는 백그라운드, 없으면 python (DETACHED).
        cmd = [
            "powershell.exe", "-NoProfile", "-Command",
            f"Start-Process -WindowStyle Hidden -WorkingDirectory '{_BASE_DIR}' "
            f"-FilePath 'python' -ArgumentList '-m','pipelines.scheduler_runner'",
        ]
        subprocess.run(cmd, check=True, timeout=15)
        log("scheduler_runner 백그라운드 재기동 완료", "ok")
        return True
    except Exception as e:
        log(f"재기동 실패: {e}", "error")
        return False


def check() -> int:
    """heartbeat 검사. 반환 코드: 0=정상, 1=stale, 2=missing."""
    hb = hb_read()
    age = age_seconds()

    if hb is None or age is None:
        msg = (
            "⚠️ [Watchdog] 스케줄러 heartbeat 파일 없음 — 미실행 추정\n"
            f"• 임계값: {STALE_SEC}초\n"
            f"• 자동 재기동: {'ON' if AUTO_RESTART else 'OFF'}"
        )
        log(msg, "warn")
        _notify(msg)
        if AUTO_RESTART:
            if _restart_scheduler():
                _notify("✅ [Watchdog] 스케줄러 자동 재기동 트리거 완료")
        return 2

    if age > STALE_SEC:
        msg = (
            f"🚨 [Watchdog] 스케줄러 stale — {int(age)}초 무응답\n"
            f"• 마지막 heartbeat: {hb.get('last_beat', '?')}\n"
            f"• PID: {hb.get('pid', '?')} (started {hb.get('started_at', '?')})\n"
            f"• 등록 슬롯: {hb.get('registered', '?')}\n"
            f"• 임계값: {STALE_SEC}초\n"
            f"• 자동 재기동: {'ON' if AUTO_RESTART else 'OFF'}"
        )
        log(msg, "error")
        _notify(msg)
        if AUTO_RESTART:
            if _restart_scheduler():
                _notify("✅ [Watchdog] 스케줄러 자동 재기동 트리거 완료")
        return 1

    # 정상 — 조용히 종료. 로그만 남김.
    log(f"[Watchdog] OK — heartbeat age={int(age)}s, "
        f"slots={hb.get('registered')}", "info")
    return 0


if __name__ == "__main__":
    sys.exit(check())
