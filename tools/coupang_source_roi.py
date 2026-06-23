"""쿠팡 소스별(goldbox/bestcategory/keyword) 전환 분석.

발행 링크의 subId 접미사(-gb/-bc/-kw)로 소스를 구분해 clicks/orders/commission/
전환율을 집계한다. sources/coupang.py 의 _tagged_subid 와 짝을 이룬다.

⚠️ 데이터는 쿠팡 파트너스 reports(clicks/orders/commission) 에서 온다. 발행 직후
  에는 클릭/주문이 없어 빈 결과가 정상이며, 며칠 운영 후 의미가 생긴다.

사용:
  python -m tools.coupang_source_roi            # 어제까지 7일
  python -m tools.coupang_source_roi --days 14
"""
import sys
import argparse
from datetime import date, timedelta

sys.stdout.reconfigure(encoding="utf-8")

from dotenv import load_dotenv
load_dotenv()

from common.coupang_stats import fetch_daily_stats


_SRC_LABEL = {"gb": "goldbox(특가)", "bc": "bestcategory(베셀)", "kw": "keyword(검색)"}


def _source_of(sub_id: str) -> str:
    """subId 접미사 → 소스 태그. 접미사 없는 레거시/크롤 링크는 keyword 로 본다."""
    for tag in ("gb", "bc", "kw"):
        if (sub_id or "").endswith("-" + tag):
            return tag
    return "kw"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=7, help="어제부터 거슬러 집계할 일수")
    args = ap.parse_args()

    end   = date.today() - timedelta(days=1)        # 당일은 미집계 → 어제까지
    start = end - timedelta(days=max(args.days, 1) - 1)
    rows  = fetch_daily_stats(start, end)

    print(f"\n쿠팡 소스별 전환 — {start} ~ {end} ({args.days}일)\n")
    if not rows:
        print("데이터 없음 — 아직 클릭/주문이 없거나(배포 직후), 자격 미설정.")
        print("며칠 운영 후 다시 실행하세요.")
        return

    agg: dict = {}
    for r in rows:
        src = _source_of(r.get("subId", ""))
        a = agg.setdefault(src, {"clicks": 0, "orders": 0, "commission": 0, "gmv": 0})
        for k in ("clicks", "orders", "commission", "gmv"):
            a[k] += int(r.get(k, 0) or 0)

    header = f"{'소스':<18}{'클릭':>8}{'주문':>7}{'수수료':>11}{'전환율':>9}{'클릭당수수료':>13}"
    print(header)
    print("-" * 64)
    total = {"clicks": 0, "orders": 0, "commission": 0}
    for tag in ("gb", "bc", "kw"):
        a = agg.get(tag)
        if not a:
            continue
        cr  = (a["orders"] / a["clicks"] * 100) if a["clicks"] else 0.0
        cpc = (a["commission"] / a["clicks"]) if a["clicks"] else 0.0
        print(f"{_SRC_LABEL[tag]:<18}{a['clicks']:>8}{a['orders']:>7}"
              f"{a['commission']:>11,}{cr:>8.1f}%{cpc:>12,.0f}")
        for k in total:
            total[k] += a[k]

    cr = (total["orders"] / total["clicks"] * 100) if total["clicks"] else 0.0
    print("-" * 64)
    print(f"{'합계':<18}{total['clicks']:>8}{total['orders']:>7}"
          f"{total['commission']:>11,}{cr:>8.1f}%")
    print("\n해석: 전환율=주문/클릭, 클릭당수수료=수수료/클릭. 둘 다 높은 소스에"
          " 비중(.env 의 COUPANG_*_RATIO)을 더 주면 됨.")


if __name__ == "__main__":
    main()
