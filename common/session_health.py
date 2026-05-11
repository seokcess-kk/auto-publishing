"""
영속 프로필(.sessions/*_profile) 쿠키 만료일 사전 점검.

Chromium 영속 프로필의 Cookies sqlite 파일을 직접 읽어 핵심 도메인의
인증 쿠키 만료일을 추출. D-WARN_DAYS 이내면 일일 요약에 경고를 띄운다.

설계:
- sqlite 직접 read-only 모드로 open — lock 회피
- Chromium expires_utc 는 1601-01-01 UTC 기준 마이크로초
- session-only 쿠키 (expires_utc == 0) 는 점검 대상 외
- 프로필이 없거나 핵심 쿠키가 없으면 "미로그인" 상태로 보고

호출:
    from common.session_health import check_profiles
    rows = check_profiles()  # [{"profile":..., "status":..., "days_left":...}, ...]
"""
import sqlite3
import tempfile
import shutil
from datetime import datetime, timezone
from pathlib import Path

_BASE_DIR = Path(__file__).resolve().parent.parent

# Chromium expires_utc → unix epoch 변환을 위한 오프셋 (마이크로초)
# 1601-01-01 ~ 1970-01-01 사이 마이크로초 수
_CHROME_EPOCH_OFFSET = 11644473600 * 1_000_000


# 점검 대상: 각 항목은 (도메인 like, 쿠키 이름 후보) 페어를 여러 개 가질 수
# 있다. 페어 중 하나라도 매칭되면 그 만료일을 사용 (가장 늦은 것).
#
# 뉴스픽/Tistory 는 카카오 SSO 기반이라 .kakao.com / _kau (카카오 access)
# 쿠키 만료가 실질 로그인 유효기간이다.
PROFILES = [
    {
        "profile": "newspick_profile",
        "label":   "뉴스픽",
        "checks":  [
            ("%newspic.kr%",  ("partnersMyStatus", "partnersPCID")),
            ("%kakao.com%",   ("_kau", "_karb")),
        ],
    },
    {
        "profile": "naver_searchadvisor_profile",
        "label":   "네이버 서치어드바이저",
        "checks":  [
            ("%naver.com%",   ("NID_AUT", "NID_SES")),
        ],
    },
    {
        "profile": "tistory_shared_profile",
        "label":   "Tistory",
        "checks":  [
            ("%kakao.com%",   ("_kau", "_karb")),
            ("%tistory.com%", ("_T_ANO",)),
        ],
    },
    {
        "profile": "aliexpress_login_profile",
        "label":   "AliExpress",
        "checks":  [
            ("%aliexpress.com%", ("_hvn", "ali_apache_id", "_m_h5_tk", "xman_t")),
        ],
    },
]


def _cookie_expires_to_dt(chrome_us: int) -> datetime | None:
    """Chromium expires_utc → datetime (UTC). session-only(0) 면 None."""
    if not chrome_us:
        return None
    try:
        unix_us = chrome_us - _CHROME_EPOCH_OFFSET
        return datetime.fromtimestamp(unix_us / 1_000_000, tz=timezone.utc)
    except (OverflowError, OSError):
        return None


def _safe_open_sqlite(path: Path) -> sqlite3.Connection | None:
    """sqlite 를 read-only 로 open. Chromium 실행 중 락이면 임시 복사본 사용."""
    try:
        # read-only URI — uri=True 필수
        return sqlite3.connect(
            f"file:{path}?mode=ro&immutable=1", uri=True, timeout=2
        )
    except sqlite3.OperationalError:
        pass
    # 락이면 임시 디렉토리에 복사 후 open
    try:
        tmp = Path(tempfile.gettempdir()) / f"cookies_{path.parent.parent.name}.db"
        shutil.copy2(path, tmp)
        return sqlite3.connect(str(tmp), timeout=2)
    except Exception:
        return None


def _max_expiry(conn: sqlite3.Connection, domain_like: str,
                names: tuple) -> datetime | None:
    """해당 도메인의 핵심 쿠키 중 만료 가장 늦은 것의 datetime."""
    placeholders = ",".join("?" for _ in names)
    sql = (
        "SELECT MAX(expires_utc) FROM cookies "
        f"WHERE host_key LIKE ? AND name IN ({placeholders})"
    )
    try:
        cur = conn.execute(sql, (domain_like, *names))
        row = cur.fetchone()
    except sqlite3.DatabaseError:
        return None
    if not row or not row[0]:
        return None
    return _cookie_expires_to_dt(row[0])


def check_profiles(warn_days: int = 7) -> list[dict]:
    """모든 영속 프로필을 점검해 상태 행 반환.

    Args:
        warn_days: 이 일수 이내 만료면 'warn' 상태

    Returns:
        [{"profile":..., "label":..., "status":..., "days_left":..., "detail":...}, ...]
        status: "ok" | "warn" | "missing" | "session_only" | "no_cookies"
    """
    rows: list[dict] = []
    now = datetime.now(timezone.utc)

    for cfg in PROFILES:
        profile_dir = _BASE_DIR / ".sessions" / cfg["profile"]
        cookies_db  = profile_dir / "Default" / "Network" / "Cookies"

        out = {
            "profile":   cfg["profile"],
            "label":     cfg["label"],
            "status":    "missing",
            "days_left": None,
            "detail":    "",
        }

        if not cookies_db.exists():
            out["detail"] = "프로필 미존재 — 수동 로그인 필요"
            rows.append(out)
            continue

        conn = _safe_open_sqlite(cookies_db)
        if conn is None:
            out["status"] = "no_cookies"
            out["detail"] = "sqlite open 실패 (브라우저 실행 중?)"
            rows.append(out)
            continue

        try:
            # 여러 (domain, names) 쌍 중 가장 늦은 만료일 채택
            expiries = []
            for domain_like, names in cfg["checks"]:
                e = _max_expiry(conn, domain_like, names)
                if e is not None:
                    expiries.append(e)
            expiry = max(expiries) if expiries else None
        finally:
            try:
                conn.close()
            except Exception:
                pass

        if expiry is None:
            out["status"] = "session_only"
            out["detail"] = "핵심 쿠키 없음 또는 session-only (재로그인 필요 가능)"
            rows.append(out)
            continue

        days_left = (expiry - now).days
        out["days_left"] = days_left
        if days_left < 0:
            out["status"] = "warn"
            out["detail"] = f"이미 만료 ({-days_left}일 경과)"
        elif days_left <= warn_days:
            out["status"] = "warn"
            out["detail"] = f"D-{days_left} 만료 임박 ({expiry.date()})"
        else:
            out["status"] = "ok"
            out["detail"] = f"만료 {expiry.date()} (D-{days_left})"
        rows.append(out)

    return rows


def build_warning_lines(rows: list[dict] | None = None) -> list[str]:
    """일일 요약에 끼울 한 줄들. 경고/이상만 반환 (정상은 생략)."""
    if rows is None:
        rows = check_profiles()
    lines: list[str] = []
    for r in rows:
        if r["status"] == "ok":
            continue
        icon = {
            "warn":         "⚠️",
            "missing":      "❌",
            "no_cookies":   "❓",
            "session_only": "🔄",
        }.get(r["status"], "•")
        lines.append(f"  {icon} {r['label']}: {r['detail']}")
    return lines


if __name__ == "__main__":
    # 수동 점검용 — `python -m common.session_health`
    rows = check_profiles()
    print(f"{'프로필':<32} {'상태':<14} {'D-':<6} 상세")
    print("-" * 80)
    for r in rows:
        d = "-" if r["days_left"] is None else r["days_left"]
        print(f"{r['profile']:<32} {r['status']:<14} {d!s:<6} {r['detail']}")
