"""워치독 파일 로그 / 콘솔 창 숨김 단위 테스트.

pythonw.exe 로 실행하면 sys.stdout/stderr 이 None 이라 콘솔 로그가 전부
사라진다. 이 테스트들이 지키는 계약:
  1. 콘솔이 없어도 예외 없이 동작하고 logs/watchdog.log 에는 남는다
  2. 재기동 subprocess 가 Windows 에서 콘솔 창을 띄우지 않는다
  3. 기존 반환 코드(0/1/2) · 텔레그램 알림 · 재기동 트리거가 그대로다
"""
import logging
import subprocess
import sys

import pytest

import tools.watchdog as wd


@pytest.fixture
def log_file(tmp_path, monkeypatch):
    """워치독 파일 로그를 tmp_path 로 격리."""
    path = tmp_path / "logs" / "watchdog.log"
    monkeypatch.setattr(wd, "LOG_FILE", path)
    monkeypatch.setattr(wd, "_FILE_LOGGER", None)
    logger = logging.getLogger("watchdog.file")
    for h in list(logger.handlers):
        logger.removeHandler(h)
        h.close()
    yield path
    for h in list(logger.handlers):
        logger.removeHandler(h)
        h.close()
    wd._FILE_LOGGER = None


def _read(path) -> str:
    return path.read_text(encoding="utf-8")


# ─── 파일 로그 ────────────────────────────────────────────────────────────────

def test_wlog_creates_log_file(log_file):
    wd.wlog("hello", "info")
    assert log_file.exists()
    assert "[INFO] hello" in _read(log_file)


def test_wlog_labels_per_level(log_file):
    wd.wlog("정상", "info")
    wd.wlog("완료", "ok")
    wd.wlog("주의", "warn")
    wd.wlog("실패", "error")
    body = _read(log_file)
    assert "[INFO] 정상" in body
    assert "[OK] 완료" in body
    assert "[WARN] 주의" in body
    assert "[ERROR] 실패" in body


def test_wlog_writes_utf8_korean_and_emoji(log_file):
    wd.wlog("🚨 스케줄러 stale — 327초 무응답", "error")
    # cp949 로 열면 깨지므로 utf-8 로 기록됐는지 명시적으로 확인
    assert "🚨 스케줄러 stale — 327초 무응답" in log_file.read_text(encoding="utf-8")


def test_wlog_flattens_multiline_message(log_file):
    wd.wlog("첫 줄\n• 두 번째\n• 세 번째", "warn")
    lines = [ln for ln in _read(log_file).splitlines() if ln.strip()]
    assert len(lines) == 1
    assert "첫 줄 | • 두 번째 | • 세 번째" in lines[0]


def test_wlog_timestamp_format(log_file):
    import re
    wd.wlog("x", "info")
    assert re.match(r"^\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\] \[INFO\] x$",
                    _read(log_file).strip())


def test_wlog_survives_missing_stdout(log_file, monkeypatch):
    """pythonw.exe 재현 — sys.stdout/stderr 이 None 이어도 죽지 않는다."""
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)
    wd.wlog("콘솔 없음", "error")  # 예외가 나면 실패
    assert "[ERROR] 콘솔 없음" in _read(log_file)


def test_wlog_survives_unwritable_log_path(tmp_path, monkeypatch):
    """로그 파일을 못 만들어도 워치독 본체는 계속 동작해야 한다."""
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory", encoding="utf-8")
    monkeypatch.setattr(wd, "LOG_FILE", blocker / "sub" / "watchdog.log")
    monkeypatch.setattr(wd, "_FILE_LOGGER", None)
    logger = logging.getLogger("watchdog.file")
    for h in list(logger.handlers):
        logger.removeHandler(h)
        h.close()
    wd.wlog("무시됨", "info")  # 예외 없이 통과해야 한다
    assert wd._file_logger() is None


def test_log_rotates_on_max_bytes(log_file, monkeypatch):
    monkeypatch.setattr(wd, "LOG_MAX_BYTES", 200)
    monkeypatch.setattr(wd, "LOG_BACKUP_COUNT", 1)
    for i in range(40):
        wd.wlog(f"라인 {i} " + "x" * 50, "info")
    assert log_file.exists()
    assert log_file.with_suffix(".log.1").exists()
    assert log_file.stat().st_size <= 400  # 무한 증가하지 않음


# ─── 콘솔 창 숨김 ─────────────────────────────────────────────────────────────

def test_hidden_kwargs_no_window_on_windows(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    kwargs = wd._hidden_subprocess_kwargs()
    assert kwargs["creationflags"] == 0x08000000  # CREATE_NO_WINDOW
    # pythonw 에는 상속시킬 표준 핸들이 없다 → 명시적으로 못박아야 한다
    assert kwargs["stdin"] == subprocess.DEVNULL
    assert kwargs["stdout"] == subprocess.PIPE
    assert kwargs["stderr"] == subprocess.PIPE
    assert kwargs["encoding"] == "utf-8"


def test_hidden_kwargs_no_creationflags_off_windows(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    assert "creationflags" not in wd._hidden_subprocess_kwargs()


def test_restart_uses_hidden_window(log_file, monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    seen = {}

    def fake_run(cmd, **kwargs):
        seen["cmd"] = cmd
        seen["kwargs"] = kwargs
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(wd.subprocess, "run", fake_run)
    assert wd._restart_scheduler() is True
    assert seen["kwargs"]["creationflags"] == 0x08000000
    assert seen["kwargs"]["check"] is True
    assert seen["kwargs"]["timeout"] == 30
    # Stop → Start 순서의 Start-ScheduledTask 경로 유지 (elevated 컨텍스트)
    joined = " ".join(seen["cmd"])
    assert "Stop-ScheduledTask" in joined
    assert "Start-ScheduledTask" in joined
    # 파이프로 받은 PS 출력이 cp949 로 깨지지 않도록 UTF-8 을 강제한다
    assert "[Console]::OutputEncoding" in joined
    assert "[OK] scheduler_runner 재기동 완료" in _read(log_file)


def test_restart_failure_logs_stderr(log_file, monkeypatch):
    def fake_run(cmd, **kwargs):
        raise subprocess.CalledProcessError(1, cmd, output="", stderr="접근이 거부되었습니다")

    monkeypatch.setattr(wd.subprocess, "run", fake_run)
    assert wd._restart_scheduler() is False
    body = _read(log_file)
    assert "[ERROR]" in body
    assert "접근이 거부되었습니다" in body


# ─── check() 분기 ─────────────────────────────────────────────────────────────

@pytest.fixture
def no_notify(monkeypatch):
    sent = []
    monkeypatch.setattr(wd, "_notify", lambda m: sent.append(m))
    return sent


def test_check_ok(log_file, no_notify, monkeypatch):
    monkeypatch.setattr(wd, "hb_read", lambda: {"registered": 20, "pid": 1})
    monkeypatch.setattr(wd, "age_seconds", lambda: 12.0)
    assert wd.check() == 0
    assert "[INFO] [Watchdog] OK" in _read(log_file)
    assert no_notify == []  # 정상일 땐 텔레그램 안 보냄


def test_check_stale(log_file, no_notify, monkeypatch):
    monkeypatch.setattr(wd, "hb_read", lambda: {"registered": 20, "pid": 1})
    monkeypatch.setattr(wd, "age_seconds", lambda: 327.0)
    monkeypatch.setattr(wd, "STALE_SEC", 300)
    monkeypatch.setattr(wd, "AUTO_RESTART", True)
    monkeypatch.setattr(wd, "_restart_scheduler", lambda: True)
    assert wd.check() == 1
    body = _read(log_file)
    assert "[ERROR]" in body and "327초 무응답" in body
    assert len(no_notify) == 2  # stale 알림 + 재기동 완료 알림


def test_check_missing_heartbeat(log_file, no_notify, monkeypatch):
    monkeypatch.setattr(wd, "hb_read", lambda: None)
    monkeypatch.setattr(wd, "age_seconds", lambda: None)
    monkeypatch.setattr(wd, "AUTO_RESTART", True)
    monkeypatch.setattr(wd, "_restart_scheduler", lambda: True)
    assert wd.check() == 2
    assert "[WARN]" in _read(log_file)
    assert len(no_notify) == 2


def test_check_no_auto_restart(log_file, no_notify, monkeypatch):
    calls = []
    monkeypatch.setattr(wd, "hb_read", lambda: None)
    monkeypatch.setattr(wd, "age_seconds", lambda: None)
    monkeypatch.setattr(wd, "AUTO_RESTART", False)
    monkeypatch.setattr(wd, "_restart_scheduler", lambda: calls.append(1) or True)
    assert wd.check() == 2
    assert calls == []  # OFF 면 재기동 트리거 안 함
    assert len(no_notify) == 1


def test_main_logs_unexpected_exception(log_file, monkeypatch):
    def boom():
        raise RuntimeError("heartbeat 읽기 폭발")

    monkeypatch.setattr(wd, "check", boom)
    assert wd.main() == 3
    assert "[ERROR] [Watchdog] 예외로 중단: RuntimeError: heartbeat 읽기 폭발" in _read(log_file)
