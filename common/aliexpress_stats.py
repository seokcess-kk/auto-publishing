"""
알리익스프레스 파트너스 통계 API (Alibaba Open Platform / TOP)

쿠팡 파트너스의 sub-id-channel report 와 같은 역할 — 어필리에이트 일별 클릭/
주문/커미션 통계를 가져와 keyword_roi.json 누적에 사용.

엔드포인트 : POST https://api-sg.aliexpress.com/sync
메서드     : aliexpress.affiliate.order.list.by.index
인증       : AppKey + Session(access_token) + MD5 sign
서명 규칙  : sorted(params) → key+value concat → APP_SECRET 으로 양쪽 wrap
             → MD5 hex 대문자

발급 절차:
  1. https://open.aliexpress.com/ 앱 등록 + Affiliate API 권한 신청
  2. AppKey / AppSecret 받음
  3. OAuth 인증으로 본인 access_token / refresh_token 발급
  4. .env 에 ALIEXPRESS_APP_KEY / ALIEXPRESS_APP_SECRET / ALIEXPRESS_ACCESS_TOKEN
     채우기 → 즉시 fetch_orders() 호출 가능
"""
import hashlib
import os
from datetime import datetime, timedelta, date

import requests

from common.logger import log


_APP_KEY    = os.getenv("ALIEXPRESS_APP_KEY", "")
_APP_SECRET = os.getenv("ALIEXPRESS_APP_SECRET", "")
_SESSION    = os.getenv("ALIEXPRESS_ACCESS_TOKEN", "")
_BASE_URL   = "https://api-sg.aliexpress.com/sync"


def _sign_md5(params: dict) -> str:
    """Alibaba TOP MD5 서명.

    sorted by key → key+value concat → APP_SECRET 으로 양쪽 wrap
    → MD5 hex 대문자.
    """
    sorted_items = sorted(
        (k, v) for k, v in params.items()
        if v is not None and k != "sign" and v != ""
    )
    s = "".join(f"{k}{v}" for k, v in sorted_items)
    raw = f"{_APP_SECRET}{s}{_APP_SECRET}"
    return hashlib.md5(raw.encode("utf-8")).hexdigest().upper()


def _has_credentials() -> bool:
    return bool(_APP_KEY and _APP_SECRET and _SESSION)


def fetch_orders(start_time: datetime, end_time: datetime,
                  status: str = "Payment Completed",
                  page_size: int = 50, max_pages: int = 20) -> list:
    """일자 범위 어필리에이트 주문 조회.

    Args:
        start_time, end_time: 조회 범위 (서버 시간대 기준 — 보통 UTC)
        status: 주문 상태 — "Payment Completed" | "Buyer Confirmed Receipt"
                | "Wait Buyer Accept Goods" | "Finished"
        page_size, max_pages: 페이지네이션

    Returns:
        주문 dict 리스트 — TOP API 의 order 객체 배열 그대로
        실패/자격 미설정 시 빈 리스트
    """
    if not _has_credentials():
        log("알리 TOP API 자격 미설정 — ALIEXPRESS_APP_KEY/APP_SECRET/ACCESS_TOKEN 확인", "warn")
        return []

    all_orders: list = []
    next_index = "0"

    for page in range(max_pages):
        params = {
            "method":         "aliexpress.affiliate.order.list.by.index",
            "app_key":        _APP_KEY,
            "session":        _SESSION,
            "timestamp":      datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
            "format":         "json",
            "v":              "2.0",
            "sign_method":    "md5",
            "start_time":     start_time.strftime("%Y-%m-%d %H:%M:%S"),
            "end_time":       end_time.strftime("%Y-%m-%d %H:%M:%S"),
            "status":         status,
            "page_size":      str(page_size),
            "start_query_index_id": next_index,
        }
        params["sign"] = _sign_md5(params)

        try:
            r = requests.post(_BASE_URL, data=params, timeout=20)
            r.raise_for_status()
            body = r.json()
        except Exception as e:
            log(f"알리 TOP API 오류 (page={page}): {e}", "warn")
            break

        # 응답 구조 (TOP API 표준):
        # aliexpress_affiliate_order_list_by_index_response
        #   .resp_result.result.orders.order: [...]
        #   .resp_result.result.next_query_index_id: "..."
        try:
            wrap = body.get("aliexpress_affiliate_order_list_by_index_response", {})
            result = wrap.get("resp_result", {}).get("result", {}) or {}
            orders = (result.get("orders") or {}).get("order") or []
            if not isinstance(orders, list):
                orders = [orders]
            all_orders.extend(orders)
            next_index = str(result.get("next_query_index_id") or "")
            if not next_index or not orders:
                break
        except Exception as e:
            log(f"알리 TOP 응답 파싱 오류: {e} body={str(body)[:200]}", "warn")
            break

    log(f"알리 TOP 주문 조회: {start_time.date()}~{end_time.date()} {len(all_orders)}건", "ok")
    return all_orders


def fetch_yesterday_orders() -> list:
    """어제 1일치 — 일일 ROI 집계용 헬퍼."""
    today = date.today()
    start = datetime.combine(today - timedelta(days=1),
                              datetime.min.time())
    end = datetime.combine(today, datetime.min.time()) - timedelta(seconds=1)
    return fetch_orders(start, end)


if __name__ == "__main__":
    import json
    rows = fetch_yesterday_orders()
    print(f"알리 어필리에이트 주문 — 어제: {len(rows)}건")
    if not rows:
        print("  (자격 미설정 또는 주문 없음)")
    for r in rows[:3]:
        print(f"  {json.dumps(r, ensure_ascii=False)[:300]}")
