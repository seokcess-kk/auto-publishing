"""
common/kakao_calendar 라이브 스모크 테스트.

실행:
    python -m tools.test_kakao_calendar ensure
    python -m tools.test_kakao_calendar record
    python -m tools.test_kakao_calendar all
"""
import sys

from dotenv import load_dotenv

load_dotenv()

from common.kakao_calendar import ensure_calendar, record_failure
from common.logger import log


def cmd_ensure():
    log("ensure_calendar() 호출", "step")
    cal_id = ensure_calendar()
    if cal_id:
        log(f"calendar_id = {cal_id}", "ok")
        log("카카오톡 앱 → 톡캘린더에서 '자동발행기록' 서브캘린더 확인하세요", "info")
    else:
        log("calendar_id 획득 실패", "error")
        sys.exit(1)


def cmd_record():
    log("record_failure() 호출 — 3 케이스", "step")

    # 1) 완전 실패
    ok1 = record_failure("test_pipeline", "완전 실패 스모크 테스트 — RED")
    log(f"[1] 완전실패 등록: {ok1}", "ok" if ok1 else "error")

    # 2) 부분 실패
    ok2 = record_failure("test_pipeline", "2/5건 발행", partial=True)
    log(f"[2] 부분실패 등록: {ok2}", "ok" if ok2 else "error")

    # 3) 긴 detail truncate
    ok3 = record_failure("test_pipeline", "x" * 600)
    log(f"[3] 600자 truncate 등록: {ok3}", "ok" if ok3 else "error")

    log("카카오톡 앱 → 톡캘린더 '자동발행기록' 에서 3건 확인하세요", "info")


def main():
    args = sys.argv[1:] or ["all"]
    cmd = args[0]
    if cmd == "ensure":
        cmd_ensure()
    elif cmd == "record":
        cmd_record()
    elif cmd == "all":
        cmd_ensure()
        cmd_record()
    else:
        print(f"Unknown command: {cmd}")
        sys.exit(2)


if __name__ == "__main__":
    main()
