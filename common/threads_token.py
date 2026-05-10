"""
Threads 토큰 관리 유틸리티

토큰 갱신 시나리오:
  1. 정기 갱신 (토큰 살아있을 때): refresh_long_lived_token()
     - 발급/갱신 후 24시간 이상 ~ 60일 이내 사이에 가능
     - 갱신 성공 시 .env 자동 업데이트

  2. 완전 만료 후 재발급: run_oauth_flow()
     - 브라우저로 인증 URL 열기 → 사용자 직접 로그인 → redirect URL에서 code 추출
     - 단기 토큰 발급 → 장기 토큰 교환 → .env 자동 업데이트

CLI 사용법:
  python -m common.threads_token refresh   # 갱신 시도 (정기 갱신)
  python -m common.threads_token exchange  # 단기→장기 교환 (code 입력 필요)
  python -m common.threads_token oauth     # OAuth 전체 플로우 (브라우저 + code 입력)
  python -m common.threads_token status    # 토큰 만료 상태 확인
"""
import os
import sys
from typing import Optional
import re
import webbrowser
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode, urlparse, parse_qs

import requests
from dotenv import load_dotenv, set_key

load_dotenv()

GRAPH_BASE = "https://graph.threads.net/v1.0"

# .env 파일 경로
_ENV_PATH = Path(__file__).parent.parent / ".env"


# ──────────────────────────────────────────────────────────────────────────────
# 환경변수 로드
# ──────────────────────────────────────────────────────────────────────────────

def _get_config() -> dict:
    return {
        "app_id":       os.getenv("THREADS_APP_ID", ""),
        "app_secret":   os.getenv("THREADS_APP_SECRET", ""),
        "redirect_uri": os.getenv("THREADS_REDIRECT_URI", ""),
        "user_id":      os.getenv("THREADS_USER_ID", ""),
        "access_token": os.getenv("THREADS_ACCESS_TOKEN", ""),
    }


def _save_token(new_token: str) -> None:
    """갱신된 토큰을 .env에 저장."""
    set_key(str(_ENV_PATH), "THREADS_ACCESS_TOKEN", new_token)
    print(f"[OK] .env THREADS_ACCESS_TOKEN 업데이트 완료")


# ──────────────────────────────────────────────────────────────────────────────
# 1. 토큰 상태 확인
# ──────────────────────────────────────────────────────────────────────────────

def check_token_status() -> dict:
    """현재 토큰의 유효성과 만료 정보를 조회.

    Returns:
        {'valid': bool, 'expires_at': datetime|None, 'days_left': int, 'user_id': str}
    """
    cfg = _get_config()
    if not cfg["access_token"]:
        print("[ERROR] THREADS_ACCESS_TOKEN 미설정")
        return {"valid": False}

    url = f"{GRAPH_BASE}/me"
    resp = requests.get(url, params={
        "fields": "id,name",
        "access_token": cfg["access_token"],
    }, timeout=10)

    if resp.ok:
        data = resp.json()
        print(f"[OK] 토큰 유효 — 사용자: {data.get('name', '알 수 없음')} (id={data.get('id')})")
        return {"valid": True, "user_id": data.get("id", "")}
    else:
        err = resp.json().get("error", {})
        msg = err.get("message", resp.text[:200])
        print(f"[ERROR] 토큰 오류: {msg}")

        # 만료 시각 파싱 시도
        m = re.search(r"expired on (.+?)\.", msg)
        if m:
            print(f"  만료일: {m.group(1)}")

        return {"valid": False, "error": msg}


# ──────────────────────────────────────────────────────────────────────────────
# 2. 장기 토큰 갱신 (기존 토큰이 유효할 때)
# ──────────────────────────────────────────────────────────────────────────────

def refresh_long_lived_token(save: bool = True) -> Optional[str]:
    """현재 장기 토큰을 갱신하여 60일 연장.

    조건: 발급 후 24시간 이상 경과, 60일 이내
    성공 시 .env 자동 업데이트.

    Returns:
        새 토큰 문자열, 실패 시 None
    """
    cfg = _get_config()
    if not cfg["access_token"]:
        print("[ERROR] THREADS_ACCESS_TOKEN 미설정")
        return None

    print("장기 토큰 갱신 시도...")
    resp = requests.get(f"{GRAPH_BASE}/refresh_access_token", params={
        "grant_type":   "th_refresh_token",
        "access_token": cfg["access_token"],
    }, timeout=15)

    if resp.ok:
        data = resp.json()
        new_token   = data.get("access_token", "")
        expires_in  = data.get("expires_in", 0)
        days_left   = expires_in // 86400
        print(f"[OK] 갱신 성공 — 만료까지 {days_left}일")
        if save and new_token:
            _save_token(new_token)
        return new_token
    else:
        err = resp.json().get("error", {})
        print(f"[ERROR] 갱신 실패: {err.get('message', resp.text[:300])}")
        print("  → 토큰이 만료됐거나 24시간 미경과. OAuth 재발급 필요: python -m common.threads_token oauth")
        return None


# ──────────────────────────────────────────────────────────────────────────────
# 3. 단기 토큰 → 장기 토큰 교환
# ──────────────────────────────────────────────────────────────────────────────

def exchange_short_to_long(short_token: str, save: bool = True) -> Optional[str]:
    """단기 액세스 토큰을 60일짜리 장기 토큰으로 교환.

    Args:
        short_token: OAuth flow에서 받은 단기 토큰
        save:        성공 시 .env 자동 업데이트 여부

    Returns:
        장기 토큰 문자열, 실패 시 None
    """
    cfg = _get_config()
    if not cfg["app_secret"]:
        print("[ERROR] THREADS_APP_SECRET 미설정")
        return None

    print("단기 토큰 → 장기 토큰 교환 중...")
    resp = requests.get(f"{GRAPH_BASE}/access_token", params={
        "grant_type":    "th_exchange_token",
        "client_secret": cfg["app_secret"],
        "access_token":  short_token,
    }, timeout=15)

    if resp.ok:
        data = resp.json()
        long_token = data.get("access_token", "")
        expires_in = data.get("expires_in", 0)
        days_left  = expires_in // 86400
        print(f"[OK] 장기 토큰 발급 성공 — 만료까지 {days_left}일")
        if save and long_token:
            _save_token(long_token)
        return long_token
    else:
        err = resp.json().get("error", {})
        print(f"[ERROR] 교환 실패: {err.get('message', resp.text[:300])}")
        return None


# ──────────────────────────────────────────────────────────────────────────────
# 4. 단기 토큰 발급 (authorization code → short-lived token)
# ──────────────────────────────────────────────────────────────────────────────

def get_short_lived_token(auth_code: str) -> Optional[str]:
    """Authorization code를 단기 액세스 토큰으로 교환.

    Args:
        auth_code: OAuth redirect URL에서 추출한 code 값

    Returns:
        단기 토큰 문자열, 실패 시 None
    """
    cfg = _get_config()
    missing = [k for k in ("app_id", "app_secret", "redirect_uri") if not cfg[k]]
    if missing:
        print(f"[ERROR] 미설정 환경변수: {', '.join('THREADS_' + k.upper() for k in missing)}")
        return None

    print("Authorization code → 단기 토큰 교환 중...")
    resp = requests.post("https://graph.threads.net/oauth/access_token", data={
        "client_id":     cfg["app_id"],
        "client_secret": cfg["app_secret"],
        "code":          auth_code,
        "grant_type":    "authorization_code",
        "redirect_uri":  cfg["redirect_uri"],
    }, timeout=15)

    if resp.ok:
        data = resp.json()
        short_token = data.get("access_token", "")
        user_id     = data.get("user_id", "")
        print(f"[OK] 단기 토큰 발급 성공 (user_id={user_id})")
        return short_token
    else:
        err = resp.json().get("error", {})
        print(f"[ERROR] 단기 토큰 발급 실패: {err.get('message', resp.text[:300])}")
        return None


# ──────────────────────────────────────────────────────────────────────────────
# 5. OAuth 전체 플로우 (완전 만료 시 재발급)
# ──────────────────────────────────────────────────────────────────────────────

def run_oauth_flow() -> Optional[str]:
    """OAuth 전체 플로우 실행 (브라우저 인증 → 단기 토큰 → 장기 토큰).

    완전히 만료된 토큰을 재발급할 때 사용.
    브라우저가 열리고 인증 후 redirect URL을 터미널에 붙여넣어야 함.

    Returns:
        장기 토큰 문자열, 실패 시 None
    """
    cfg = _get_config()
    missing = [k for k in ("app_id", "redirect_uri") if not cfg[k]]
    if missing:
        print(f"[ERROR] 미설정 환경변수: {', '.join('THREADS_' + k.upper() for k in missing)}")
        return None

    # Step 1: OAuth 인증 URL 생성 및 브라우저 열기
    auth_params = {
        "client_id":     cfg["app_id"],
        "redirect_uri":  cfg["redirect_uri"],
        # threads_manage_replies 가 있어야 reply chain 발행 가능
        "scope":         "threads_basic,threads_content_publish,threads_manage_replies",
        "response_type": "code",
    }
    auth_url = "https://threads.net/oauth/authorize?" + urlencode(auth_params)

    print("=" * 60)
    print("Threads OAuth 인증 플로우")
    print("=" * 60)
    print(f"\n[1] 아래 URL로 브라우저가 열립니다. Threads 계정으로 로그인하세요:\n")
    print(f"  {auth_url}\n")

    try:
        webbrowser.open(auth_url)
        print("  (브라우저가 자동으로 열렸습니다)\n")
    except Exception:
        print("  (브라우저를 직접 열어서 위 URL에 접속하세요)\n")

    # Step 2: redirect URL 입력 받기
    print("[2] 인증 완료 후 브라우저 주소창의 URL 전체를 붙여넣으세요.")
    print(f"    (예: {cfg['redirect_uri']}?code=AQ...#_)\n")
    redirect_url = input("  리다이렉트 URL: ").strip()

    # code 파싱
    parsed = urlparse(redirect_url)
    qs = parse_qs(parsed.query)
    auth_code = qs.get("code", [None])[0]

    if not auth_code:
        # URL 전체가 아닌 code만 붙여넣은 경우 대응
        auth_code = redirect_url.strip()

    if not auth_code:
        print("[ERROR] code를 추출할 수 없습니다.")
        return None

    # code 끝의 #_ 제거 (Threads가 붙이는 fragment)
    auth_code = auth_code.split("#")[0]
    print(f"\n  추출된 code: {auth_code[:20]}...\n")

    # Step 3: code → 단기 토큰
    short_token = get_short_lived_token(auth_code)
    if not short_token:
        return None

    # Step 4: 단기 → 장기 토큰
    long_token = exchange_short_to_long(short_token, save=True)
    if long_token:
        print("\n[완료] 장기 토큰이 .env에 저장되었습니다.")
        print("       이제 Threads 발행을 다시 사용할 수 있습니다.")
    return long_token


# ──────────────────────────────────────────────────────────────────────────────
# CLI 진입점
# ──────────────────────────────────────────────────────────────────────────────

def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"

    if cmd == "status":
        print("=== Threads 토큰 상태 확인 ===")
        check_token_status()

    elif cmd == "refresh":
        print("=== 장기 토큰 갱신 ===")
        refresh_long_lived_token()

    elif cmd == "exchange":
        print("=== 단기 → 장기 토큰 교환 ===")
        if len(sys.argv) < 3:
            short = input("단기 토큰을 입력하세요: ").strip()
        else:
            short = sys.argv[2]
        exchange_short_to_long(short)

    elif cmd == "oauth":
        print("=== OAuth 전체 플로우 (토큰 재발급) ===")
        run_oauth_flow()

    else:
        print("사용법:")
        print("  python -m common.threads_token status    # 토큰 상태 확인")
        print("  python -m common.threads_token refresh   # 장기 토큰 갱신 (만료 전)")
        print("  python -m common.threads_token exchange  # 단기→장기 교환")
        print("  python -m common.threads_token oauth     # OAuth 전체 플로우 (만료 후 재발급)")


if __name__ == "__main__":
    main()
