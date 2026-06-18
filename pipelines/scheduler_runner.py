"""
전체 파이프라인 스케줄 실행기 (registry 패턴)

실행:
    python -m pipelines.scheduler_runner

각 파이프라인은 모듈 상단에 SCHEDULE 메타를 선언한다:

    SCHEDULE = {
        "env":  "SCHEDULE_NEWSPICK_WP",     # 시간을 읽을 .env 키
        "func": "run",                       # 호출할 함수명
        "args_from_env": (                   # (선택) 함수 인자를 env 에서 읽음
            "NEWSPICK_CATEGORY:추천",        # 기본값 지정 (문자열)
            "POST_COUNT:3:int",              # 타입 캐스팅 (int|float)
        ),
    }

scheduler_runner 는 pipelines/ 의 모든 모듈을 import 해 SCHEDULE 을 자동 발견한다.
새 파이프라인은 SCHEDULE 만 선언하면 자동으로 스케줄링된다.

.env 에서 시간 설정:
    SCHEDULE_NEWSPICK_TISTORY=09:00,18:00
    SCHEDULE_NEWSPICK_WP=10:00,19:00
    SCHEDULE_COUPANG_WP=07:00
    SCHEDULE_ALIEXPRESS_WP=11:30
    SCHEDULE_RISESET_NAVER=06:30
    ... (각 파이프라인의 SCHEDULE['env'] 참고)

추가 고정 스케줄:
    SCHEDULE_THREADS_REFRESH=03:00  # Threads 토큰 갱신 (파이프라인 외 시스템 작업)
"""
import importlib
import os
import pkgutil
import subprocess
import sys
import traceback
import schedule
import time
from typing import Callable, Sequence, TypedDict

from dotenv import load_dotenv
load_dotenv()

import pipelines
from common.logger import log
from common.scheduler import _safe_call


class ScheduleMeta(TypedDict, total=False):
    """파이프라인 모듈이 선언하는 SCHEDULE 딕셔너리의 타입.

    필수:
        env:  실행 시간을 읽을 .env 키 (예: "SCHEDULE_NEWSPICK_WP")
        func: 호출할 함수 이름 (예: "run")
    선택:
        args_from_env: 함수 인자를 환경변수로 전달. 형식:
                       "ENV_NAME:기본값" 또는 "ENV_NAME:기본값:type"
                       type 은 "str" | "int" | "float"
    """
    env: str
    func: str
    args_from_env: Sequence[str]


def _resolve_arg(spec: str) -> str | int | float:
    """'ENV_NAME:default' 또는 'ENV_NAME:default:type' 형태를 값으로 변환."""
    parts = spec.split(":")
    env_name = parts[0]
    default  = parts[1] if len(parts) > 1 else ""
    typ      = parts[2] if len(parts) > 2 else "str"

    raw = os.getenv(env_name, default)
    if typ == "int":
        return int(raw)
    if typ == "float":
        return float(raw)
    return raw


def _register(times_env: str, func: Callable, *args, **kwargs) -> int:
    """환경변수로 지정된 시간에 func 등록 (in-process). 등록 수 반환."""
    times_str = os.getenv(times_env, "")
    if not times_str:
        return 0
    count = 0
    for t in times_str.split(","):
        t = t.strip()
        if t:
            schedule.every().day.at(t).do(_safe_call, func, *args, **kwargs)
            log(f"스케줄 등록: {func.__name__} @ {t}", "ok")
            count += 1
    return count


# 일시적(네트워크/DNS) 실패 — 영구 실패와 구분해 재시도할 가치가 있는 표식.
# 모두 "사이트에 닿지도 못한" 단계의 오류라, 재시도해도 중복 발행 부작용이 없다.
_TRANSIENT_MARKERS = (
    "ERR_NAME_NOT_RESOLVED",
    "getaddrinfo failed",
    "NameResolutionError",
    "Temporary failure in name resolution",
    "ERR_INTERNET_DISCONNECTED",
    "ERR_NETWORK_CHANGED",
    "ERR_CONNECTION_RESET",
    "ERR_CONNECTION_TIMED_OUT",
    "ERR_TIMED_OUT",
    "ERR_CONNECTION_CLOSED",
    "ERR_PROXY_CONNECTION_FAILED",
    "Max retries exceeded",
)


def _is_transient(stderr_text: str) -> bool:
    """stderr 에 일시적 네트워크/DNS 오류 표식이 있으면 True."""
    return any(m in stderr_text for m in _TRANSIENT_MARKERS)


def _to_text(raw) -> str:
    """bytes|str|None → str (utf-8, 손상 문자 대체)."""
    if isinstance(raw, (bytes, bytearray)):
        return raw.decode("utf-8", errors="replace")
    return raw or ""


def _kill_process_tree(pid: int) -> None:
    """자식·손자 프로세스까지 강제 종료.

    파이프라인 subprocess(python) 가 timeout 으로 죽어도 그가 띄운 Playwright
    Chromium 은 살아남아 프로필/포트를 점유하고 이후 실행을 연쇄 오염시킨다
    (06-16 aliexpress 1800s timeout 이후 잔존 Chromium → 후속 CDP 'Target page
    closed' 패턴). Windows 는 taskkill /T(tree), POSIX 는 프로세스 그룹 killpg.
    """
    if sys.platform == "win32":
        try:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                capture_output=True, timeout=15,
            )
        except Exception as e:
            log(f"taskkill 트리 종료 실패 (무시) pid={pid}: {e}", "warn")
    else:
        import signal as _sig
        try:
            os.killpg(os.getpgid(pid), _sig.SIGKILL)
        except Exception:
            try:
                os.kill(pid, _sig.SIGKILL)
            except Exception:
                pass


def _notify_err(module_name: str, exc: Exception) -> None:
    try:
        from common.notifier import notify_error
        notify_error(module_name, exc)
    except Exception:
        pass


def _safe_subprocess_call(module_name: str, _attempt: int = 0) -> None:
    """파이프라인 모듈을 별도 python 프로세스로 실행.

    playwright sync_playwright 인스턴스/persistent profile 잠금 등 누적 부작용은
    매번 새 프로세스 → 격리로 차단. 종료 코드 != 0 이면 notify_error.

    매 실행은 common.run_ledger 에 기록되어 daily_summary 의 슬롯 검증/원인
    진단 입력으로 쓰인다. stderr 는 capture 해 ledger 의 stderr_tail 에
    저장한다 (Task Scheduler 환경에서는 어차피 stdout 이 어디에도 흐르지 않음).

    timeout: .env SCHEDULE_SUBPROCESS_TIMEOUT (기본 1800초/30분). timeout 시
    자식 프로세스 트리(Chromium 포함)를 강제 종료한다.

    stderr 에 일시적 네트워크/DNS 오류 표식이 있으면 즉시 실패로 버리지 않고
    SCHEDULE_TRANSIENT_BACKOFF_MIN 분 뒤 1회 재시도를 schedule 에 예약한다 —
    최대 SCHEDULE_TRANSIENT_RETRIES 회. 재시도는 메인 루프를 sleep 으로 막지
    않는다(블로킹 시 heartbeat 정지 → watchdog 가 스케줄러를 재기동시키므로).
    """
    def _int_env(name: str, default: int) -> int:
        try:
            return int(os.getenv(name, str(default)))
        except ValueError:
            return default

    timeout = _int_env("SCHEDULE_SUBPROCESS_TIMEOUT", 1800)
    max_retries = _int_env("SCHEDULE_TRANSIENT_RETRIES", 2)
    backoff_min = max(1, _int_env("SCHEDULE_TRANSIENT_BACKOFF_MIN", 5))

    from datetime import datetime as _dt
    from common.run_ledger import append_run

    is_retry = _attempt > 0
    log(f"실행 (subprocess): {module_name}"
        + (f" [일시오류 재시도 {_attempt}/{max_retries}]" if is_retry else ""), "step")
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    started_at = _dt.now()

    # subprocess.run 대신 Popen — timeout 시 proc.pid 로 트리를 직접 정리하기 위함.
    # stdin=DEVNULL: 자식이 부모의 isatty 를 상속해 _is_interactive()=True 로 오판,
    #   무인 실행에서 manual login 모드로 빠지던 문제 차단.
    # start_new_session: POSIX 에서 새 세션 → killpg 로 손자까지 한 번에 종료.
    #   Windows 에선 무시되며(taskkill /T 로 트리 종료), False 이므로 무해.
    try:
        proc = subprocess.Popen(
            [sys.executable, "-u", "-m", module_name],
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=(sys.platform != "win32"),
        )
    except Exception as e:
        log(f"{module_name} subprocess 생성 예외:\n{traceback.format_exc()}", "error")
        append_run(
            module=module_name, started_at=started_at, finished_at=_dt.now(),
            exit_code="exception", status="exception", stderr_tail=None, error=str(e),
        )
        _notify_err(module_name, e)
        return

    try:
        _stdout_b, stderr_b = proc.communicate(timeout=timeout)
        returncode = proc.returncode
    except subprocess.TimeoutExpired:
        log(f"{module_name} timeout 초과 ({timeout}s) — 프로세스 트리 강제 종료", "error")
        _kill_process_tree(proc.pid)
        try:
            _stdout_b, stderr_b = proc.communicate(timeout=10)
        except Exception:
            stderr_b = b""
        append_run(
            module=module_name, started_at=started_at, finished_at=_dt.now(),
            exit_code="timeout", status="timeout",
            stderr_tail=stderr_b, error=f"subprocess timeout {timeout}s",
        )
        _notify_err(module_name, TimeoutError(f"subprocess timeout {timeout}s"))
        return

    stderr_text = _to_text(stderr_b)

    # 대부분의 파이프라인은 내부 logic 에서 publish 실패해도 sys.exit(0) 으로
    # 끝나므로 exit_code 만으로는 "발행이 실제로 됐는지" 알 수 없다. stderr 에
    # [ERROR] 가 있고 성공 표식("발행 성공"/"발행 완료") 이 없으면 exit 0 이어도
    # 'failure' 로 강등해 거짓 양성 알림을 막는다.
    status = "success" if returncode == 0 else "failure"
    if returncode == 0 and "[ERROR]" in stderr_text:
        success_markers = ("발행 성공", "발행 완료", "발행 완료:")
        if not any(m in stderr_text for m in success_markers):
            status = "failure"
            log(f"{module_name} exit=0 이지만 stderr 에 [ERROR] 존재 — 'failure' 로 기록", "warn")

    # 일시적 네트워크 오류 → 메인 루프를 막지 않는 1회 재시도 예약. 이번 시도는
    # ledger/알림 보류 — 최종 시도만 기록해 daily_summary 가 한 슬롯=한 결과로 본다.
    if status == "failure" and _attempt < max_retries and _is_transient(stderr_text):
        log(f"{module_name} 일시적 네트워크 오류 감지 — {backoff_min}분 후 재시도 예약 "
            f"({_attempt + 1}/{max_retries})", "warn")

        def _retry_job(_m=module_name, _a=_attempt):
            _safe_subprocess_call(_m, _a + 1)
            return schedule.CancelJob  # 한 번만 실행 후 자기 제거

        schedule.every(backoff_min).minutes.do(_retry_job)
        return

    append_run(
        module=module_name, started_at=started_at, finished_at=_dt.now(),
        exit_code=returncode, status=status, stderr_tail=stderr_b,
    )

    if status == "failure":
        if returncode != 0:
            log(f"{module_name} 비정상 종료 (exit={returncode})", "error")
        reason = (
            f"exit code {returncode}" if returncode != 0
            else "stderr 에 [ERROR] 존재 (publish 실패 추정)"
        )
        if is_retry and _is_transient(stderr_text):
            reason += f" (일시적 오류 {max_retries}회 재시도 후 실패)"
        _notify_err(module_name, RuntimeError(reason))


def _register_module(times_env: str, module_name: str) -> int:
    """파이프라인 모듈을 subprocess 로 실행하도록 등록. 자원 격리 목적."""
    times_str = os.getenv(times_env, "")
    if not times_str:
        return 0
    count = 0
    for t in times_str.split(","):
        t = t.strip()
        if t:
            schedule.every().day.at(t).do(_safe_subprocess_call, module_name)
            log(f"스케줄 등록: {module_name} @ {t} (subprocess)", "ok")
            count += 1
    return count


def _discover_schedules():
    """pipelines 패키지의 모듈을 스캔해 SCHEDULE 메타를 수집."""
    for mod_info in pkgutil.iter_modules(pipelines.__path__):
        # 커널/공통/실행기 모듈 제외
        if mod_info.name.startswith("_") or mod_info.name == "scheduler_runner":
            continue
        try:
            mod = importlib.import_module(f"pipelines.{mod_info.name}")
        except Exception as e:
            log(f"import 실패 (건너뜀): pipelines.{mod_info.name} — {e}", "warn")
            continue
        meta = getattr(mod, "SCHEDULE", None)
        if meta:
            yield mod, meta


def _kill_other_scheduler_instances() -> int:
    """singleton 가드 — 다른 scheduler_runner 프로세스를 찾아 강제 종료.

    watchdog 가 기존 스케줄러를 죽이지 않고 새 인스턴스를 띄우는 결함으로 인해
    여러 스케줄러가 공존하면 동일 시각에 같은 파이프라인이 N회 실행되어
    daily_summary 가 N번 발송되고 키워드 풀이 다중 소모되는 사고가 발생.
    매 기동 시 본 가드가 잔존 인스턴스를 정리한다.

    탐지 경로 2가지 (둘 다 시도):
      (a) heartbeat 파일에 기록된 PID — 항상 정확 (자신이 직전 인스턴스로부터 인계)
      (b) cmdline 매칭 — Windows WMI 가 cmdline 을 노출하는 경우. 권한/서비스 등
          이유로 비공개일 수도 있으므로 (a) 와 병용.

    Returns: 종료시킨 프로세스 수
    """
    import signal as _signal

    my_pid = os.getpid()
    killed_pids: set[int] = set()

    # (a) heartbeat 의 PID — 거의 항상 실제 스케줄러
    try:
        from common.heartbeat import read as _hb_read
        hb = _hb_read()
        if hb:
            hb_pid = int(hb.get("pid", 0) or 0)
            if hb_pid and hb_pid != my_pid:
                killed_pids.add(hb_pid)
    except Exception:
        pass

    # (b) cmdline 매칭
    marker = "pipelines.scheduler_runner"
    if sys.platform == "win32":
        try:
            ps_cmd = (
                "Get-CimInstance Win32_Process | "
                f"Where-Object {{ $_.Name -eq 'python.exe' -and $_.CommandLine -like '*{marker}*' "
                f"-and $_.ProcessId -ne {my_pid} }} | "
                "ForEach-Object { $_.ProcessId }"
            )
            result = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_cmd],
                capture_output=True, text=True, timeout=10,
            )
            for s in result.stdout.splitlines():
                s = s.strip()
                if s.isdigit():
                    killed_pids.add(int(s))
        except Exception as e:
            log(f"singleton 가드 PowerShell 예외(무시): {e}", "warn")
    else:
        try:
            res = subprocess.run(
                ["pgrep", "-f", marker], capture_output=True, text=True, timeout=5,
            )
            for s in res.stdout.split():
                if s.isdigit() and int(s) != my_pid:
                    killed_pids.add(int(s))
        except Exception as e:
            log(f"singleton 가드 pgrep 예외(무시): {e}", "warn")

    if not killed_pids:
        return 0

    # 종료 — Windows: taskkill /F, POSIX: SIGTERM
    # taskkill 의 'Access is denied' 같은 권한 거부 케이스가 silent 하게 통과하면
    # 잔존 인스턴스를 인지 못한 채 둘이 함께 돌게 된다 (= 중복 발행 사고).
    # 그래서 실패한 PID 도 별도로 추적해 운영자에게 알림.
    actually_killed: list[int] = []
    failed_kills: list[tuple[int, str]] = []
    for pid in sorted(killed_pids):
        try:
            if sys.platform == "win32":
                proc = subprocess.run(
                    ["taskkill", "/F", "/PID", str(pid)],
                    capture_output=True, text=True, timeout=5,
                )
                if proc.returncode == 0:
                    actually_killed.append(pid)
                else:
                    reason = (proc.stderr or proc.stdout or "rc=" + str(proc.returncode)).strip()
                    failed_kills.append((pid, reason))
            else:
                os.kill(pid, _signal.SIGTERM)
                actually_killed.append(pid)
        except Exception as e:
            failed_kills.append((pid, str(e)))

    if actually_killed:
        log(f"기존 scheduler 인스턴스 {len(actually_killed)}개 정리: "
            f"PID {', '.join(map(str, actually_killed))}", "warn")
        time.sleep(2)  # OS 가 리소스 해제할 시간

    if failed_kills:
        detail = "; ".join(f"PID {p}: {r[:80]}" for p, r in failed_kills)
        msg = (
            f"🚨 [Scheduler] 잔존 인스턴스 정리 실패 — 중복 실행 위험\n"
            f"• 실패: {detail}\n"
            f"• 원인 추정: 권한 컨텍스트 불일치 (elevated 인스턴스를 비-elevated 가드가 못 죽임)\n"
            f"• 권장: 본 프로세스를 즉시 종료하고 Task Scheduler 경로로 재기동"
        )
        log(msg, "error")
        try:
            from common.notifier import _send_telegram
            _send_telegram(msg)
        except Exception:
            pass

    return len(actually_killed)


def main() -> None:
    log("=== 스케줄러 시작 ===", "step")

    # 다른 scheduler_runner 인스턴스가 떠있으면 즉시 정리 — 중복 발행 방지
    _kill_other_scheduler_instances()

    # Tistory bridge 모드면 HTTP 서버를 daemon thread 로 임베드 — 별도 터미널 불필요
    if os.getenv("TISTORY_PUBLISHER", "web").strip().lower() == "bridge":
        try:
            from pipelines.tistory_bridge import start_server_in_thread
            start_server_in_thread(port=int(os.getenv("TISTORY_BRIDGE_PORT", "5757")))
        except Exception as e:
            log(f"[bridge] embedded 시작 예외 (무시 — 별도 프로세스로 띄울 수 있음): {e}", "warn")

    registered = 0

    # 파이프라인 자동 발견 — subprocess 로 실행 (자원 격리)
    for mod, meta in _discover_schedules():
        func = getattr(mod, meta["func"], None)
        if not func:
            log(f"{mod.__name__}: SCHEDULE['func']={meta['func']} 함수 없음", "warn")
            continue
        # args_from_env 는 모듈의 __main__ 블록이 직접 env 에서 읽으므로
        # subprocess 가 그대로 환경변수 상속하면 동일하게 동작.
        registered += _register_module(meta["env"], mod.__name__)

    # 파이프라인 외 고정 작업 — playwright 안 쓰는 단순 토큰 갱신은 in-process
    from common.threads_token import refresh_long_lived_token
    registered += _register("SCHEDULE_THREADS_REFRESH", refresh_long_lived_token)

    if registered == 0:
        log("등록된 스케줄 없음 — .env에서 SCHEDULE_* 환경변수를 설정하세요", "warn")
        log("예시: SCHEDULE_NEWSPICK_TISTORY=09:00,18:00", "info")
        return

    log(f"총 {registered}개 스케줄 등록 완료. 실행 대기 중... (Ctrl+C로 종료)", "step")

    from common.notifier import notify_scheduler_start
    notify_scheduler_start(registered)

    from common.heartbeat import write as _hb_write, clear as _hb_clear
    from datetime import datetime
    started_at = datetime.now().isoformat(timespec="seconds")

    try:
        while True:
            _hb_write(os.getpid(), registered, started_at)
            schedule.run_pending()
            time.sleep(30)
    except KeyboardInterrupt:
        log("스케줄러 종료", "warn")
    finally:
        _hb_clear()


if __name__ == "__main__":
    main()
