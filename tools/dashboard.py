"""
Auto-Publishing 운영 대시보드 (Streamlit)

실행:
    pip install streamlit pandas streamlit-autorefresh
    streamlit run tools/dashboard.py

기능:
    - 사이드바: 자동 새로고침 토글, 기간/플랫폼/소스 필터, 빠른 필터
      ("문제만 보기"), 데이터 파일 갱신 시각
    - 헤더 카드 4개 + delta (오늘 vs 어제, 주간 vs 지난주)
    - 발행 표: 상대시간 + 상태 뱃지 + 실패 사유 + 키워드/소스 placeholder
    - 색인/백링크 진척도 + 빈 상태 안내 (자격 채우기 가이드)
    - 풀 분포 + ROI(쿠팡/알리 분리) + 백링크 풀 + 스케줄러

좁은 화면(<900px) 에선 컬럼이 자동 세로 적층 (Streamlit 기본 반응형).
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


@st.cache_data(ttl=15)
def load_queue() -> list:
    return _load(DATA / "publish_queue.json", [])


@st.cache_data(ttl=15)
def load_pool() -> dict:
    return _load(DATA / "keyword_pool.json", {"keywords": [], "total": 0})


@st.cache_data(ttl=15)
def load_used() -> dict:
    return _load(DATA / "used_keywords.json", {})


@st.cache_data(ttl=15)
def load_roi() -> dict:
    return _load(DATA / "keyword_roi.json", {})


@st.cache_data(ttl=15)
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


# ─── 시간/플레이스홀더/상태 헬퍼 ────────────────────────────────────────────

def _format_relative_time(iso_ts: str) -> str:
    if not iso_ts:
        return "—"
    try:
        ts = datetime.fromisoformat(iso_ts.split("+")[0])
    except Exception:
        return iso_ts[:16]
    delta = datetime.now() - ts
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
    return value if value else "—"


_STATUS_LABEL = {
    "ok":            "완료",
    "limit":         "한도 초과",
    "no_permission": "권한 없음",
    "error":         "오류",
}


def _status_badge(item: dict, field: str) -> str:
    """{field} 결과를 이모지+짧은 사유로 표시.

    field 자체는 X/O 만 알지만, mark_status_bulk 가 저장한 {field}_status
    가 있으면 거기서 사유 추출.
    """
    if item.get(field) == "O":
        return "🟢"
    detail = (item.get(f"{field}_status") or "").lower()
    if detail == "limit":
        return "🟡 한도"
    if detail == "no_permission":
        return "🔴 권한"
    if detail == "error":
        return "🔴 오류"
    return "⚪"


def _has_problem(item: dict) -> bool:
    """색인/백링크 중 하나라도 명시적 실패(no_permission/error)가 있는 항목."""
    for f in ("google_indexed", "naver_indexed", "backlinked"):
        s = (item.get(f"{f}_status") or "").lower()
        if s in ("no_permission", "error"):
            return True
    return False


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
    st.markdown("### 🔄 자동 새로고침")
    refresh_options = {"끔": 0, "30초": 30, "1분": 60, "5분": 300}
    refresh_label = st.selectbox(
        "주기",
        list(refresh_options.keys()),
        index=0,
        label_visibility="collapsed",
    )
    refresh_seconds = refresh_options[refresh_label]
    if refresh_seconds > 0:
        try:
            from streamlit_autorefresh import st_autorefresh
            st_autorefresh(interval=refresh_seconds * 1000, key="dashboard_refresh")
            st.caption(f"⏱ {refresh_label}마다 자동 갱신")
        except ImportError:
            st.caption("⚠ `pip install streamlit-autorefresh` 후 활성")
    else:
        st.caption("수동 새로고침: Ctrl+R 또는 메뉴 → Rerun")
    st.divider()

    st.markdown("### 🔍 필터")
    period = st.selectbox(
        "기간",
        ["오늘", "최근 7일", "최근 30일", "전체"],
        index=1,
    )
    all_platforms = sorted({(it.get("platform") or "?") for it in queue})
    sel_platforms = st.multiselect("플랫폼", all_platforms, default=all_platforms)
    all_sources = sorted({(it.get("source") or "(미정)") for it in queue})
    sel_sources = st.multiselect("소스", all_sources, default=all_sources)

    st.markdown("### ⚡ 빠른 필터")
    only_problems     = st.toggle("문제 있는 항목만 (색인/백링크 실패)")
    only_index_failed = st.toggle("색인 실패만")
    only_no_backlink  = st.toggle("백링크 미완료만")
    only_old_meta     = st.toggle("메타 누락 (소스 비어있음)")
    st.divider()

    st.markdown("### 🗂️ 데이터 갱신 시각")
    st.caption(f"queue: {_file_mtime(DATA / 'publish_queue.json')}")
    st.caption(f"pool:  {_file_mtime(DATA / 'keyword_pool.json')}")
    st.caption(f"used:  {_file_mtime(DATA / 'used_keywords.json')}")
    st.caption(f"roi:   {_file_mtime(DATA / 'keyword_roi.json')}")


# ── 필터 적용 ────────────────────────────────────────────────────────────────

today_iso        = date.today().isoformat()
week_cutoff      = (date.today() - timedelta(days=7)).isoformat()
month_cutoff     = (date.today() - timedelta(days=30)).isoformat()
yesterday_iso    = (date.today() - timedelta(days=1)).isoformat()
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

if only_problems:
    filtered_queue = [it for it in filtered_queue if _has_problem(it)]
if only_index_failed:
    filtered_queue = [
        it for it in filtered_queue
        if it.get("google_indexed") != "O" or it.get("naver_indexed") != "O"
    ]
if only_no_backlink:
    filtered_queue = [it for it in filtered_queue if it.get("backlinked") != "O"]
if only_old_meta:
    filtered_queue = [it for it in filtered_queue
                       if not (it.get("source") or "").strip()]


# ─── 헤더 ────────────────────────────────────────────────────────────────────

st.title("📊 Auto-Publishing Dashboard")
filter_summary = f"{period}"
quick_flags = [lbl for lbl, v in [
    ("문제만", only_problems), ("색인실패", only_index_failed),
    ("백링크X", only_no_backlink), ("메타X", only_old_meta),
] if v]
if quick_flags:
    filter_summary += " · " + " + ".join(quick_flags)
st.caption(f"마지막 새로고침: {datetime.now().strftime('%H:%M:%S')} • "
           f"필터: {filter_summary} • 결과 {len(filtered_queue)}/{len(queue)}건")


# ─── 헤더 카드 (4개 + delta) ─────────────────────────────────────────────────

today_pubs = sum(1 for it in queue if (it.get("queued_at") or "").startswith(today_iso))
yesterday_pubs = sum(1 for it in queue if (it.get("queued_at") or "").startswith(yesterday_iso))
week_pubs  = sum(1 for it in queue if (it.get("queued_at") or "")[:10] >= week_cutoff)
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
          delta=(today_pubs - yesterday_pubs) if yesterday_pubs else None)
c2.metric("주간 발행", week_pubs,
          delta=(week_pubs - prev_week_pubs) if prev_week_pubs else None)
c3.metric("풀 잔여", f"{pool_available:,}",
          delta=f"{pool_pct}% / {pool_total:,}", delta_color="off")
c4.metric("ROI 누적 수수료", f"{roi_total_comm:,}원",
          delta=(f"클릭 {roi_total_clicks:,} • 주문 {roi_total_orders:,}"
                  if (roi_total_clicks or roi_total_orders) else "데이터 누적 대기"),
          delta_color="off")

st.divider()


# ─── 발행 표 ─────────────────────────────────────────────────────────────────

st.subheader(f"발행 내역 ({len(filtered_queue)}건)")

if filtered_queue:
    recent = sorted(filtered_queue, key=lambda it: it.get("queued_at", ""),
                    reverse=True)[:50]

    df_rows = []
    for it in recent:
        df_rows.append({
            "발행시각": _format_relative_time(it.get("queued_at", "")),
            "플랫폼":   it.get("platform", ""),
            "소스":     _placeholder(it.get("source", "")),
            "키워드":   _placeholder(it.get("keyword", "")),
            "G":        _status_badge(it, "google_indexed"),
            "N":        _status_badge(it, "naver_indexed"),
            "BL":       _status_badge(it, "backlinked"),
            "사유":     _placeholder(
                it.get("google_indexed_message") or it.get("naver_indexed_message") or ""
            )[:50],
            "URL":      it.get("url", ""),
        })
    df = pd.DataFrame(df_rows)

    st.dataframe(
        df,
        use_container_width=True,
        hide_index=True,
        column_config={
            "URL":     st.column_config.LinkColumn("URL", width="medium"),
            "발행시각": st.column_config.TextColumn("발행시각", width="small"),
            "G":       st.column_config.TextColumn("G",  width="small",
                                                    help="Google Indexing"),
            "N":       st.column_config.TextColumn("N",  width="small",
                                                    help="Naver Search Advisor"),
            "BL":      st.column_config.TextColumn("BL", width="small",
                                                    help="SNS 백링크"),
            "사유":    st.column_config.TextColumn("실패 사유", width="medium"),
        },
    )
else:
    if any([only_problems, only_index_failed, only_no_backlink, only_old_meta]):
        st.success("🎉 선택한 빠른 필터에 해당하는 항목이 없습니다 — 운영 안정 상태")
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

    g_failed = sum(1 for it in filtered_queue
                    if (it.get("google_indexed_status") or "").lower()
                    in ("no_permission", "error"))
    n_failed = sum(1 for it in filtered_queue
                    if (it.get("naver_indexed_status") or "").lower()
                    in ("no_permission", "error"))

    pcol1, pcol2, pcol3 = st.columns(3)
    with pcol1:
        st.metric("Google", f"{g_done}/{total}",
                  delta=f"실패 {g_failed}건" if g_failed else "정상",
                  delta_color="inverse" if g_failed else "off")
        st.progress(g_done / total if total else 0)
    with pcol2:
        st.metric("Naver", f"{n_done}/{total}",
                  delta=f"실패 {n_failed}건" if n_failed else "정상",
                  delta_color="inverse" if n_failed else "off")
        st.progress(n_done / total if total else 0)
    with pcol3:
        st.metric("백링크", f"{b_done}/{total}",
                  delta=f"{round(b_done/total*100, 1)}%" if total else "—",
                  delta_color="off")
        st.progress(b_done / total if total else 0)

    # 색인 미동작이면 (전체 0건 진척) 자격 채우기 안내
    if total and g_done == 0 and n_done == 0:
        st.warning(
            "색인이 아직 진행되지 않았습니다. 다음 명령으로 자격 검증/수동 실행 가능:\n\n"
            "```\npython tools/test_indexing.py\n```\n"
            "스케줄: SCHEDULE_INDEX (.env). 자격 가이드는 README 색인 섹션 참조."
        )

st.divider()


# ─── 풀 분포 ────────────────────────────────────────────────────────────────

st.subheader("키워드 풀 분포")

if not pool.get("keywords"):
    st.warning(
        "키워드 풀이 비어있습니다. 다음 명령으로 수집:\n\n"
        "```python\n"
        "from sources.itemscout_keywords import collect_all_keywords_multi\n"
        "collect_all_keywords_multi()\n"
        "```"
    )
else:
    col_a, col_b, col_c = st.columns(3)
    with col_a:
        st.caption("소스별")
        src_counts = Counter((k.get("source") or "unknown")
                              for k in pool.get("keywords", []))
        st.bar_chart(pd.DataFrame.from_dict(src_counts, orient="index",
                                             columns=["count"]))
    with col_b:
        st.caption("rank_change (트렌드)")
        rc_counts = Counter((k.get("rank_change") or "").lower() or "none"
                             for k in pool.get("keywords", []))
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

if not roi:
    st.info(
        "ROI 데이터 없음. **자동 누적되려면 다음 자격이 필요**합니다:\n\n"
        "- 쿠팡: `COUPANG_ACCESS_KEY` / `COUPANG_SECRET_KEY` (.env)\n"
        "- 알리: `ALIEXPRESS_APP_KEY` / `APP_SECRET` / `ACCESS_TOKEN` "
        "  (`tools/aliexpress_oauth.py` 로 발급)\n\n"
        "스케줄 `SCHEDULE_ROI_AGGREGATE=03:30` 에서 매일 집계."
    )
else:
    rcol1, rcol2 = st.columns(2)
    coupang_kws = [v for v in roi.values() if v.get("clicks", 0) > 0]
    ali_kws     = [v for v in roi.values()
                   if v.get("orders", 0) > 0 and v.get("clicks", 0) == 0]
    rcol1.metric("쿠팡 — 키워드", len(coupang_kws),
                  delta=f"수수료 {sum(v.get('commission', 0) for v in coupang_kws):,}원",
                  delta_color="off")
    rcol2.metric("알리 — 키워드", len(ali_kws),
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
        cols = st.columns(len(bl_posted))
        for i, (plat, n) in enumerate(bl_posted.items()):
            cols[i].metric(plat, n)
    else:
        st.info(
            "수집은 됐지만 SNS 발행 이력이 없습니다. "
            "`SCHEDULE_BACKLINK_SNS=` 시간 채우면 자동 발행 시작."
        )
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
    f"데이터 출처: data/*.json • 캐시 TTL 15초 • read-only • "
    f"필터 결과 {len(filtered_queue)}/{len(queue)}건"
)
