"""
쿠팡 파트너스 통계 API 모듈

매일 stats 를 가져와 publish_queue 의 어필리에이트 URL / 키워드에 매칭
→ 키워드별 ROI 산출 (roi_aggregator).

엔드포인트: GET /v2/providers/affiliate_open_api/apis/openapi/v1/reports/sub-id-channel
인증: HmacSHA256 (.env 의 COUPANG_ACCESS_KEY / COUPANG_SECRET_KEY)

응답 필드는 쿠팡 파트너스 콘솔의 Reports 와 동일 — 정확한 스키마는 운영
초기 1회 fetch 후 publish_queue 와 매칭하면서 매핑 확정.
"""
import hashlib
import hmac
import json
import os
from datetime import datetime, timezone, date, timedelta

import requests

from common.logger import log


_ACCESS_KEY = os.getenv("COUPANG_ACCESS_KEY", "")
_SECRET_KEY = os.getenv("COUPANG_SECRET_KEY", "")
_BASE_URL   = "https://api-gateway.coupang.com"
_REPORT_PATH = "/v2/providers/affiliate_open_api/apis/openapi/v1/reports/sub-id-channel"


def _sign(method: str, path: str, query: str = "") -> str:
    """HmacSHA256 Authorization 헤더 값 생성."""
    dt  = datetime.now(timezone.utc).strftime("%y%m%dT%H%M%SZ")
    msg = dt + method + path + query
    sig = hmac.new(_SECRET_KEY.encode(), msg.encode(), hashlib.sha256).hexdigest()
    return (f"CEA algorithm=HmacSHA256, access-key={_ACCESS_KEY}, "
            f"signed-date={dt}, signature={sig}")


def fetch_daily_stats(start_date: date, end_date: date,
                      channel_id: str = "") -> list:
    """일별 클릭/주문/수수료 통계 조회.

    Args:
        start_date: 조회 시작일
        end_date:   조회 종료일 (포함)
        channel_id: 특정 subId 필터 — 빈 값이면 전체

    Returns:
        쿠팡 응답의 data 배열 그대로 — 키 매핑은 호출 측에서 처리
        실패/자격 미설정 시 빈 리스트
    """
    if not _ACCESS_KEY or not _SECRET_KEY:
        log("쿠팡 ACCESS_KEY/SECRET_KEY 미설정 — stats 조회 불가", "warn")
        return []

    s = start_date.strftime("%Y%m%d")
    e = end_date.strftime("%Y%m%d")
    query = f"startDate={s}&endDate={e}"
    if channel_id:
        query += f"&subId={channel_id}"

    auth = _sign("GET", _REPORT_PATH, query)
    try:
        r = requests.get(
            f"{_BASE_URL}{_REPORT_PATH}?{query}",
            headers={"Authorization": auth,
                      "Content-Type": "application/json;charset=UTF-8"},
            timeout=15,
        )
        if not r.ok:
            log(f"쿠팡 stats API 실패: {r.status_code} {r.text[:200]}", "warn")
            return []
        body = r.json()
        data = body.get("data") or []
        log(f"쿠팡 stats 조회 완료: {s}~{e} {len(data)}행", "ok")
        return data
    except Exception as e:
        log(f"쿠팡 stats 조회 오류: {e}", "warn")
        return []


def fetch_yesterday_stats(channel_id: str = "") -> list:
    """어제 1일치 — 일일 ROI 집계용 헬퍼."""
    yesterday = date.today() - timedelta(days=1)
    return fetch_daily_stats(yesterday, yesterday, channel_id)


if __name__ == "__main__":
    # 매뉴얼 검증 — 어제 1일치 출력
    rows = fetch_yesterday_stats()
    yest = date.today() - timedelta(days=1)
    print(f"쿠팡 stats — 어제({yest}):")
    if not rows:
        print("  (응답 없음 — 자격 미설정 또는 API 미적용 계정)")
    for r in rows:
        print(f"  {json.dumps(r, ensure_ascii=False)}")
