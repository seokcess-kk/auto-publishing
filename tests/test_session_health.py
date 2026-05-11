"""session_health 의 만료일 산출 로직 테스트.

Chromium 쿠키 sqlite 의 expires_utc 변환 + 워닝 분류 검증.
"""
import sqlite3
from datetime import datetime, timezone, timedelta
from pathlib import Path

import common.session_health as sh


_CHROME_EPOCH_OFFSET = 11644473600 * 1_000_000


def _dt_to_chrome_us(dt: datetime) -> int:
    """datetime → Chromium expires_utc 형식."""
    return int(dt.timestamp() * 1_000_000) + _CHROME_EPOCH_OFFSET


def _build_cookies_db(path: Path, rows: list[tuple]) -> None:
    """rows: (host_key, name, expires_utc) 튜플 리스트."""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.execute("""
        CREATE TABLE cookies (
            host_key TEXT, name TEXT, expires_utc INTEGER
        )
    """)
    conn.executemany("INSERT INTO cookies VALUES (?, ?, ?)", rows)
    conn.commit()
    conn.close()


def test_cookie_expires_session_only():
    """expires_utc=0 → session-only → None."""
    assert sh._cookie_expires_to_dt(0) is None


def test_cookie_expires_valid():
    target = datetime(2027, 1, 1, tzinfo=timezone.utc)
    chrome_us = _dt_to_chrome_us(target)
    result = sh._cookie_expires_to_dt(chrome_us)
    assert result is not None
    # 마이크로초 손실로 1초 이내 오차 허용
    assert abs((result - target).total_seconds()) < 1


def test_check_profiles_with_real_sqlite(tmp_path, monkeypatch):
    """실제 sqlite 만들어 check_profiles 가 D-day 계산하는지."""
    # 임시 .sessions 디렉토리 구조
    fake_base = tmp_path / "auto-pub"
    profile = fake_base / ".sessions" / "newspick_profile"
    cookies = profile / "Default" / "Network" / "Cookies"

    # 30일 후 만료되는 newspick 쿠키
    expire = datetime.now(timezone.utc) + timedelta(days=30)
    _build_cookies_db(cookies, [
        ("partners.newspic.kr", "partnersMyStatus",
         _dt_to_chrome_us(expire)),
    ])

    monkeypatch.setattr(sh, "_BASE_DIR", fake_base)
    rows = sh.check_profiles(warn_days=7)

    newspick = next(r for r in rows if r["profile"] == "newspick_profile")
    assert newspick["status"] == "ok"
    assert newspick["days_left"] is not None
    # 30일 ± 1 (실행 시각 차이)
    assert 29 <= newspick["days_left"] <= 30


def test_warn_when_within_warn_days(tmp_path, monkeypatch):
    """warn_days 이내 만료 → status='warn' 검증."""
    fake_base = tmp_path / "auto-pub"
    cookies = (fake_base / ".sessions" / "newspick_profile"
               / "Default" / "Network" / "Cookies")

    # 3일 + 1시간 후 만료 — 정수 일수 절삭으로 D-2 또는 D-3 로 보고됨
    expire = datetime.now(timezone.utc) + timedelta(days=3, hours=1)
    _build_cookies_db(cookies, [
        ("partners.newspic.kr", "partnersMyStatus",
         _dt_to_chrome_us(expire)),
    ])

    monkeypatch.setattr(sh, "_BASE_DIR", fake_base)
    rows = sh.check_profiles(warn_days=7)
    newspick = next(r for r in rows if r["profile"] == "newspick_profile")
    assert newspick["status"] == "warn"
    # warn_days(7) 이내 → days_left 0~7
    assert newspick["days_left"] is not None
    assert 0 <= newspick["days_left"] <= 7


def test_expired_cookie(tmp_path, monkeypatch):
    """이미 만료된 쿠키 → status='warn', days_left 음수."""
    fake_base = tmp_path / "auto-pub"
    cookies = (fake_base / ".sessions" / "newspick_profile"
               / "Default" / "Network" / "Cookies")

    expire = datetime.now(timezone.utc) - timedelta(days=5)
    _build_cookies_db(cookies, [
        ("partners.newspic.kr", "partnersMyStatus",
         _dt_to_chrome_us(expire)),
    ])

    monkeypatch.setattr(sh, "_BASE_DIR", fake_base)
    rows = sh.check_profiles()
    newspick = next(r for r in rows if r["profile"] == "newspick_profile")
    assert newspick["status"] == "warn"
    assert "이미 만료" in newspick["detail"]


def test_missing_profile(tmp_path, monkeypatch):
    """프로필 디렉토리 자체가 없으면 status='missing'."""
    monkeypatch.setattr(sh, "_BASE_DIR", tmp_path)  # 빈 디렉토리
    rows = sh.check_profiles()
    assert all(r["status"] == "missing" for r in rows)


def test_build_warning_lines_skips_ok():
    """status='ok' 인 행은 경고 라인에 포함 안 됨."""
    fake_rows = [
        {"label": "정상",    "status": "ok",      "detail": "D-30"},
        {"label": "임박",    "status": "warn",    "detail": "D-3"},
        {"label": "미존재",  "status": "missing", "detail": "프로필 없음"},
    ]
    lines = sh.build_warning_lines(fake_rows)
    assert len(lines) == 2
    assert any("임박" in ln for ln in lines)
    assert any("미존재" in ln for ln in lines)
    assert not any("정상" in ln for ln in lines)
