"""
일일 ROI 집계 — publish_queue × 쿠팡 파트너스 stats 매칭

흐름:
  1. 어제 publish_queue 항목 중 source="coupang" + keyword 가 있는 것만 추출
  2. 쿠팡 파트너스 sub-id-channel report 조회 (어제 1일치)
  3. publish_queue 의 platform 별 subId 정책으로 매칭
     - subId 가 platform 에 매핑되어 있으면 platform 별 합산
     - 키워드별 합산은 publish_queue 의 keyword 필드로 수행
  4. data/keyword_roi.json 에 누적 — {keyword: {clicks, orders, commission, last}}
  5. keyword_pool.json 의 각 항목에 roi 메타 병합 (옵션, ROI_PROPAGATE_POOL=true 일 때)

스케줄: SCHEDULE_ROI_AGGREGATE — 매일 아침/저녁 1회 권장

⚠️ 운영 초기에는 응답 스키마(쿠팡 reports API 의 정확한 키 이름)가 계정마다
다를 수 있어, 이 스크립트는 발견되는 키 후보를 모두 시도하고 매칭이
실패하면 raw 응답을 로그에 출력해 사용자가 매핑을 확정할 수 있게 한다.
"""
import json
import os
from datetime import date, timedelta

from dotenv import load_dotenv
load_dotenv()

from common.coupang_stats import fetch_daily_stats
from common.aliexpress_stats import fetch_yesterday_orders as fetch_ali_orders
from common.logger import log
from common.publish_queue import _load as _load_queue, DEFAULT_QUEUE_PATH


_BASE_DIR     = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROI_PATH      = os.path.join(_BASE_DIR, "data", "keyword_roi.json")
KEYWORD_POOL  = os.path.join(_BASE_DIR, "data", "keyword_pool.json")


SCHEDULE = {
    "env":  "SCHEDULE_ROI_AGGREGATE",
    "func": "run",
}


def _load_json(path: str, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _save_json(path: str, data) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _extract_int(row: dict, candidates: tuple) -> int:
    """row 의 후보 키 중 첫 번째로 발견되는 값을 int 로."""
    for k in candidates:
        v = row.get(k)
        if v is not None:
            try:
                return int(v)
            except (TypeError, ValueError):
                continue
    return 0


def _extract_str(row: dict, candidates: tuple) -> str:
    for k in candidates:
        v = row.get(k)
        if v:
            return str(v)
    return ""


def _row_to_normalized(row: dict) -> dict:
    """쿠팡 reports row → 정규화 dict.

    실제 쿠팡 응답 스키마는 운영 계정마다 미세 차이가 있어 키 후보를 다수
    시도. 매칭 0인 row 는 호출 측에서 raw 로그로 보고.
    """
    return {
        "sub_id":     _extract_str(row, ("subId", "sub_id", "subid")),
        "date":       _extract_str(row, ("date", "statDate", "reportDate")),
        "clicks":     _extract_int(row, ("click", "clicks", "clickCount")),
        "orders":     _extract_int(row, ("order", "orders", "orderCount")),
        "commission": _extract_int(row, ("commission", "totalCommission",
                                          "commissionAmount")),
    }


def _aggregate_aliexpress(yest_iso: str, queue: list, roi_db: dict) -> int:
    """알리 TOP API 의 어제 주문 → 키워드별 ROI 누적. 처리한 주문 수 반환.

    publish_queue 의 source=aliexpress 발행 키워드들과 어제 주문을 매칭.
    1차 — 어제 주문 총합을 어제 발행 키워드 수로 균등 분배 (쿠팡과 동일).
    추후 product_id 단위 정밀 매칭은 publish_queue 의 affiliate_url 패턴 확정 후.
    """
    ali_pubs = [
        it for it in queue
        if (it.get("source") or "") == "aliexpress"
        and (it.get("keyword") or "").strip()
        and (it.get("queued_at") or "").startswith(yest_iso)
    ]
    if not ali_pubs:
        return 0

    orders = fetch_ali_orders()
    if not orders:
        log("[ROI] 알리 주문 응답 없음 — TOP 자격 또는 어제 주문 0", "info")
        return 0

    total_orders = len(orders)
    total_paid = 0.0
    total_comm = 0.0
    for od in orders:
        try:
            total_paid += float(od.get("paid_amount", {}).get("amount") or
                                  od.get("paid_amount") or 0)
            total_comm += float(od.get("estimated_commission", {}).get("amount") or
                                  od.get("estimated_commission") or
                                  od.get("commission") or 0)
        except (TypeError, ValueError):
            continue

    n = len(ali_pubs)
    per_orders = total_orders // max(n, 1)
    per_comm   = int(total_comm) // max(n, 1)

    for it in ali_pubs:
        kw = it["keyword"]
        agg = roi_db.setdefault(kw, {
            "clicks": 0, "orders": 0, "commission": 0,
            "publishes": 0, "first_seen": yest_iso,
        })
        agg["orders"]     += per_orders
        agg["commission"] += per_comm
        agg["publishes"]  += 1
        agg["last"]        = yest_iso

    log(f"[ROI] 알리 어제 주문 {total_orders}건 / 커미션 {int(total_comm)} → "
        f"{n}개 키워드 분배", "ok")
    return total_orders


def run() -> None:
    """일일 ROI 집계 — 어제 1일치 (쿠팡 + 알리)."""
    yesterday = date.today() - timedelta(days=1)
    log(f"[ROI] 집계 시작 — 대상일 {yesterday}", "step")

    queue = _load_queue(DEFAULT_QUEUE_PATH)
    yest_iso = yesterday.isoformat()

    # 알리 ROI 도 같은 roi_db 에 누적 — 쿠팡 처리 직전에 미리 로드
    roi_db = _load_json(ROI_PATH, {})

    # ── 알리익스프레스 ROI ──────────────────────────────────────────────────
    ali_processed = _aggregate_aliexpress(yest_iso, queue, roi_db)

    # ── 쿠팡 파트너스 ROI ───────────────────────────────────────────────────
    coupang_pubs = [
        it for it in queue
        if (it.get("source") or "") == "coupang"
        and (it.get("keyword") or "").strip()
        and (it.get("queued_at") or "").startswith(yest_iso)
    ]
    log(f"[ROI] 어제 쿠팡 발행 후보: {len(coupang_pubs)}건", "info")
    if not coupang_pubs:
        log("[ROI] 어제 쿠팡 발행 없음 — 알리만 처리", "info")
        # 알리도 0 이면 진짜 종료, 아니면 알리 결과만 저장
        if ali_processed > 0:
            _save_json(ROI_PATH, roi_db)
        return

    # 2) 쿠팡 stats 조회
    raw = fetch_daily_stats(yesterday, yesterday)
    if not raw:
        log("[ROI] 쿠팡 stats 응답 없음 — 자격/API 미적용 가능. 종료", "warn")
        return

    rows = [_row_to_normalized(r) for r in raw]
    matched_any = any(r["clicks"] or r["orders"] or r["commission"] for r in rows)
    if not matched_any:
        log("[ROI] 정규화 결과 모두 0 — 응답 스키마 불일치 가능. raw 샘플:", "warn")
        for r in raw[:3]:
            log(f"  {json.dumps(r, ensure_ascii=False)}", "info")
        return

    # 3) subId 별 합산 (publish_queue 항목과 platform/subId 매칭은 차후 정교화)
    by_sub = {}
    for r in rows:
        sub = r["sub_id"] or "(unknown)"
        b = by_sub.setdefault(sub, {"clicks": 0, "orders": 0, "commission": 0})
        b["clicks"]     += r["clicks"]
        b["orders"]     += r["orders"]
        b["commission"] += r["commission"]

    # 4) 키워드별 누적 (현재는 어제 발행 키워드 전체를 평균 분배 — subId/URL
    #    매칭 정밀화는 publish_queue 의 affiliate_url 패턴이 확정되면 적용)
    total_clicks = sum(b["clicks"] for b in by_sub.values())
    total_orders = sum(b["orders"] for b in by_sub.values())
    total_comm   = sum(b["commission"] for b in by_sub.values())
    n = len(coupang_pubs)
    per = {
        "clicks":     total_clicks // max(n, 1),
        "orders":     total_orders // max(n, 1),
        "commission": total_comm // max(n, 1),
    }

    for it in coupang_pubs:
        kw = it["keyword"]
        agg = roi_db.setdefault(kw, {
            "clicks": 0, "orders": 0, "commission": 0,
            "publishes": 0, "first_seen": yest_iso,
        })
        agg["clicks"]     += per["clicks"]
        agg["orders"]     += per["orders"]
        agg["commission"] += per["commission"]
        agg["publishes"]  += 1
        agg["last"]        = yest_iso
    _save_json(ROI_PATH, roi_db)

    log(f"[ROI] 키워드 ROI 갱신: 총 클릭 {total_clicks}, 주문 {total_orders}, "
        f"수수료 {total_comm} → {n}개 키워드에 분배", "ok")

    # 5) 옵션 — keyword_pool.json 에 roi 메타 병합
    if os.getenv("ROI_PROPAGATE_POOL", "false").lower() == "true":
        pool = _load_json(KEYWORD_POOL, {})
        kws = pool.get("keywords", [])
        updated = 0
        for item in kws:
            kw = item.get("keyword", "")
            if kw in roi_db:
                item["roi"] = dict(roi_db[kw])
                updated += 1
        if updated:
            _save_json(KEYWORD_POOL, pool)
            log(f"[ROI] keyword_pool 메타 병합: {updated}개 항목", "info")


if __name__ == "__main__":
    run()
