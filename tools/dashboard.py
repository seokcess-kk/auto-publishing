"""
Auto-Publishing 운영 대시보드 (Streamlit)

실행:
    pip install streamlit pandas
    streamlit run tools/dashboard.py

구조:
    - 사이드바: 필터(기간/채널/소스), 데이터 갱신 시각, 자동 새로고침 안내
    - 헤더 카드 4개: 오늘/주간 발행 + delta, 풀 잔여, ROI 누적
    - 발행 표: 상대시간 + 상태 뱃지 + 키워드/소스 placeholder
    - 풀 분포: 소스/rank_change/카테고리 (3 컬럼)
    - 색인/백링크 진척도: 막대 + 카운트
    - ROI: 쿠팡/알리 분리 + TOP10 키워드
    - 스케줄러: 다음 실행 시간 정렬 표

데이터 출처는 모두 data/*.json — 라이브 발행 영향 없음 (read-only).
"""
import json
import os
import sys
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path

import streamlit as st
import pandas as pd

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


def _file_mtime(path: Path) -> str:
    if not path.exists():
        return "(없음)"
    ts = datetime.fromtimestamp(path.stat().st_mtime)
    return ts.strftime("%Y-%m-%d %H:%M")


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


@st.cache_data(ttl=30)
def load_backlink() -> dict:
    return _load(DATA / "backlink_state.json", {})


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


# ─── 시간 표시 헬퍼 ─────────────────────────────────────────────────────────

def _format_relative_time(iso_ts: str) -> str:
    """ISO timestamp → 사람이 읽기 쉬운 짧은 표현."""
    if not iso_ts:
        return "—"
    try:
        ts = datetime.fromisoformat(iso_ts.split("+")[0])
    except Exception:
        return iso_ts[:16]

    now = datetime.now()
    delta = now - ts

    if delta.total_seconds() < 60:
        return "방금"
    if delta.total_seconds() < 3600:
        return f"{int(delta.total_seconds() // 60)}분 전"
    if delta.days == 0:
        return ts.strftime("%H:%M")
    if delta.days == 1:
        return f"어제 {ts.strftime('%H:%M')}"
    if delta.days < 7:
        return f"{delta.days}일 전 {ts.strftime('%H:%M')}"
    return ts.strftime("%m/%d %H:%M")


def _placeholder(value: str) -> str:
    """빈 값 → 회색 대시 placeholder."""
    return value if value else "—"


# ─── 페이지 설정 + 사이드바 ─────────────────────────────────────────────────

st.set_page_config(
    page_title="Auto-Publishing Dashboard",
    page_icon="📊",
    layout="wide",
)

queue = load_queue()
pool  = load_pool()
used  = load_used()
roi   = load_roi()
backlink = load_backlink()

# ── 사이드바 ────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("### 🗂️ 데이터 갱신 시각")
    st.caption(f"publish_queue: {_file_mtime(DATA / 'publish_queue.json')}")
    st.caption(f"keyword_pool:  {_file_mtime(DATA / 'keyword_pool.json')}")
    st.caption(f"used_keywords: {_file_mtime(DATA / 'used_keywords.json')}")
    st.caption(f"keyword_roi:   {_file_mtime(DATA / 'keyword_roi.json')}")
    st.divider()

    st.markdown("### 🔍 필터")
    # 기간
    period = st.selectbox(
        "기간",
        ["오늘", "최근 7일", "최근 30일", "전체"],
        index=1,
    )
    # 플랫폼
    all_platforms = sorted({(it.get("platform") or "?") for it in queue})
    sel_platforms = st.multiselect("플랫폼", all_platforms,
                                    default=all_platforms)
    # 소스
    all_sources = sorted({(it.get("source") or "(미정)") for it in queue})
    sel_sources = st.multiselect("소스", all_sources, default=all_sources)
    st.divider()

    st.markdown("### 🔄 자동 새로고침")
    st.caption("브라우저에서 **Ctrl+R** 또는 메뉴 → Rerun")
    st.caption("캐시 TTL: 30초")


# ── 필터 적용 ────────────────────────────────────────────────────────────────

today_iso     = date.today().isoformat()
week_cutoff   = (date.today() - timedelta(days=7)).isoformat()
month_cutoff  = (date.today() - timedelta(days=30)).isoformat()
yesterday_iso = (date.today() - timedelta(days=1)).isoformat()
prev_week_cutoff = (date.today() - timedelta(days=14)).isoformat()


def _date_predicate(period_label: str):
    if period_label == "오늘":
        return lambda iso: (iso or "").startswith(today_iso)
    if period_label == "최근 7일":
        return lambda iso: (iso or "")[:10] >= week_cutoff
    if period_label == "최근 30일":
        return lambda iso: (iso or "")[:10] >= month_cutoff
    return lambda iso: True


_in_period = _date_predicate(period)
filtered_queue = [
    it for it in queue
    if _in_period(it.get("queued_at", ""))
    and (it.get("platform") or "?") in sel_platforms
    and (it.get("source") or "(미정)") in sel_sources
]


# ─── 헤더 ────────────────────────────────────────────────────────────────────

st.title("📊 Auto-Publishing Dashboard")
st.caption(f"마지막 새로고침: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} • "
           f"필터: {period} / 플랫폼 {len(sel_platforms)}/{len(all_platforms)} / "
           f"소스 {len(sel_sources)}/{len(all_sources)}")


# ─── 헤더 카드 (4개 + delta) ─────────────────────────────────────────────────

today_pubs = sum(1 for it in queue
                  if (it.get("queued_at") or "").startswith(today_iso))
yesterday_pubs = sum(1 for it in queue
                      if (it.get("queued_at") or "").startswith(yesterday_iso))
week_pubs  = sum(1 for it in queue
                  if (it.get("queued_at") or "")[:10] >= week_cutoff)
prev_week_pubs = sum(1 for it in queue
                      if prev_week_cutoff <= (it.get("queued_at") or "")[:10] < week_cutoff)

pool_total     = pool.get("total", 0) or len(pool.get("keywords", []))
pool_used      = len(used)
pool_available = pool_total - pool_used
pool_pct       = round(pool_available / pool_total * 100, 1) if pool_total else 0

roi_total_comm   = sum(v.get("commission", 0) for v in roi.values())
roi_total_orders = sum(v.get("orders", 0) for v in roi.values())
roi_total_clicks = sum(v.get("clicks", 0) for v in roi.values())

c1, c2, c3, c4 = st.columns(4)
c1.metric("오늘 발행", today_pubs,
          delta=(today_pubs - yesterday_pubs) if yesterday_pubs else None,
          delta_color="normal")
c2.metric("주간 발행 (7일)", week_pubs,
          delta=(week_pubs - prev_week_pubs) if prev_week_pubs else None,
          delta_color="normal")
c3.metric("풀 잔여", f"{pool_available:,}",
          delta=f"{pool_pct}% / 총 {pool_total:,}",
          delta_color="off")
c4.metric("ROI 누적 수수료", f"{roi_total_comm:,}원",
          delta=(f"클릭 {roi_total_clicks:,} • 주문 {roi_total_orders:,}"
                  if (roi_total_clicks or roi_total_orders) else "데이터 누적 대기"),
          delta_color="off")

st.divider()


# ─── 발행 표 (상대시간 + 상태 뱃지) ──────────────────────────────────────────

st.subheader(f"발행 내역 ({len(filtered_queue)}건)")

if filtered_queue:
    recent = sorted(filtered_queue,
                    key=lambda it: it.get("queued_at", ""),
                    reverse=True)[:50]

    df = pd.DataFrame([{
        "발행시각":  _format_relative_time(it.get("queued_at", "")),
        "플랫폼":    it.get("platform", ""),
        "소스":      _placeholder(it.get("source", "")),
        "키워드":    _placeholder(it.get("keyword", "")),
        "Google":    "🟢" if it.get("google_indexed") == "O" else "⚪",
        "Naver":     "🟢" if it.get("naver_indexed") == "O" else "⚪",
        "백링크":    "🟢" if it.get("backlinked") == "O" else "⚪",
        "URL":       it.get("url", ""),
    } for it in recent])

    st.dataframe(
        df,
        use_container_width=True,
        hide_index=True,
        column_config={
            "URL": st.column_config.LinkColumn("URL", width="medium"),
            "발행시각": st.column_config.TextColumn("발행시각", width="small"),
            "Google":  st.column_config.TextColumn("G",  width="small",
                                                    help="Google Indexing API 색인 완료"),
            "Naver":   st.column_config.TextColumn("N",  width="small",
                                                    help="Naver Search Advisor 색인 완료"),
            "백링크":  st.column_config.TextColumn("BL", width="small",
                                                    help="SNS 백링크 발행 완료"),
        },
    )
else:
    st.info("선택한 필터에 해당하는 발행 내역이 없습니다.")

st.divider()


# ─── 색인/백링크 진척도 ──────────────────────────────────────────────────────

st.subheader("색인/백링크 진척도")

if filtered_queue:
    g_done = sum(1 for it in filtered_queue if it.get("google_indexed") == "O")
    n_done = sum(1 for it in filtered_queue if it.get("naver_indexed") == "O")
    b_done = sum(1 for it in filtered_queue if it.get("backlinked") == "O")
    total  = len(filtered_queue)

    pcol1, pcol2, pcol3 = st.columns(3)
    pcol1.metric("Google 색인", f"{g_done}/{total}",
                  delta=f"{round(g_done/total*100, 1)}%" if total else "0%",
                  delta_color="off")
    pcol1.progress(g_done / total if total else 0)

    pcol2.metric("Naver 색인", f"{n_done}/{total}",
                  delta=f"{round(n_done/total*100, 1)}%" if total else "0%",
                  delta_color="off")
    pcol2.progress(n_done / total if total else 0)

    pcol3.metric("백링크", f"{b_done}/{total}",
                  delta=f"{round(b_done/total*100, 1)}%" if total else "0%",
                  delta_color="off")
    pcol3.progress(b_done / total if total else 0)

st.divider()


# ─── 풀 분포 (3컬럼) ────────────────────────────────────────────────────────

st.subheader("키워드 풀 분포")
col_a, col_b, col_c = st.columns(3)

with col_a:
    st.caption("소스별")
    src_counts = Counter((k.get("source") or "unknown")
                          for k in pool.get("keywords", []))
    if src_counts:
        st.bar_chart(pd.DataFrame.from_dict(src_counts, orient="index",
                                             columns=["count"]))
    else:
        st.info("풀 비어있음")

with col_b:
    st.caption("rank_change (트렌드)")
    rc_counts = Counter((k.get("rank_change") or "").lower() or "none"
                         for k in pool.get("keywords", []))
    if rc_counts:
        st.bar_chart(pd.DataFrame.from_dict(rc_counts, orient="index",
                                             columns=["count"]))

with col_c:
    st.caption("카테고리 TOP 8")
    cat_counts = Counter((k.get("category") or "기타")
                          for k in pool.get("keywords", []))
    top8 = dict(cat_counts.most_common(8))
    if top8:
        st.bar_chart(pd.DataFrame.from_dict(top8, orient="index",
                                             columns=["count"]))

st.divider()


# ─── ROI ─────────────────────────────────────────────────────────────────────

st.subheader("ROI 키워드 TOP 10")

if roi:
    # 쿠팡/알리 합계
    rcol1, rcol2 = st.columns(2)
    coupang_kws = [v for v in roi.values() if v.get("clicks", 0) > 0]
    ali_kws     = [v for v in roi.values() if v.get("orders", 0) > 0
                   and v.get("clicks", 0) == 0]
    rcol1.metric("쿠팡 — 데이터 보유 키워드",
                  len(coupang_kws),
                  delta=f"수수료 {sum(v.get('commission', 0) for v in coupang_kws):,}원",
                  delta_color="off")
    rcol2.metric("알리 — 데이터 보유 키워드",
                  len(ali_kws),
                  delta=f"수수료 {sum(v.get('commission', 0) for v in ali_kws):,}원",
                  delta_color="off")

    rows = [{
        "키워드":  kw,
        "수수료":  v.get("commission", 0),
        "주문":    v.get("orders", 0),
        "클릭":    v.get("clicks", 0),
        "발행":    v.get("publishes", 0),
        "마지막":  v.get("last", ""),
    } for kw, v in roi.items()]
    rows.sort(key=lambda r: (-r["수수료"], -r["클릭"], -r["주문"]))
    st.dataframe(pd.DataFrame(rows[:10]),
                  use_container_width=True, hide_index=True)
else:
    st.info("ROI 데이터 없음 — 쿠팡 stats 또는 알리 TOP API 자격 채워지면 자동 누적.")

st.divider()


# ─── 백링크 풀 ───────────────────────────────────────────────────────────────

if backlink:
    bl_total = len(backlink)
    bl_posted = {}
    for url, rec in backlink.items():
        for plat, info in (rec.get("platforms") or {}).items():
            if info.get("status") == "ok":
                bl_posted[plat] = bl_posted.get(plat, 0) + 1

    st.subheader(f"백링크 풀 ({bl_total}건 수집)")
    if bl_posted:
        st.caption("플랫폼별 발행 완료:")
        bcol = st.columns(len(bl_posted))
        for i, (plat, n) in enumerate(bl_posted.items()):
            bcol[i].metric(plat, n)
    else:
        st.caption("아직 SNS 발행 이력 없음")
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
    f"데이터 출처: data/*.json • 캐시 TTL 30초 • read-only • "
    f"필터 결과 {len(filtered_queue)}/{len(queue)}건"
)
