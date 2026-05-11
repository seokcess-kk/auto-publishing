"""
스케줄러 헬퍼 모듈
- schedule 라이브러리 기반 주기 실행
- 기존 스크립트들의 time.sleep 루프 패턴을 통합
"""
import time
import traceback
from typing import Callable

try:
    import schedule
    HAS_SCHEDULE = True
except ImportError:
    HAS_SCHEDULE = False

from .logger import log


def run_every(interval_minutes: int, func: Callable, *args, **kwargs) -> None:
    """func을 interval_minutes마다 반복 실행 (Ctrl+C로 종료)."""
    if not HAS_SCHEDULE:
        raise ImportError("schedule 패키지 필요: pip install schedule")

    log(f"스케줄러 시작: {func.__name__} / {interval_minutes}분 간격", "step")
    schedule.every(interval_minutes).minutes.do(_safe_call, func, *args, **kwargs)

    # 시작 즉시 1회 실행
    _safe_call(func, *args, **kwargs)

    try:
        while True:
            schedule.run_pending()
            time.sleep(30)
    except KeyboardInterrupt:
        log("스케줄러 종료 (Ctrl+C)", "warn")


def run_at(time_str: str, func: Callable, *args, **kwargs) -> None:
    """매일 time_str (예: '09:00')에 func 실행."""
    if not HAS_SCHEDULE:
        raise ImportError("schedule 패키지 필요: pip install schedule")

    log(f"스케줄러 시작: {func.__name__} / 매일 {time_str}", "step")
    schedule.every().day.at(time_str).do(_safe_call, func, *args, **kwargs)

    try:
        while True:
            schedule.run_pending()
            time.sleep(30)
    except KeyboardInterrupt:
        log("스케줄러 종료 (Ctrl+C)", "warn")


# 일시적 에러로 분류되는 예외 — 재시도 대상.
# 영구 에러 (4xx/로그인 실패/no privilege) 는 재시도 무의미하므로 제외.
_TRANSIENT_EXC = (
    "Timeout", "ReadTimeout", "ConnectTimeout", "ConnectionError",
    "ConnectionResetError", "ProtocolError", "RemoteDisconnected",
    "ChunkedEncodingError", "SSLError",
)

# 영구 에러 키워드 — 메시지에 포함되면 재시도 안 함
_PERMANENT_MARKERS = (
    "no privilege", "unauthorized", "forbidden", "401", "403",
    "로그인 실패", "login failed", "invalid_grant", "permission",
    "captcha", "blocked",
)


def _is_transient(exc: Exception) -> bool:
    """일시적/재시도 가능 예외인지 판단."""
    name = type(exc).__name__
    if any(t in name for t in _TRANSIENT_EXC):
        msg = str(exc).lower()
        # 일시적 예외 타입이라도 메시지에 영구 마커 있으면 재시도 안 함
        if any(m in msg for m in _PERMANENT_MARKERS):
            return False
        return True
    return False


def _safe_call(func: Callable, *args, **kwargs) -> None:
    """예외가 발생해도 스케줄러가 멈추지 않도록 래핑.

    봇 탐지 회피용 시각 jitter 적용 — 매 호출 시작 시점을 0~SCHEDULE_JITTER_SEC
    초 만큼 무작위 지연. 기본 0(비활성). .env 에 SCHEDULE_JITTER_SEC=180 설정 시
    각 파이프라인이 정시 ±0~3분 사이 임의 시점에 시작.

    재시도 정책: 일시적 네트워크 에러 (Timeout/ConnectionError/SSL 등) 만
    지수 백오프 재시도. 영구 에러 (4xx/no privilege/로그인 실패) 는 즉시 실패.
        .env: SCHEDULE_RETRY_COUNT (기본 2), SCHEDULE_RETRY_BACKOFF_SEC (기본 300)
        백오프: 1차 300초, 2차 900초 (=300*3)
    """
    import os, random
    try:
        jitter = int(os.getenv("SCHEDULE_JITTER_SEC", "0"))
    except ValueError:
        jitter = 0
    if jitter > 0:
        delay = random.uniform(0, jitter)
        log(f"  jitter sleep {delay:.1f}s before {func.__name__}", "info")
        time.sleep(delay)

    try:
        retry_count = int(os.getenv("SCHEDULE_RETRY_COUNT", "2"))
        backoff_base = int(os.getenv("SCHEDULE_RETRY_BACKOFF_SEC", "300"))
    except ValueError:
        retry_count, backoff_base = 2, 300

    attempt = 0
    while True:
        try:
            func(*args, **kwargs)
            return
        except Exception as e:
            attempt += 1
            transient = _is_transient(e)
            if transient and attempt <= retry_count:
                wait = backoff_base * (3 ** (attempt - 1))  # 300, 900, ...
                log(f"{func.__name__} 일시 오류 ({type(e).__name__}) — "
                    f"{wait}s 후 재시도 ({attempt}/{retry_count}): {e}", "warn")
                time.sleep(wait)
                continue

            # 재시도 한도 초과 또는 영구 에러
            log(f"{func.__name__} 실행 중 오류:\n{traceback.format_exc()}", "error")
            try:
                from .notifier import notify_error
                notify_error(func.__name__, e)
            except Exception:
                pass
            return
