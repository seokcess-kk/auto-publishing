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
    """AutoPublishing_Scheduler 작업을 Stop + Start 로 재기동.

    Start-Process 로 직접 python 을 띄우면 작업 스케줄러를 우회해 비-elevated
    인스턴스가 만들어진다. 기존 인스턴스는 RunLevel=Highest 로 elevated 라
    가드의 taskkill 가 'Access denied' 로 거부되어 중복 실행 사고가 재현된다.
    Start-ScheduledTask 경로로 트리거하면:
      - 권한 컨텍스트가 install_task_scheduler.ps1 의 RunLevel=Highest 일관
      - MultipleInstances=IgnoreNew 가 중복 차단을 보장
      - Stop-ScheduledTask 가 elevated 권한으로 잔존 인스턴스를 안전하게 종료
    이 함수는 watchdog 작업 (RunLevel=Highest) 컨텍스트에서 호출되어야 정상 동작.
    """
    task_name = os.getenv("WATCHDOG_TASK_NAME", "AutoPublishing_Scheduler")
    try:
        cmd = [
            "powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
            f"Stop-ScheduledTask -TaskName '{task_name}' -ErrorAction SilentlyContinue; "
            f"Start-Sleep -Seconds 2; "
            f"Start-ScheduledTask -TaskName '{task_name}'",
        ]
        subprocess.run(cmd, check=True, timeout=30)
        log(f"scheduler_runner 재기동 완료 (Task: {task_name})", "ok")
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
