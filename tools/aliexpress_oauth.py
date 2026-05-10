"""
Alibaba TOP OAuth — code → access_token 교환 헬퍼

사전 조건:
  1. https://open.aliexpress.com 에서 앱 등록 + Affiliate API 권한 승인
  2. .env 의 ALIEXPRESS_APP_KEY / ALIEXPRESS_APP_SECRET 설정

사용 흐름:
  Step 1) 다음 URL 을 브라우저로 열어 본인 알리 어필리에이트 계정으로 로그인 + 인증:
          https://api-sg.aliexpress.com/oauth/authorize?response_type=code
            &client_id=<APP_KEY>&redirect_uri=http://localhost&state=test&view=web&sp=ae

          → 인증 후 localhost?code=XXX&state=test 로 redirect (페이지 안 떠도 OK,
            URL 표시줄의 code 값만 복사)

  Step 2) 받은 code 를 인자로 이 스크립트 실행:
          python tools/aliexpress_oauth.py <CODE>

          → access_token / refresh_token 출력 + .env 에 자동 추가 (덮어쓰기 X, 안내만)
"""
import json
import os
import sys
from pathlib import Path

import requests

_BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BASE_DIR))

from dotenv import load_dotenv
load_dotenv(_BASE_DIR / ".env")


_APP_KEY    = os.getenv("ALIEXPRESS_APP_KEY", "")
_APP_SECRET = os.getenv("ALIEXPRESS_APP_SECRET", "")
_TOKEN_URL  = "https://api-sg.aliexpress.com/oauth/token"


def authorize_url() -> str:
    """본인 계정으로 인증할 OAuth Authorize URL."""
    return (
        "https://api-sg.aliexpress.com/oauth/authorize"
        f"?response_type=code&client_id={_APP_KEY}"
        f"&redirect_uri=http://localhost&state=auto-publishing&view=web&sp=ae"
    )


def exchange_code(code: str) -> dict:
    """code → access_token / refresh_token 교환."""
    if not (_APP_KEY and _APP_SECRET):
        print("✗ ALIEXPRESS_APP_KEY / APP_SECRET 미설정 — .env 먼저 채우세요")
        sys.exit(1)

    params = {
        "grant_type":         "authorization_code",
        "need_refresh_token": "true",
        "client_id":          _APP_KEY,
        "client_secret":      _APP_SECRET,
        "redirect_uri":       "http://localhost",
        "code":               code,
        "sp":                 "ae",
    }
    try:
        r = requests.get(_TOKEN_URL, params=params, timeout=15)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"✗ 토큰 교환 실패: {e}")
        if hasattr(e, "response") and e.response is not None:
            print(f"  응답: {e.response.text[:300]}")
        sys.exit(1)


def main() -> int:
    if len(sys.argv) < 2:
        print("Step 1 — 다음 URL 을 브라우저로 열어 인증 완료 후 redirect URL 의")
        print("        ?code= 값을 복사하세요:")
        print()
        print(f"  {authorize_url()}")
        print()
        print("Step 2 — 받은 code 를 인자로 다시 실행:")
        print(f"  python tools/aliexpress_oauth.py <CODE>")
        return 1

    code = sys.argv[1].strip()
    print(f"code 교환 시도: {code[:20]}...")
    result = exchange_code(code)

    print()
    print("=== 교환 결과 ===")
    print(json.dumps(result, ensure_ascii=False, indent=2))

    access  = result.get("access_token", "")
    refresh = result.get("refresh_token", "")
    if access:
        print()
        print("✓ 발급 성공. .env 에 다음을 채우세요:")
        print(f"  ALIEXPRESS_ACCESS_TOKEN={access}")
        if refresh:
            print(f"  ALIEXPRESS_REFRESH_TOKEN={refresh}")
        print()
        print("그 다음 검증:")
        print("  python -m common.aliexpress_stats")
        return 0
    else:
        print("✗ access_token 누락 — 응답 확인")
        return 1


if __name__ == "__main__":
    sys.exit(main())
