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


def _build_report(days: int) -> str:
    """소스별 전환 리포트 텍스트 생성."""
    end   = date.today() - timedelta(days=1)        # 당일은 미집계 → 어제까지
    start = end - timedelta(days=max(days, 1) - 1)
    rows  = fetch_daily_stats(start, end)

    out = [f"쿠팡 소스별 전환 — {start} ~ {end} ({days}일)", ""]
    if not rows:
        out.append("데이터 없음 — 아직 클릭/주문이 없거나(배포 직후), 자격 미설정.")
        out.append("며칠 더 운영 후 다시 실행하세요.")
        return "\n".join(out)

    agg: dict = {}
    for r in rows:
        src = _source_of(r.get("subId", ""))
        a = agg.setdefault(src, {"clicks": 0, "orders": 0, "commission": 0, "gmv": 0})
        for k in ("clicks", "orders", "commission", "gmv"):
            a[k] += int(r.get(k, 0) or 0)

    total = {"clicks": 0, "orders": 0, "commission": 0}
    out.append(f"{'소스':<16}{'클릭':>7}{'주문':>6}{'수수료':>10}{'전환율':>8}")
    out.append("-" * 48)
    for tag in ("gb", "bc", "kw"):
        a = agg.get(tag)
        if not a:
            continue
        cr = (a["orders"] / a["clicks"] * 100) if a["clicks"] else 0.0
        out.append(f"{_SRC_LABEL[tag]:<16}{a['clicks']:>7}{a['orders']:>6}"
                   f"{a['commission']:>10,}{cr:>7.1f}%")
        for k in total:
            total[k] += a[k]
    cr = (total["orders"] / total["clicks"] * 100) if total["clicks"] else 0.0
    out.append("-" * 48)
    out.append(f"{'합계':<16}{total['clicks']:>7}{total['orders']:>6}"
               f"{total['commission']:>10,}{cr:>7.1f}%")
    out.append("")
    out.append("해석: 전환율=주문/클릭. 전환율·수수료 높은 소스에 비중"
               "(.env COUPANG_*_RATIO)을 더 주면 됨.")
    return "\n".join(out)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=7, help="어제부터 거슬러 집계할 일수")
    ap.add_argument("--telegram", action="store_true",
                    help="결과를 텔레그램으로도 전송 (예약 작업용)")
    args = ap.parse_args()

    report = _build_report(args.days)
    print("\n" + report)

    if args.telegram:
        from common.notifier import _send_telegram
        ok = _send_telegram("📊 " + report)
        print("\n[텔레그램 전송]", "성공" if ok else "실패(토큰/CHAT_ID 확인)")


if __name__ == "__main__":
    main()
