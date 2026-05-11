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
    """환경변수로 지정된 시간에 func 등록. 등록 수 반환."""
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


def main() -> None:
    log("=== 스케줄러 시작 ===", "step")

    registered = 0

    # 파이프라인 자동 발견
    for mod, meta in _discover_schedules():
        func = getattr(mod, meta["func"], None)
        if not func:
            log(f"{mod.__name__}: SCHEDULE['func']={meta['func']} 함수 없음", "warn")
            continue

        # 인자 해석 (env → 값)
        args = []
        for spec in meta.get("args_from_env", ()):
            args.append(_resolve_arg(spec))

        registered += _register(meta["env"], func, *args)

    # 파이프라인 외 고정 작업
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
