"""
카카오톡 캘린더 연동 — 자동발행 실패 로그 기록.

- notifier 에서 파이프라인 실패/부분실패 시 호출됨
- 예외를 상위로 전파하지 않음 (notifier 패턴과 동일)
- access_token 401 시 refresh_access_token() 으로 1회 재시도
- 앱 scope: talk_calendar 필요 (카카오 콘솔 '이용 중 동의')

주요 함수:
    ensure_calendar() -> str      — '자동발행기록' 서브캘린더 id 확보 (캐시 우선)
    record_failure(...) -> bool   — 실패 일정 등록 (Task 3 에서 구현)
"""
import json
import os
from datetime import datetime, timedelta, timezone
from typing import Optional

import requests

from common.kakao_token import get_access_token, refresh_access_token
from common.logger import log


_API_BASE = "https://kapi.kakao.com/v2/api/calendar"
_CACHE_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "kakao_calendar.json")
_CALENDAR_NAME = "자동발행기록"
_KST = timezone(timedelta(hours=9))
_DETAIL_MAX = 500


# ----------------------------------------------------------------------------
# Cache helpers
# ----------------------------------------------------------------------------
def _cache_path() -> str:
    return os.path.abspath(_CACHE_PATH)


def _load_cached_id() -> str:
    try:
        with open(_cache_path(), "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get("calendar_id", "") or ""
    except (FileNotFoundError, json.JSONDecodeError):
        return ""


def _save_cached_id(calendar_id: str) -> None:
    path = _cache_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    payload = {
        "calendar_id": calendar_id,
        "name": _CALENDAR_NAME,
        "created_at": datetime.now(_KST).isoformat(timespec="seconds"),
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


# ----------------------------------------------------------------------------
# HTTP helper
# ----------------------------------------------------------------------------
def _request(method: str, path: str, *, data: Optional[dict] = None) -> Optional[requests.Response]:
    """카카오 캘린더 API 호출 헬퍼. 401 → refresh 후 1회 재시도. 예외는 None 반환."""
    token = get_access_token()
    if not token:
        log("카카오 access_token 없음", "error")
        return None

    url = f"{_API_BASE}{path}"

    def _do(tok: str) -> Optional[requests.Response]:
        try:
            return requests.request(
                method,
                url,
                headers={"Authorization": f"Bearer {tok}"},
                data=data,
                timeout=10,
            )
        except requests.RequestException as e:
            log(f"카카오 캘린더 요청 실패: {e}", "error")
            return None

    resp = _do(token)
    if resp is None:
        return None

    if resp.status_code == 401:
        log("카카오 access_token 401 — refresh 후 재시도", "warn")
        new_token = refresh_access_token()
        if not new_token:
            return resp
        resp = _do(new_token)

    return resp


# ----------------------------------------------------------------------------
# Public API
# ----------------------------------------------------------------------------
def ensure_calendar() -> str:
    """'자동발행기록' 서브캘린더 id 확보. 없으면 생성. 실패 시 '' 반환."""
    cached = _load_cached_id()
    if cached:
        return cached

    # 1) 기존 목록 조회
    resp = _request("GET", "/calendars")
    if resp is not None and resp.ok:
        try:
            payload = resp.json()
        except ValueError:
            payload = {}
        for cal in payload.get("calendars", []) or []:
            if cal.get("name") == _CALENDAR_NAME:
                cal_id = cal.get("id", "")
                if cal_id:
                    _save_cached_id(cal_id)
                    log(f"'{_CALENDAR_NAME}' 캘린더 기존 것 사용: {cal_id}", "info")
                    return cal_id
    elif resp is not None:
        log(f"카카오 캘린더 목록 조회 실패: {resp.status_code} {resp.text[:200]}", "warn")

    # 2) 신규 생성
    resp = _request(
        "POST",
        "/create/calendar",
        data={"name": _CALENDAR_NAME, "color": "RED"},
    )
    if resp is None or not resp.ok:
        status = resp.status_code if resp is not None else "?"
        body = resp.text[:200] if resp is not None else ""
        log(f"카카오 캘린더 생성 실패: {status} {body}", "error")
        return ""

    try:
        payload = resp.json()
    except ValueError:
        log("카카오 캘린더 생성 응답 파싱 실패", "error")
        return ""

    cal_id = payload.get("calendar_id", "")
    if not cal_id:
        log(f"카카오 캘린더 생성 응답 이상: {payload}", "error")
        return ""

    _save_cached_id(cal_id)
    log(f"'{_CALENDAR_NAME}' 캘린더 신규 생성: {cal_id}", "ok")
    return cal_id


def record_failure(
    pipeline: str,
    detail: str,
    *,
    partial: bool = False,
    started_at: Optional[datetime] = None,
) -> bool:
    """
    실패 이벤트를 '자동발행기록' 서브캘린더에 등록한다.

    - pipeline: 파이프라인 이름 (예: 'coupang_to_wordpress')
    - detail:   실패 요약 (에러 메시지 또는 '2/5건 발행'). 500자 truncate
    - partial:  True면 부분실패(⚠️ ORANGE), False면 완전실패(❌ RED)
    - started_at: 실패 시각 (기본: now, KST)

    반환: 성공 True / 실패 False. 예외는 내부에서 흡수.
    """
    try:
        cal_id = ensure_calendar()
        if not cal_id:
            return False

        start = started_at or datetime.now(_KST)
        if start.tzinfo is None:
            start = start.replace(tzinfo=_KST)
        # Kakao API 제약: start_at 은 5분 격자 정렬 필수 (floor)
        start = start.replace(minute=(start.minute // 5) * 5, second=0, microsecond=0)
        end = start + timedelta(minutes=5)

        title_prefix = "⚠️" if partial else "❌"
        title = f"[{title_prefix} {pipeline}] {'부분실패' if partial else '실패'}"

        short_detail = (detail or "")[:_DETAIL_MAX]

        event_payload = {
            "calendar_id": cal_id,
            "event": json.dumps({
                "title":       title,
                "time":        {
                    "start_at":  start.isoformat(timespec="seconds"),
                    "end_at":    end.isoformat(timespec="seconds"),
                    "time_zone": "Asia/Seoul",
                    "all_day":   False,
                },
                "description": short_detail,
                "color":       "ORANGE" if partial else "RED",
                "reminders":   [],
            }, ensure_ascii=False),
        }

        resp = _request("POST", "/create/event", data=event_payload)
        if not resp or not resp.ok:
            body = resp.text[:200] if resp is not None else "no response"
            log(f"캘린더 이벤트 등록 실패: {body}", "warn")
            return False

        log(f"캘린더 이벤트 등록: {title}", "ok")
        return True

    except Exception as e:
        log(f"record_failure 내부 예외 (무시): {e}", "warn")
        return False
