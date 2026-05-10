"""
일일 운영 요약 — 매일 정해진 시간에 텔레그램으로 발송

내용:
  - 오늘 채널별 발행 수
  - 풀 현황 (잔여 / 회전된 키워드)
  - 색인/백링크 상태
  - ROI 어제 누적 (있으면)
  - 활성 스케줄 다음 실행 시간

스케줄: SCHEDULE_DAILY_SUMMARY=21:30  # .env

데이터 출처는 data/*.json 만 — 라이브 발행 영향 없음.
"""
import json
import os
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

from common.logger import log
from common.notifier import _send_telegram


_BASE_DIR = Path(__file__).resolve().parent.parent
DATA = _BASE_DIR / "data"


SCHEDULE = {
    "env":  "SCHEDULE_DAILY_SUMMARY",
    "func": "run",
}


def _load(path: Path, default):
    if not path.exists():
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _today_publishes(queue: list, today_iso: str) -> dict:
    """오늘 발행을 plat × source 별로 집계."""
    plat_counts: Counter = Counter()
    src_counts: Counter = Counter()
    for it in queue:
        if not (it.get("queued_at") or "").startswith(today_iso):
            continue
        plat_counts[it.get("platform", "unknown")] += 1
        src = it.get("source") or ""
        if src:
            src_counts[src] += 1
    return {"by_platform": plat_counts, "by_source": src_counts}


def _index_status(queue: list, today_iso: str) -> dict:
    """오늘 발행분의 색인/백링크 진행 상태."""
    today_pubs = [it for it in queue
                   if (it.get("queued_at") or "").startswith(today_iso)]
    return {
        "total":   len(today_pubs),
        "google":  sum(1 for it in today_pubs if it.get("google_indexed") == "O"),
        "naver":   sum(1 for it in today_pubs if it.get("naver_indexed") == "O"),
        "back":    sum(1 for it in today_pubs if it.get("backlinked") == "O"),
    }


def _yesterday_roi_summary(roi: dict) -> dict:
    """어제 last 갱신된 키워드 합계."""
    yesterday_iso = (date.today() - timedelta(days=1)).isoformat()
    rows = [v for v in roi.values()
             if (v.get("last") or "").startswith(yesterday_iso)]
    return {
        "keywords":   len(rows),
        "clicks":     sum(r.get("clicks", 0) for r in rows),
        "orders":     sum(r.get("orders", 0) for r in rows),
        "commission": sum(r.get("commission", 0) for r in rows),
    }


def _next_schedule_runs(top_n: int = 5) -> list:
    """현재 .env 기준 등록 가능 스케줄 → 다음 실행 시각이 가까운 순."""
    import importlib
    import pkgutil
    import pipelines as _pkg

    rows = []
    now = datetime.now()
    for _, name, _ in pkgutil.iter_modules(_pkg.__path__):
        if name.startswith("_") or name == "scheduler_runner":
            continue
        try:
            m = importlib.import_module(f"pipelines.{name}")
            s = getattr(m, "SCHEDULE", None)
            if not s or "env" not in s:
                continue
            times_str = os.getenv(s["env"], "").strip()
            if not times_str:
                continue
            for t in times_str.split(","):
                t = t.strip()
                if not t:
                    continue
                try:
                    h, mn = map(int, t.split(":"))
                except ValueError:
                    continue
                run_at = now.replace(hour=h, minute=mn, second=0, microsecond=0)
                if run_at <= now:
                    run_at = run_at + timedelta(days=1)
                rows.append({
                    "module": name,
                    "time":   t,
                    "next":   run_at,
                })
        except Exception:
            pass
    rows.sort(key=lambda r: r["next"])
    return rows[:top_n]


def build_summary() -> str:
    """텔레그램 메시지 텍스트 구성. 라인당 1개 정보, 80자 이내."""
    today_iso = date.today().isoformat()

    queue = _load(DATA / "publish_queue.json", [])
    pool  = _load(DATA / "keyword_pool.json", {"total": 0, "keywords": []})
    used  = _load(DATA / "used_keywords.json", {})
    roi   = _load(DATA / "keyword_roi.json", {})

    pubs   = _today_publishes(queue, today_iso)
    idxst  = _index_status(queue, today_iso)
    pool_t = pool.get("total", 0) or len(pool.get("keywords", []))
    pool_a = pool_t - len(used)

    lines = [f"📊 일일 요약 — {today_iso}"]

    # 1) 발행 요약
    today_total = sum(pubs["by_platform"].values())
    if today_total:
        plat_str = ", ".join(f"{k} {v}" for k, v in
                              pubs["by_platform"].most_common())
        lines.append(f"• 오늘 발행: {today_total}건 ({plat_str})")
        if pubs["by_source"]:
            src_str = ", ".join(f"{k} {v}" for k, v in
                                  pubs["by_source"].most_common())
            lines.append(f"  └ 소스별: {src_str}")
    else:
        lines.append("• 오늘 발행: 0건")

    # 2) 색인/백링크 (오늘 발행 기준)
    if idxst["total"]:
        lines.append(
            f"• 색인/백링크: G {idxst['google']}/{idxst['total']} • "
            f"N {idxst['naver']}/{idxst['total']} • "
            f"BL {idxst['back']}/{idxst['total']}"
        )

    # 3) 풀 현황
    pct = round(pool_a / pool_t * 100, 1) if pool_t else 0
    lines.append(f"• 풀 잔여: {pool_a:,} / {pool_t:,} ({pct}%)")

    # 4) 어제 ROI
    yroi = _yesterday_roi_summary(roi)
    if yroi["keywords"]:
        lines.append(
            f"• 어제 ROI: 키워드 {yroi['keywords']}개 • "
            f"클릭 {yroi['clicks']:,} • 주문 {yroi['orders']} • "
            f"수수료 {yroi['commission']:,}원"
        )

    # 5) 다음 스케줄 (3개)
    nx = _next_schedule_runs(top_n=3)
    if nx:
        lines.append("• 다음 실행:")
        for r in nx:
            lines.append(f"  └ {r['time']} {r['module']}")

    return "\n".join(lines)


def run() -> None:
    """일일 요약 발송."""
    text = build_summary()
    log("[일일 요약] 메시지:\n" + text, "info")

    if not os.getenv("TELEGRAM_BOT_TOKEN") or not os.getenv("TELEGRAM_CHAT_ID"):
        log("[일일 요약] TELEGRAM 자격 미설정 — 발송 생략", "warn")
        return

    ok = _send_telegram(text)
    if ok:
        log("[일일 요약] 텔레그램 발송 완료", "ok")
    else:
        log("[일일 요약] 텔레그램 발송 실패", "warn")


if __name__ == "__main__":
    run()
