"""
티스토리 세션 keep-alive — Kakao SSO 만료 조기 감지 + 토큰 회전 유도.

티스토리 publisher 는 .sessions/tistory_shared_profile/ 의 persistent profile
에 의존한다. Kakao SSO 토큰은 명목상 수 주간 유효하지만, 무인 환경의 idle
프로필에서는 능동 갱신이 일어나지 않아 실제로는 ~24h 안에 만료되는 경우가
관찰됐다. 이 파이프라인은 매일 1회 /manage 에 접근해서:

  1. 만료된 경우: notify_login_required 로 사용자에게 미리 알림 (다음 발행
     스케줄 도래 전 수동 로그인 기회 확보)
  2. 유효한 경우: 페이지 로드 과정에서 Kakao SDK 가 토큰을 회전시켜
     persistent profile 의 쿠키가 갱신됨 → 만료 시점이 뒤로 밀림

스케줄: .env 의 SCHEDULE_TISTORY_KEEPALIVE (권장: 06:00 — 첫 발행 08:30 이전).
"""
from __future__ import annotations

import time

from dotenv import load_dotenv
load_dotenv()

from common.browser_profile import PersistentBrowserProfile
from common.logger import log
from common.notifier import notify_login_required
from common.tistory_blogs import SUPPORTED_ROLES, resolve_blog_name


SCHEDULE = {
    "env":  "SCHEDULE_TISTORY_KEEPALIVE",
    "func": "run",
}


def _collect_unique_blogs() -> list[str]:
    """role 매핑/폴백을 거쳐 실제로 운영 중인 블로그 ID 집합 반환."""
    blogs: set[str] = set()
    for role in SUPPORTED_ROLES:
        try:
            blogs.add(resolve_blog_name(role))
        except ValueError:
            continue
    return sorted(blogs)


def _check_one(blog_name: str) -> tuple[bool, str]:
    """profile 로 /manage 한 번 접근. 리다이렉트 결과로 세션 유효 여부 판단."""
    blog_url = f"https://{blog_name}.tistory.com"
    # publisher 와 동일하게 'tistory_shared' 프로필을 공유 (TISTORY_ISOLATED_PROFILE
    # 옵션은 publisher 쪽에서 처리; keep-alive 는 단순화를 위해 shared 만 본다).
    profile = PersistentBrowserProfile("tistory_shared")

    try:
        # publisher/diag 와 동일하게 headless=False. headless 로 점검하면
        # Tistory/Kakao 가 다른 브라우저 핑거프린트로 인식해 멀쩡한 세션도
        # /auth/login 으로 리다이렉트시키는 오탐이 발생한다 (publishers/tistory.py:84
        # 의 'headless 에서 자주 막힌다' 와 동일 원인).
        with profile.launch(headless=False) as context:
            page = context.new_page() if not context.pages else context.pages[0]
            try:
                page.goto(
                    f"{blog_url}/manage",
                    wait_until="domcontentloaded", timeout=15000,
                )
            except Exception as e:
                return False, f"/manage goto 예외: {e}"
            # Kakao SDK JS 가 토큰 회전을 실행할 시간 부여
            time.sleep(3)
            try:
                cur = page.url
            except Exception:
                return False, "URL 추출 실패"
            if "/auth/login" in cur:
                return False, "세션 만료 (/auth/login 리다이렉트)"
            if cur.rstrip("/") in ("https://www.tistory.com", "https://tistory.com"):
                return False, "blog-specific 세션 없음"
            if "tistory.com/manage" in cur:
                return True, "OK"
            return False, f"예상 외 URL: {cur[:120]}"
    except Exception as e:
        return False, f"context launch 예외: {e}"


def run() -> None:
    log("=== 티스토리 세션 keep-alive 시작 ===", "step")

    blogs = _collect_unique_blogs()
    if not blogs:
        log("티스토리 블로그 미설정 — skip", "warn")
        return

    log(f"점검 대상: {len(blogs)}개 블로그 ({', '.join(blogs)})", "info")

    ok_count = 0
    for blog in blogs:
        ok, msg = _check_one(blog)
        if ok:
            log(f"[{blog}] 세션 유효 — 토큰 회전 완료", "ok")
            ok_count += 1
        else:
            log(f"[{blog}] 세션 점검 실패: {msg}", "error")
            # throttle_hours=24 기본 — 동일 블로그에 하루 한 번만 알림
            notify_login_required(
                f"tistory:{blog}",
                instructions="python -m scripts.tistory_manual_login",
            )

    log(f"=== 티스토리 세션 keep-alive 완료: {ok_count}/{len(blogs)} 유효 ===", "step")


if __name__ == "__main__":
    run()
