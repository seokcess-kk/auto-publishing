"""
스케줄러 워치독.

용도: Windows 작업 스케줄러로 5분 단위 실행하며 scheduler_runner heartbeat
검사. 5분 (WATCHDOG_STALE_SEC, 기본 300) 이상 갱신이 없으면:
  1. 텔레그램 즉시 알림
  2. WATCHDOG_AUTO_RESTART=true 면 백그라운드로 scheduler_runner 재기동

heartbeat 가 정상이면 조용히 종료 — 사용자는 텔레그램 알림으로만 인지.

실행 기록은 `logs/watchdog.log` 에 남는다. 작업 스케줄러가 5분마다 콘솔 창을
띄우지 않도록 워치독은 `pythonw.exe` 로 등록되는데, pythonw 는 sys.stdout /
sys.stderr 이 None 이라 common.logger 의 콘솔 출력이 통째로 사라진다. 파일
로그가 워치독이 돌았다는 유일한 근거이므로 콘솔과 별개로 항상 기록한다.

수동 실행:
    python tools/watchdog.py       # 콘솔 + 파일 로그
    pythonw tools/watchdog.py      # 파일 로그만 (작업 스케줄러와 동일 조건)

작업 스케줄러 등록은 tools/install_task_scheduler.ps1 참조.
"""
import logging
import os
import subprocess
import sys
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

_BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BASE_DIR))

from dotenv import load_dotenv  # noqa: E402
load_dotenv()

from common.heartbeat import read as hb_read, age_seconds  # noqa: E402
from common.logger import log  # noqa: E402


STALE_SEC = int(os.getenv("WATCHDOG_STALE_SEC", "300"))  # 5분
AUTO_RESTART = os.getenv("WATCHDOG_AUTO_RESTART", "true").lower() == "true"

# 5분마다 돌기 때문에 무한 증가를 막아야 한다. 1MB × 3 백업이면 대략 수 주치.
LOG_FILE = Path(os.getenv("WATCHDOG_LOG_FILE") or (_BASE_DIR / "logs" / "watchdog.log"))
LOG_MAX_BYTES = int(os.getenv("WATCHDOG_LOG_MAX_BYTES", str(1024 * 1024)))
LOG_BACKUP_COUNT = int(os.getenv("WATCHDOG_LOG_BACKUP_COUNT", "3"))

_FILE_LOGGER: logging.Logger | None = None

# common.logger.log() 의 레벨 문자열 → (logging 레벨, 파일 로그 라벨)
_LEVEL_LABELS: dict[str, tuple[int, str]] = {
    "info":    (logging.INFO,    "INFO"),
    "ok":      (logging.INFO,    "OK"),
    "success": (logging.INFO,    "OK"),
    "warn":    (logging.WARNING, "WARN"),
    "warning": (logging.WARNING, "WARN"),
    "error":   (logging.ERROR,   "ERROR"),
}


class _WatchdogFormatter(logging.Formatter):
    """`[2026-08-17 09:30:00] [INFO] 메시지` — 1 이벤트 1 라인."""

    def format(self, record: logging.LogRecord) -> str:
        ts = datetime.fromtimestamp(record.created).strftime("%Y-%m-%d %H:%M:%S")
        label = getattr(record, "label", record.levelname)
        # 텔레그램 알림 본문은 여러 줄이라 그대로 쓰면 grep 이 깨진다.
        msg = " | ".join(
            line.strip() for line in record.getMessage().splitlines() if line.strip()
        )
        return f"[{ts}] [{label}] {msg}"


def _file_logger() -> logging.Logger | None:
    """logs/watchdog.log 로거. 파일을 못 열면 None (워치독 본체는 계속 동작)."""
    global _FILE_LOGGER
    if _FILE_LOGGER is not None:
        return _FILE_LOGGER
    logger = logging.getLogger("watchdog.file")
    if not logger.handlers:
        try:
            LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
            handler = RotatingFileHandler(
                LOG_FILE,
                maxBytes=LOG_MAX_BYTES,
                backupCount=LOG_BACKUP_COUNT,
                encoding="utf-8",
            )
            handler.setFormatter(_WatchdogFormatter())
            logger.addHandler(handler)
        except OSError:
            return None
    logger.setLevel(logging.INFO)
    logger.propagate = False
    _FILE_LOGGER = logger
    return logger


def wlog(msg: str, level: str = "info") -> None:
    """콘솔(가능하면) + logs/watchdog.log 동시 기록.

    pythonw.exe 로 실행되면 콘솔 쪽은 조용히 사라지고 파일 로그만 남는다.
    로깅 실패가 워치독을 죽여선 안 되므로 양쪽 다 예외를 삼킨다.
    """
    try:
        log(msg, level)
    except Exception:
        pass
    logger = _file_logger()
    if logger is None:
        return
    lvl, label = _LEVEL_LABELS.get(level.lower(), (logging.INFO, level.upper()))
    try:
        logger.log(lvl, msg, extra={"label": label})
    except Exception:
        pass


def _hidden_subprocess_kwargs() -> dict:
    """자식 프로세스를 콘솔 창 없이 띄우기 위한 subprocess kwargs.

    - CREATE_NO_WINDOW: 워치독을 pythonw.exe 로 돌려도 powershell.exe 는 자기
      콘솔 창을 새로 띄운다. 이 플래그가 그걸 막는다 (Windows 전용).
    - 표준 스트림 명시: pythonw 에는 상속시킬 표준 핸들이 없다. 기본값
      (상속) 이면 자식이 잘못된 핸들을 받으므로 DEVNULL/PIPE 로 못박고,
      받은 출력은 실패 시 로그에 남긴다.
    """
    kwargs: dict = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
    }
    if sys.platform == "win32":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
    return kwargs


def _notify(msg: str) -> None:
    """텔레그램 알림 — 환경변수 없으면 stdout 만."""
    try:
        from common.notifier import _send_telegram
        _send_telegram(msg)
    except Exception as e:
        wlog(f"텔레그램 발송 실패: {e}", "warn")


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
            # 출력이 파이프로 리다이렉트되면 PS 5.1 은 콘솔 코드페이지(cp949)로
            # 쓴다. 아래 한 줄이 없으면 한글 에러 메시지가 로그에서 깨진다.
            "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; "
            f"Stop-ScheduledTask -TaskName '{task_name}' -ErrorAction SilentlyContinue; "
            f"Start-Sleep -Seconds 2; "
            f"Start-ScheduledTask -TaskName '{task_name}'",
        ]
        proc = subprocess.run(
            cmd, check=True, timeout=30, **_hidden_subprocess_kwargs()
        )
        wlog(f"scheduler_runner 재기동 완료 (Task: {task_name})", "ok")
        stderr = (proc.stderr or "").strip()
        if stderr:
            wlog(f"재기동 stderr: {stderr}", "warn")
        return True
    except subprocess.CalledProcessError as e:
        detail = ((e.stderr or "") + " " + (e.stdout or "")).strip()
        wlog(f"재기동 실패 (exit={e.returncode}): {detail or e}", "error")
        return False
    except Exception as e:
        wlog(f"재기동 실패: {e}", "error")
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
        wlog(msg, "warn")
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
        wlog(msg, "error")
        _notify(msg)
        if AUTO_RESTART:
            if _restart_scheduler():
                _notify("✅ [Watchdog] 스케줄러 자동 재기동 트리거 완료")
        return 1

    # 정상 — 조용히 종료. 로그만 남김.
    wlog(f"[Watchdog] OK — heartbeat age={int(age)}s, "
         f"slots={hb.get('registered')}", "info")
    return 0


def main() -> int:
    """pythonw 로 실행되면 traceback 이 갈 곳(stderr) 이 없어 조용히 죽는다.
    예외를 파일 로그로 흘려야 5분마다 실패하는 상황을 나중에 알 수 있다."""
    try:
        return check()
    except Exception as e:
        wlog(f"[Watchdog] 예외로 중단: {type(e).__name__}: {e}", "error")
        return 3


if __name__ == "__main__":
    sys.exit(main())
