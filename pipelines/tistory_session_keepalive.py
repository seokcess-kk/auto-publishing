"""
세션 keep-alive — 발행 자격 세션(티스토리/알리 제휴/뉴스픽) 만료 조기 감지.

세션 만료는 이 시스템의 대표적 '조용한 사망' 원인이다 (실측: 뉴스픽 세션
만료로 newspick→tistory 1주 7전 0승, 알리 제휴 만료로 알리 발행 이틀 전멸).
발행 슬롯이 실패하고 나서야 알게 되면 그날 발행량을 잃으므로, 매일 발행
시작 전에 세 세션을 점검해 만료 시 텔레그램으로 복구 명령을 선제 안내한다.

  - 티스토리: /manage 접근 (web 모드 전용 — bridge 모드는 Chrome 확장이
    자체 keepalive 하므로 skip; Playwright 가 shared 프로필을 열면 사용자
    Chrome 과 충돌해 STATUS_BREAKPOINT 로 죽는 문제도 있음)
  - 알리 제휴: data/aliexpress_storage.json 쿠키로 portals 링크생성 API 를
    requests 로 호출 — JSON 이면 유효 (발행 시 _shorten_link 와 동일 신호)
  - 뉴스픽: NewspickSource.ensure_session() — SESSION 쿠키가 휘발성이라
    persistent profile 로 짧게 재발급받는 실제 발행과 동일 경로

만료 발견 시 log(..., "error") 로 ledger 를 failure 로 만들어 daily_summary
에 노출하고, notify_login_required(24h throttle)로 복구 명령을 보낸다.

스케줄: .env 의 SCHEDULE_TISTORY_KEEPALIVE (권장: 06:00 — 첫 발행 08:30 이전.
PC 가 그 시각에 꺼져 있어도 scheduler catch-up 이 기동 직후 보충 실행한다).
"""
from __future__ import annotations

import json
import os
import time

from dotenv import load_dotenv
load_dotenv()

from common.browser_profile import PersistentBrowserProfile
from common.logger import log
from common.notifier import notify_login_required
from common.tistory_blogs import SUPPORTED_ROLES, resolve_blog_name

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ALI_STORAGE = os.path.join(_BASE_DIR, "data", "aliexpress_storage.json")


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


def _check_tistory() -> None:
    """티스토리 세션 점검 (web 모드 전용) — 만료 블로그는 [error] 로그 + 알림."""
    # bridge 모드: 실제 발행은 사용자 Chrome 의 확장이 담당하고, 확장이 자체 6h
    # keepalive(background.js sessionKeepalive)로 그 세션을 유지한다. Playwright 가
    # tistory_shared 프로필을 여는 것은 (a) bridge 발행에 쓰이지 않는 세션이고
    # (b) 사용자 Chrome 과 프로필 충돌로 launch 즉시 STATUS_BREAKPOINT 로 죽는다
    # (2회 연속 실패의 원인). 따라서 bridge 모드에선 Playwright keepalive 를 skip.
    if os.getenv("TISTORY_PUBLISHER", "web").strip().lower() == "bridge":
        log("[tistory] bridge 모드 — 세션 keepalive 는 Chrome 확장이 담당. skip", "info")
        return

    blogs = _collect_unique_blogs()
    if not blogs:
        log("[tistory] 블로그 미설정 — skip", "warn")
        return

    log(f"[tistory] 점검 대상: {len(blogs)}개 블로그 ({', '.join(blogs)})", "info")
    for blog in blogs:
        ok, msg = _check_one(blog)
        if ok:
            log(f"[tistory:{blog}] 세션 유효 — 토큰 회전 완료", "ok")
        else:
            log(f"[tistory:{blog}] 세션 점검 실패: {msg}", "error")
            # throttle_hours=24 기본 — 동일 블로그에 하루 한 번만 알림
            notify_login_required(
                f"tistory:{blog}",
                instructions="python -m scripts.tistory_manual_login",
            )


def _check_aliexpress() -> None:
    """알리 제휴 세션 점검 — storage 쿠키로 portals 링크생성 API 호출.

    발행 시 sources/aliexpress.py._shorten_link 가 쓰는 것과 동일 신호를
    requests 로 재현한다 (2026-07-10 실측: Playwright 없이 200+JSON 통과).
    로그인 안 된 세션이면 JSON 대신 로그인 HTML 페이지가 온다.
    """
    if not os.path.exists(_ALI_STORAGE):
        log("[aliexpress] storage 없음 — 제휴 세션 미설정. "
            "python tools/aliexpress_manual_login.py 필요", "error")
        notify_login_required(
            "알리익스프레스 (제휴=Google 계정)",
            "python tools/aliexpress_manual_login.py → 'Continue with Google' 로 로그인",
        )
        return
    try:
        import requests
        with open(_ALI_STORAGE, encoding="utf-8") as f:
            storage = json.load(f)
        s = requests.Session()
        for c in storage.get("cookies", []):
            try:
                s.cookies.set(c["name"], c["value"],
                              domain=c.get("domain", ""), path=c.get("path", "/"))
            except Exception:
                continue
        track_id = os.getenv("ALIEXPRESS_TRACKING_ID", "wordpress")
        url = ("https://portals.aliexpress.com/tools/linkGenerate/"
               "generatePromotionLink.htm"
               f"?trackId={track_id}"
               "&targetUrl=https%3A%2F%2Fwww.aliexpress.com")
        r = s.get(url, headers={
            "accept": "application/json, text/plain, */*",
            "referer": "https://portals.aliexpress.com/affiportals/web/link_generator.htm",
            "user-agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                           "AppleWebKit/537.36 (KHTML, like Gecko) "
                           "Chrome/124.0.0.0 Safari/537.36"),
        }, timeout=15)
        if r.ok and r.text.strip().startswith("{"):
            log("[aliexpress] 제휴 세션 유효 (portals JSON 응답)", "ok")
            return
        log(f"[aliexpress] 알리 제휴 세션 만료 감지 (HTTP {r.status_code}, "
            f"JSON 아님) — 수동 로그인 필요", "error")
        notify_login_required(
            "알리익스프레스 (제휴=Google 계정)",
            "python tools/aliexpress_manual_login.py → 'Continue with Google' 로 로그인",
        )
    except Exception as e:
        # 네트워크 일시 오류일 수 있어 만료 단정은 않되, warn 으로 흔적은 남긴다.
        log(f"[aliexpress] 세션 점검 불가 (네트워크?): {e}", "warn")


def _check_newspick() -> None:
    """뉴스픽 세션 점검 — 실제 발행과 동일한 ensure_session() 경로.

    SESSION 쿠키가 휘발성이라 requests 만으론 판정 불가. persistent profile 의
    Kakao 토큰으로 재발급을 시도하며, 실패 시 ensure_session 내부에서 이미
    [ERROR] 로그 + notify_login_required(24h throttle) 를 수행한다.
    """
    try:
        from sources.newspick import NewspickSource
        src = NewspickSource()
        if src.ensure_session():
            log("[newspick] 세션 유효 (SESSION 재발급 성공)", "ok")
        else:
            # ensure_session 이 이미 error 로그+알림 처리 — 요약만 남긴다.
            log("[newspick] 세션 점검 실패 — 위 ensure_session 로그 참조", "warn")
    except Exception as e:
        log(f"[newspick] 세션 점검 예외: {e}", "warn")


def run() -> None:
    log("=== 세션 keep-alive 시작 (tistory / aliexpress / newspick) ===", "step")
    for name, check in (("tistory", _check_tistory),
                        ("aliexpress", _check_aliexpress),
                        ("newspick", _check_newspick)):
        try:
            check()
        except Exception as e:
            log(f"[{name}] 점검 중 예외 (다음 항목 계속): {e}", "warn")
    log("=== 세션 keep-alive 완료 ===", "step")


if __name__ == "__main__":
    run()
