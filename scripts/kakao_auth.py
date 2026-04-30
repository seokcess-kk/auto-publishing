"""
카카오 OAuth 초기 토큰 발급 스크립트.

최초 1회만 실행. 이후 access_token 은 common/kakao_token.py 가 자동 갱신.

사전 준비:
  1. developers.kakao.com → 앱 → 카카오 로그인 ON
  2. Redirect URI 에 https://localhost:5000 등록
  3. 동의항목 → '카카오톡 메시지 전송' 필수 동의 설정
  4. .env 에 KAKAO_REST_API_KEY 값 확인

실행:
    python scripts/kakao_auth.py
"""
import os
import sys
import webbrowser
from urllib.parse import urlencode, urlparse, parse_qs

import requests
from dotenv import load_dotenv, set_key

load_dotenv()

REST_API_KEY  = os.getenv("KAKAO_REST_API_KEY", "")
REDIRECT_URI  = "https://localhost:5000"
_ENV_FILE     = os.path.join(os.path.dirname(__file__), "..", ".env")
_AUTH_URL     = "https://kauth.kakao.com/oauth/authorize"
_TOKEN_URL    = "https://kauth.kakao.com/oauth/token"


def main():
    if not REST_API_KEY:
        print("❌ KAKAO_REST_API_KEY 가 .env 에 없습니다.")
        sys.exit(1)

    # 1단계: 인증 코드 발급 URL 생성
    params = {
        "client_id":     REST_API_KEY,
        "redirect_uri":  REDIRECT_URI,
        "response_type": "code",
        "scope":         "talk_message,talk_calendar",
    }
    auth_url = f"{_AUTH_URL}?{urlencode(params)}"

    print("=" * 60)
    print("카카오 OAuth 초기 토큰 발급")
    print("=" * 60)
    print("\n아래 URL 을 브라우저에서 열어 카카오 계정으로 로그인하세요:")
    print(f"\n  {auth_url}\n")

    try:
        webbrowser.open(auth_url)
        print("(브라우저가 자동으로 열렸습니다.)")
    except Exception:
        pass

    print("\n로그인 후 리다이렉트된 URL 전체를 붙여넣기 하세요:")
    print("예: https://localhost:5000/?code=XXXXXX\n")
    redirected = input("리다이렉트 URL: ").strip()

    # 인증 코드 파싱
    try:
        qs = parse_qs(urlparse(redirected).query)
        code = qs["code"][0]
    except Exception:
        print("❌ URL 에서 code 를 찾지 못했습니다.")
        sys.exit(1)

    # 2단계: code → access_token + refresh_token
    resp = requests.post(
        _TOKEN_URL,
        data={
            "grant_type":   "authorization_code",
            "client_id":    REST_API_KEY,
            "redirect_uri": REDIRECT_URI,
            "code":         code,
        },
        timeout=10,
    )
    if not resp.ok:
        print(f"❌ 토큰 발급 실패: {resp.text}")
        sys.exit(1)

    data = resp.json()
    access_token  = data.get("access_token", "")
    refresh_token = data.get("refresh_token", "")
    expires_in    = data.get("expires_in", 0)

    if not access_token:
        print(f"❌ access_token 없음: {data}")
        sys.exit(1)

    # 3단계: .env 에 저장
    env_path = os.path.abspath(_ENV_FILE)
    set_key(env_path, "KAKAO_ACCESS_TOKEN",  access_token)
    set_key(env_path, "KAKAO_REFRESH_TOKEN", refresh_token)

    print("\n✅ 토큰 발급 완료!")
    print(f"   access_token  유효: {expires_in // 3600}시간")
    print(f"   refresh_token 유효: {data.get('refresh_token_expires_in', 0) // 86400}일")
    print(f"   .env 저장 완료: {env_path}")

    # 4단계: 테스트 메시지 전송
    print("\n테스트 메시지 전송 중...")
    import json
    template = json.dumps({
        "object_type": "text",
        "text":        "✅ Auto Publishing 카카오 알림 연결 완료!",
        "link":        {"web_url": "", "mobile_web_url": ""},
    }, ensure_ascii=False)

    test_resp = requests.post(
        "https://kapi.kakao.com/v2/api/talk/memo/default/send",
        headers={"Authorization": f"Bearer {access_token}"},
        data={"template_object": template},
        timeout=10,
    )
    if test_resp.ok:
        print("✅ 카카오톡 나와의 채팅에 테스트 메시지 전송 성공!")
    else:
        print(f"⚠️  테스트 메시지 실패: {test_resp.text}")
        print("   (토큰은 저장됐습니다. 앱 동의항목을 확인하세요.)")


if __name__ == "__main__":
    main()
