"""
Auto-Publishing 운영 대시보드 (Streamlit)

실행:
    pip install streamlit pandas
    streamlit run tools/dashboard.py

표시 항목:
    - 헤더 카드: 오늘 발행 / 주간 발행 / 풀 잔여 / ROI 누적 수수료
    - publish_queue 최근 30건 (색인/백링크 상태)
    - 키워드 풀 분포 (소스별 / rank_change / 카테고리)
    - 스케줄러 등록 현황 + 다음 실행 시간
    - ROI 상위 키워드 TOP10

데이터 출처는 모두 data/*.json — 라이브 발행 영향 없음.
"""
import json
import os
import sys
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path

import streamlit as st
import pandas as pd

# 프로젝트 루트를 sys.path 에 추가 — `streamlit run` 으로 실행 시 cwd 와 무관하게 import
_BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BASE_DIR))

from dotenv import load_dotenv
load_dotenv(_BASE_DIR / ".env")


# ─── 데이터 로더 ────────────────────────────────────────────────────────────

DATA = _BASE_DIR / "data"


def _load(path: Path, default):
    if not path.exists():
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


@st.cache_data(ttl=30)
def load_queue() -> list:
    return _load(DATA / "publish_queue.json", [])


@st.cache_data(ttl=30)
def load_pool() -> dict:
    return _load(DATA / "keyword_pool.json", {"keywords": [], "total": 0})


@st.cache_data(ttl=30)
def load_used() -> dict:
    return _load(DATA / "used_keywords.json", {})


@st.cache_data(ttl=30)
def load_roi() -> dict:
    return _load(DATA / "keyword_roi.json", {})


@st.cache_data(ttl=300)
def load_schedules() -> list:
    """pipelines/ 모듈 스캔 → SCHEDULE 메타 + 현재 .env 값 매핑."""
    import importlib
    import pkgutil
    import pipelines as _pkg
    rows = []
    for _, name, _ in pkgutil.iter_modules(_pkg.__path__):
        if name.startswith("_") or name == "scheduler_runner":
            continue
        try:
            m = importlib.import_module(f"pipelines.{name}")
            s = getattr(m, "SCHEDULE", None)
            if not s or "env" not in s:
                continue
            env_key = s["env"]
            times_str = os.getenv(env_key, "").strip()
            rows.append({
                "module":    name,
                "env":       env_key,
                "times":     times_str or "(비활성)",
                "active":    bool(times_str),
            })
        except Exception:
            pass
    rows.sort(key=lambda r: (not r["active"], r["times"], r["module"]))
    return rows


# ─── 페이지 설정 ─────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="Auto-Publishing Dashboard",
    page_icon="📊",
    layout="wide",
)

st.title("📊 Auto-Publishing Dashboard")
st.caption(f"마지막 새로고침: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

queue = load_queue()
pool  = load_pool()
used  = load_used()
roi   = load_roi()


# ─── 헤더 카드 ───────────────────────────────────────────────────────────────

today_iso     = date.today().isoformat()
week_cutoff   = (date.today() - timedelta(days=7)).isoformat()

today_pubs = sum(1 for it in queue
                  if (it.get("queued_at") or "").startswith(today_iso))
week_pubs  = sum(1 for it in queue
                  if (it.get("queued_at") or "")[:10] >= week_cutoff)

pool_total     = pool.get("total", 0) or len(pool.get("keywords", []))
pool_used      = len(used)
pool_available = pool_total - pool_used

roi_total_comm   = sum(v.get("commission", 0) for v in roi.values())
roi_total_clicks = sum(v.get("clicks", 0) for v in roi.values())

c1, c2, c3, c4 = st.columns(4)
c1.metric("오늘 발행", today_pubs)
c2.metric("주간 발행 (7일)", week_pubs)
c3.metric("풀 잔여 / 총", f"{pool_available:,} / {pool_total:,}")
c4.metric("ROI 누적 수수료", f"{roi_total_comm:,}원",
          delta=f"클릭 {roi_total_clicks:,}" if roi_total_clicks else None)

st.divider()


# ─── 최근 publish_queue ─────────────────────────────────────────────────────

st.subheader("최근 발행 (publish_queue)")
if queue:
    recent = sorted(queue, key=lambda it: it.get("queued_at", ""), reverse=True)[:30]
    df = pd.DataFrame([{
        "발행시각":  it.get("queued_at", ""),
        "플랫폼":    it.get("platform", ""),
        "소스":      it.get("source", "") or "-",
        "키워드":    it.get("keyword", "") or "-",
        "Google":   it.get("google_indexed", "X"),
        "Naver":    it.get("naver_indexed", "X"),
        "백링크":    it.get("backlinked", "X"),
        "URL":      it.get("url", ""),
    } for it in recent])
    st.dataframe(df, use_container_width=True, hide_index=True,
                  column_config={"URL": st.column_config.LinkColumn("URL")})
else:
    st.info("publish_queue 가 비어있습니다.")

st.divider()


# ─── 풀 분포 ─────────────────────────────────────────────────────────────────

st.subheader("키워드 풀 분포")
col_a, col_b = st.columns(2)

with col_a:
    st.caption("소스별")
    src_counts = Counter((k.get("source") or "unknown")
                          for k in pool.get("keywords", []))
    if src_counts:
        st.bar_chart(pd.DataFrame.from_dict(src_counts, orient="index",
                                             columns=["count"]))
    else:
        st.info("풀이 비어있습니다.")

with col_b:
    st.caption("rank_change (트렌드)")
    rc_counts = Counter((k.get("rank_change") or "").lower() or "none"
                         for k in pool.get("keywords", []))
    if rc_counts:
        rc_df = pd.DataFrame.from_dict(rc_counts, orient="index",
                                        columns=["count"])
        st.bar_chart(rc_df)

st.divider()


# ─── ROI 상위 키워드 ─────────────────────────────────────────────────────────

st.subheader("ROI 상위 키워드 (TOP 10)")
if roi:
    rows = [{
        "키워드":      kw,
        "수수료":      v.get("commission", 0),
        "주문":        v.get("orders", 0),
        "클릭":        v.get("clicks", 0),
        "발행수":      v.get("publishes", 0),
        "마지막":      v.get("last", ""),
    } for kw, v in roi.items()]
    rows.sort(key=lambda r: (-r["수수료"], -r["클릭"]))
    st.dataframe(pd.DataFrame(rows[:10]), use_container_width=True,
                  hide_index=True)
else:
    st.info("ROI 데이터 없음 (운영 후 누적되면 표시).")

st.divider()


# ─── 스케줄러 ────────────────────────────────────────────────────────────────

st.subheader("스케줄러 등록 현황")
schedules = load_schedules()
active = [s for s in schedules if s["active"]]
inactive = [s for s in schedules if not s["active"]]

st.caption(f"활성 {len(active)}개 / 전체 {len(schedules)}개")
if active:
    st.dataframe(pd.DataFrame(active)[["times", "module", "env"]],
                  use_container_width=True, hide_index=True)

with st.expander(f"비활성 {len(inactive)}개 보기"):
    if inactive:
        st.dataframe(pd.DataFrame(inactive)[["module", "env"]],
                      use_container_width=True, hide_index=True)


# ─── 풋터 ────────────────────────────────────────────────────────────────────

st.divider()
st.caption(
    "데이터 출처: data/*.json • 캐시 30s • "
    "발행 라이브 영향 없음 (read-only)"
)
