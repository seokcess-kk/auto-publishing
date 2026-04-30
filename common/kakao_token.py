"""
카카오 OAuth access_token 관리.

- access_token: 6시간 유효, API 호출에 사용
- refresh_token: 최대 60일 유효, access_token 자동 갱신
- 토큰은 .env 의 KAKAO_ACCESS_TOKEN / KAKAO_REFRESH_TOKEN 으로 관리
  (초기 발급은 scripts/kakao_auth.py 로 수행)

사용:
    from common.kakao_token import get_access_token
    token = get_access_token()   # 만료됐으면 자동 갱신
"""
import os
import time

import requests
from dotenv import load_dotenv, set_key

from common.logger import log


_ENV_FILE = os.path.join(os.path.dirname(__file__), "..", ".env")
_TOKEN_URL = "https://kauth.kakao.com/oauth/token"


def _env_path() -> str:
    return os.path.abspath(_ENV_FILE)


def refresh_access_token() -> str:
    """refresh_token 으로 새 access_token 발급 후 .env 갱신. 새 토큰 반환."""
    rest_api_key   = os.getenv("KAKAO_REST_API_KEY", "")
    refresh_token  = os.getenv("KAKAO_REFRESH_TOKEN", "")

    if not rest_api_key or not refresh_token:
        log("KAKAO_REST_API_KEY 또는 KAKAO_REFRESH_TOKEN 없음", "error")
        return ""

    resp = requests.post(
        _TOKEN_URL,
        data={
            "grant_type":    "refresh_token",
            "client_id":     rest_api_key,
            "refresh_token": refresh_token,
        },
        timeout=10,
    )
    if not resp.ok:
        log(f"카카오 토큰 갱신 실패: {resp.text[:200]}", "error")
        return ""

    data = resp.json()
    new_access_token = data.get("access_token", "")
    if not new_access_token:
        log(f"카카오 토큰 갱신 응답 이상: {data}", "error")
        return ""

    env_path = _env_path()
    set_key(env_path, "KAKAO_ACCESS_TOKEN", new_access_token)
    os.environ["KAKAO_ACCESS_TOKEN"] = new_access_token

    # refresh_token 도 갱신된 경우 함께 저장
    new_refresh = data.get("refresh_token", "")
    if new_refresh:
        set_key(env_path, "KAKAO_REFRESH_TOKEN", new_refresh)
        os.environ["KAKAO_REFRESH_TOKEN"] = new_refresh
        log("카카오 refresh_token 도 갱신됨", "info")

    expires_in = data.get("expires_in", 21600)
    log(f"카카오 access_token 갱신 완료 (유효: {expires_in//3600}h)", "ok")
    load_dotenv(env_path, override=True)
    return new_access_token


def get_access_token() -> str:
    """유효한 access_token 반환. 없거나 만료됐으면 자동 갱신."""
    token = os.getenv("KAKAO_ACCESS_TOKEN", "")
    if not token:
        log("KAKAO_ACCESS_TOKEN 없음 — 갱신 시도", "warn")
        return refresh_access_token()
    return token
