"""티스토리 카카오 1회 수동 로그인 헬퍼.

publisher 의 자동 로그인은 TISTORY_EMAIL/PASSWORD 가 필요하지만, .env 에
계정 정보를 두기 싫을 때 본 스크립트로 1회만 수동 로그인하면 세션이
.sessions/tistory_shared_profile/ 에 영구 저장된다. 이후 publisher 는
저장된 세션으로 /manage 직접 접근 → 자동 발행.

사용:
    python -m scripts.tistory_manual_login                  # 일반 모드
    python -m scripts.tistory_manual_login --fresh          # 프로필 초기화 후 로그인
    python -m scripts.tistory_manual_login kkkseok          # 블로그 지정
    python -m scripts.tistory_manual_login kkkseok --fresh

흐름:
    1. (옵션) --fresh: persistent profile 삭제 → ID/PW 폼 강제 진입
    2. headless=false 로 persistent profile 기동
    3. Kakao OAuth URL 직접 진입
       - 프로필에 Kakao 세션 있으면 자동 전환 (사용자 개입 불가)
       - 없으면 ID/PW 입력 + 2단계 인증 화면 등장
    4. /manage 페이지 도달 자동 감지 (5분 polling), 또는 콘솔 Enter
    5. context 정리 후 종료 → 세션 저장 완료
"""
from __future__ import annotations

import os
import shutil
import sys
import time

from dotenv import load_dotenv
load_dotenv()

from common.browser_profile import PersistentBrowserProfile


def main(blog_name: str = "", fresh: bool = False) -> int:
    blog_name = blog_name or os.getenv("TISTORY_BLOG_NAME", "")
    if not blog_name:
        print("[ERROR] TISTORY_BLOG_NAME 미설정 — .env 확인 또는 인자로 전달")
        return 2

    blog_url = f"https://{blog_name}.tistory.com"
    profile = PersistentBrowserProfile("tistory_shared")

    print(f"[INFO] 프로필 디렉토리: {profile.user_data_dir}")
    print(f"[INFO] 대상 블로그: {blog_url}")

    if fresh and profile.user_data_dir.exists():
        # stale Kakao 토큰이 Tistory 콜백에 거부당해 /auth/login 으로 빠지는 경우,
        # 프로필을 통째로 비워서 ID/PW 폼 강제 진입.
        print(f"[INFO] --fresh: 기존 프로필 삭제 중 ...")
        try:
            shutil.rmtree(profile.user_data_dir)
            print(f"[OK]   프로필 삭제 완료. ID/PW 폼이 새로 뜹니다.")
        except Exception as e:
            print(f"[WARN] 프로필 삭제 실패 ({e}) — 계속 진행")

    print("[INFO] Chromium 창이 곧 열립니다. 카카오 로그인을 완료하세요.")
    print("[INFO] /manage 페이지 도달 시 자동 종료. 막히면 콘솔 Enter 로 강제 종료.\n")

    # 직접 OAuth URL 진입 — Tistory 사이트의 JS SDK 가 붙이는 prompt=select_account
    # 를 회피하려는 의도된 우회 (publishers/tistory.py:51-54 참고).
    # 사이트 경유 방식은 prompt=select_account 때문에 Kakao 가 계정 선택 화면에
    # 머물러 OAuth 가 완결되지 않는 경우가 발생.
    from base64 import b64encode
    from urllib.parse import urlencode, urlparse
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

    blog_host = f"{blog_name}.tistory.com"

    def _scan_pages_for_manage(ctx) -> tuple[str, str] | None:
        """모든 page 의 URL 을 훑어서 manage 도달 page 와 그 URL 반환. 없으면 None."""
        for p in list(ctx.pages):
            try:
                u = p.url
            except Exception:
                continue
            try:
                parsed = urlparse(u)
            except Exception:
                continue
            if parsed.hostname == blog_host and parsed.path.startswith("/manage"):
                return u, parsed.path
        return None

    def _snapshot_all_urls(ctx) -> list[str]:
        urls = []
        for p in list(ctx.pages):
            try:
                urls.append(p.url)
            except Exception:
                continue
        return urls

    with profile.launch(headless=False) as context:
        page = context.new_page() if not context.pages else context.pages[0]

        # 새 페이지(popup/tab) 가 열리면 자동 추적 — Kakao SDK 가 popup 으로 열거나
        # OAuth 중간에 새 탭이 뜨는 케이스 대응
        new_pages: list = []
        context.on("page", lambda p: new_pages.append(p))

        try:
            page.goto(authorize_url, wait_until="domcontentloaded", timeout=30000)
        except Exception as e:
            print(f"[WARN] OAuth URL 진입 예외(무시 가능): {e}")

        print(f"[GUIDE] 카카오 로그인을 완료하세요.")
        print(f"        성공 시: 어딘가의 탭에서 '{blog_url}/manage' 도달.")
        print(f"        모든 탭을 자동 감시 중입니다.\n")

        # 자동 감지: context 의 모든 page 를 훑어 manage 도달 검사
        deadline = time.time() + 300
        success = False
        last_snapshot = ""
        try:
            while time.time() < deadline:
                snap = " | ".join(_snapshot_all_urls(context))
                if snap and snap != last_snapshot:
                    # 너무 길지 않게 줄여서 로그
                    print(f"[POLL] {snap[:200]}")
                    last_snapshot = snap

                hit = _scan_pages_for_manage(context)
                if hit:
                    url, path = hit
                    print(f"[OK] {blog_host}{path} 도달 — 블로그별 세션 발급 성공")
                    print(f"     URL: {url}")
                    success = True
                    break
                time.sleep(2)
        except (EOFError, KeyboardInterrupt):
            print("\n[ABORT] 사용자 취소")

        if not success:
            print(f"\n[FAIL] 5분 내 {blog_host}/manage 도달 실패.")
            print(f"       마지막 스냅샷: {last_snapshot[:200] if last_snapshot else '(없음)'}")
            print(f"       → '--fresh' 옵션으로 프로필 초기화 후 재시도 권장.")
            return 1

        # 추가 검증 — 살아있는 page 하나 골라 /manage 재방문해 쿠키 flush 보장
        live_page = None
        for p in list(context.pages):
            try:
                _ = p.url
                live_page = p
                break
            except Exception:
                continue
        if live_page is None:
            try:
                live_page = context.new_page()
            except Exception:
                live_page = None
        if live_page is not None:
            try:
                live_page.goto(redirect_url, wait_until="domcontentloaded", timeout=15000)
                cur = live_page.url
                parsed = urlparse(cur)
                print(f"[INFO] 최종 URL: {cur}")
                if parsed.hostname == blog_host and parsed.path.startswith("/manage"):
                    print(f"[OK] 블로그별 세션 쿠키 발급 확인")
                else:
                    print(f"[WARN] /manage 직접 접근 시 {parsed.hostname} 로 빠짐 — 세션 불안정")
            except Exception as e:
                print(f"[WARN] /manage 진입 예외(무시): {e}")

    print("\n[OK] 세션이 .sessions/tistory_shared_profile/ 에 저장되었습니다.")
    print("[OK] 이제 'python -m pipelines.riseset_to_tistory' 실행 시 자동 로그인됩니다.")
    return 0


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    fresh = "--fresh" in sys.argv[1:]
    blog = args[0] if args else ""
    sys.exit(main(blog, fresh=fresh))
