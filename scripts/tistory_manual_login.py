"""티스토리 카카오 1회 수동 로그인 헬퍼.

publisher 의 자동 로그인은 TISTORY_EMAIL/PASSWORD 가 필요하지만, .env 에
계정 정보를 두기 싫을 때 본 스크립트로 1회만 수동 로그인하면 세션이
.sessions/tistory_shared_profile/ 에 영구 저장된다. 이후 publisher 는
저장된 세션으로 /manage 직접 접근 → 자동 발행.

사용:
    python -m scripts.tistory_manual_login
    (또는)  python -m scripts.tistory_manual_login kkkseok

흐름:
    1. headless=false 로 persistent profile 기동
    2. https://www.tistory.com/auth/login 자동 진입
    3. 사용자가 카카오 버튼 클릭 → 로그인 → 2단계 인증
    4. /manage 페이지로 이동 확인되면 콘솔에서 Enter
    5. context 정리 후 종료 → 세션 저장 완료
"""
from __future__ import annotations

import os
import sys

from dotenv import load_dotenv
load_dotenv()

from common.browser_profile import PersistentBrowserProfile


def main(blog_name: str = "") -> int:
    blog_name = blog_name or os.getenv("TISTORY_BLOG_NAME", "")
    if not blog_name:
        print("[ERROR] TISTORY_BLOG_NAME 미설정 — .env 확인 또는 인자로 전달")
        return 2

    blog_url = f"https://{blog_name}.tistory.com"
    profile = PersistentBrowserProfile("tistory_shared")

    print(f"[INFO] 프로필 디렉토리: {profile.user_data_dir}")
    print(f"[INFO] 대상 블로그: {blog_url}")
    print("[INFO] Chromium 창이 곧 열립니다. 카카오 로그인을 완료하세요.")
    print("[INFO] /manage 페이지 진입을 확인하면 이 콘솔에서 Enter 를 눌러주세요.\n")

    # publisher 와 동일한 OAuth URL 로 직접 진입 — 사용자는 ID/PW 입력만 하면
    # tistory OAuth 콜백 → <blog>/manage 까지 한 번에 도달하여 blog 세션 쿠키 발급.
    from base64 import b64encode
    from urllib.parse import urlencode
    from publishers.tistory import (
        TISTORY_KAKAO_CLIENT_ID, TISTORY_KAKAO_REDIRECT_URI,
    )

    redirect_url = f"{blog_url}/manage"
    state = b64encode(redirect_url.encode("utf-8")).decode("ascii").rstrip("=")
    authorize_url = (
        "https://kauth.kakao.com/oauth/authorize?"
        + urlencode({
            "client_id": TISTORY_KAKAO_CLIENT_ID,
            "redirect_uri": TISTORY_KAKAO_REDIRECT_URI,
            "response_type": "code",
            "state": state,
            "through_account": "true",
        })
    )

    with profile.launch(headless=False) as context:
        page = context.new_page() if not context.pages else context.pages[0]
        try:
            page.goto(authorize_url, wait_until="domcontentloaded", timeout=30000)
        except Exception as e:
            print(f"[WARN] OAuth URL 진입 예외(무시 가능): {e}")

        print(f"[GUIDE] 카카오 로그인 완료 후 브라우저 주소창이")
        print(f"        '{redirect_url}' 로 자동 이동하는지 확인하세요.")
        print(f"        만약 자동 이동이 안 되면 직접 주소창에 입력해서 들어가세요.\n")

        try:
            input(">>> kkkseok.tistory.com/manage 화면이 보이면 Enter: ")
        except (EOFError, KeyboardInterrupt):
            print("\n[ABORT] 사용자 취소 — 세션 미저장")
            return 1

        # 한 번 더 /manage 직접 접근으로 blog-specific 쿠키 flush 보장
        try:
            page.goto(redirect_url, wait_until="domcontentloaded", timeout=15000)
            cur = page.url
            print(f"[INFO] 최종 URL: {cur}")
            if "/manage" in cur and blog_name in cur:
                print("[OK] 블로그별 세션 쿠키 발급 확인")
            else:
                print("[WARN] /manage 직접 접근 실패 — 세션이 부족할 수 있습니다.")
                print("       그래도 일단 저장합니다. 파이프라인 실행 시 실패하면 재시도 필요.")
        except Exception as e:
            print(f"[WARN] /manage 진입 예외(무시): {e}")

    print("\n[OK] 세션이 .sessions/tistory_shared_profile/ 에 저장되었습니다.")
    print("[OK] 이제 'python -m pipelines.riseset_to_tistory' 실행 시 자동 로그인됩니다.")
    return 0


if __name__ == "__main__":
    arg = sys.argv[1] if len(sys.argv) > 1 else ""
    sys.exit(main(arg))
