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


_BASE_URL   = "https://api-gateway.coupang.com"
# ⚠️ sub-id-channel 엔드포인트는 이 계정에서 404(PRECONDITION_FAILED, 미존재).
# 실제 동작하는 리포트는 clicks/orders/commission (2026-06-23 프로브 확인).
# 이들을 subId 기준으로 병합해 사용한다.
_REPORTS_BASE = "/v2/providers/affiliate_open_api/apis/openapi/v1/reports/"


def _keys() -> tuple[str, str]:
    """.env 자격을 호출 시점에 읽는다 — import 순서(load_dotenv 이전 import)에
    따라 빈 키가 모듈에 박제되는 문제 방지."""
    return os.getenv("COUPANG_ACCESS_KEY", ""), os.getenv("COUPANG_SECRET_KEY", "")


def _sign(method: str, path: str, query: str = "") -> str:
    """HmacSHA256 Authorization 헤더 값 생성."""
    access_key, secret_key = _keys()
    dt  = datetime.now(timezone.utc).strftime("%y%m%dT%H%M%SZ")
    msg = dt + method + path + query
    sig = hmac.new(secret_key.encode(), msg.encode(), hashlib.sha256).hexdigest()
    return (f"CEA algorithm=HmacSHA256, access-key={access_key}, "
            f"signed-date={dt}, signature={sig}")


def _fetch_report(report: str, s: str, e: str, channel_id: str = "") -> list | None:
    """단일 리포트(clicks|orders|commission) 조회 → data 배열.

    반환 구분이 ROI 파이프라인의 실패 가시화에 쓰인다:
      list  — 200 정상 (빈 배열이면 '활동 0건'이라는 정상 응답)
      None  — HTTP 비정상/예외 (서명 거부·자격 회귀 등 조사 필요한 실패)
    """
    path  = _REPORTS_BASE + report
    query = f"startDate={s}&endDate={e}"
    if channel_id:
        query += f"&subId={channel_id}"
    auth = _sign("GET", path, query)
    try:
        r = requests.get(
            f"{_BASE_URL}{path}?{query}",
            headers={"Authorization": auth,
                      "Content-Type": "application/json;charset=UTF-8"},
            timeout=15,
        )
        if not r.ok:
            log(f"쿠팡 {report} 리포트 실패: {r.status_code} {r.text[:150]}", "error")
            return None
        return r.json().get("data") or []
    except Exception as ex:
        log(f"쿠팡 {report} 리포트 오류: {ex}", "error")
        return None


def _g(row: dict, cands: tuple):
    """row 에서 후보 키 중 첫 비어있지 않은 값."""
    for k in cands:
        v = row.get(k)
        if v not in (None, ""):
            return v
    return None


def _gi(row: dict, cands: tuple) -> int:
    v = _g(row, cands)
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return 0


def fetch_daily_stats(start_date: date, end_date: date,
                      channel_id: str = "") -> list | None:
    """일별 클릭/주문/수수료 통계 조회 → subId 기준 병합 행.

    clicks/orders/commission 리포트를 각각 조회해 subId 로 병합한다. 반환 행은
    {subId, date, clicks, orders, commission, gmv} 형태 — roi_aggregator 의
    _row_to_normalized 가 그대로 소비. subId 에는 소스 접미사(-kw/-gb/-bc)가
    포함되므로, 호출 측에서 접미사로 소스별 집계 가능.

    Args:
        start_date / end_date: 조회 구간(포함)
        channel_id: 특정 subId 필터 — 빈 값이면 전체

    Returns:
        병합 행 리스트 (빈 리스트 = 조회 성공했지만 활동 0건).
        None = 조회 실패 (자격 미설정/서명 거부/HTTP 오류) — 호출 측이
        '측정 루프 죽음'으로 가시화해야 하는 상태.
    """
    access_key, secret_key = _keys()
    if not access_key or not secret_key:
        log("쿠팡 ACCESS_KEY/SECRET_KEY 미설정 — stats 조회 불가", "error")
        return None
    # 쿠팡 HMAC secret 은 40자 hex — 길이 이상은 콘솔 오복사/재발급 무효화의
    # 대표 증상 (2026-07-10: 41자 키로 모든 엔드포인트 401 회귀).
    if len(secret_key) != 40:
        log(f"쿠팡 SECRET_KEY 길이 이상({len(secret_key)}자, 정상 40자) — "
            "파트너스 콘솔에서 재복사/재발급 필요", "error")

    s = start_date.strftime("%Y%m%d")
    e = end_date.strftime("%Y%m%d")
    clicks = _fetch_report("clicks", s, e, channel_id)
    orders = _fetch_report("orders", s, e, channel_id)
    if clicks is None and orders is None:
        return None
    clicks = clicks or []
    orders = orders or []

    agg: dict = {}

    def _bucket(sid: str, dt: str) -> dict:
        return agg.setdefault(sid, {
            "subId": sid, "date": dt,
            "clicks": 0, "orders": 0, "commission": 0, "gmv": 0,
        })

    for r in clicks:
        sid = _g(r, ("subId", "subParam", "subid", "channelId")) or ""
        b = _bucket(sid, _g(r, ("date", "statDate", "reportDate")) or s)
        b["clicks"] += _gi(r, ("click", "clicks", "clickCount"))

    for r in orders:
        sid = _g(r, ("subId", "subParam", "subid", "channelId")) or ""
        b = _bucket(sid, _g(r, ("date", "statDate", "reportDate", "orderDate")) or s)
        oc = _g(r, ("order", "orders", "orderCount"))
        # orders 리포트가 주문/아이템 단위 행이면 count 필드가 없을 수 있어 1로 센다.
        b["orders"]     += int(float(oc)) if oc not in (None, "") else 1
        b["commission"] += _gi(r, ("commission", "commissionAmount",
                                   "totalCommission", "commissionPrice"))
        b["gmv"]        += _gi(r, ("gmv", "saleAmount", "salePrice",
                                   "amount", "orderPrice"))

    rows = list(agg.values())
    log(f"쿠팡 stats 병합: {s}~{e} clicks {len(clicks)}행 / orders {len(orders)}행 "
        f"→ subId {len(rows)}개", "ok")
    return rows


def fetch_yesterday_stats(channel_id: str = "") -> list | None:
    """어제 1일치 — 일일 ROI 집계용 헬퍼."""
    yesterday = date.today() - timedelta(days=1)
    return fetch_daily_stats(yesterday, yesterday, channel_id)


if __name__ == "__main__":
    # 매뉴얼 검증 — 어제 1일치 출력
    rows = fetch_yesterday_stats()
    yest = date.today() - timedelta(days=1)
    print(f"쿠팡 stats — 어제({yest}):")
    if rows is None:
        print("  (조회 실패 — 자격 미설정/서명 거부. 위 [ERROR] 로그 참고)")
    elif not rows:
        print("  (활동 0건 — 어제 클릭/주문 없음)")
    else:
        for r in rows:
            print(f"  {json.dumps(r, ensure_ascii=False)}")
