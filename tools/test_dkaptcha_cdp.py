"""DKAPTCHA 우회 — 사용자가 직접 띄운 Chrome 에 CDP 로 attach.

가설: Daum 캡차가 Playwright 가 시작한 Chrome 의 fingerprint (CommandLine flags
포함) 를 봇으로 감지해 위젯 렌더를 차단. 사용자가 평소처럼 시작한 Chrome 에
Playwright 가 remote-debugging-port 로 attach 하면 Chrome 의 시작 컨텍스트가
'사용자 실행' 그대로라 캡차 통과 가능성이 높다.

준비 (본인 작업):
  1. 모든 Chrome 창 닫기 (작업 관리자에서 chrome.exe 가 안 보일 때까지)
  2. PowerShell 새 창 열고:
       cd "C:\\Users\\assag\\solution\\auto-publishing"
       & "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe" `
         --remote-debugging-port=9222 `
         --user-data-dir="$PWD\\.sessions\\chrome_cdp_profile"
  3. 새로 뜬 Chrome 창에서 Tistory 로그인 (kkkseok 계정)
  4. 다른 터미널에서 이 스크립트 실행:
       python -m tools.test_dkaptcha_cdp

       (또는 기존 본인 평소 프로필을 쓰고 싶다면 user-data-dir 을 본인 Chrome
        프로필 경로로 — 보통 %LOCALAPPDATA%\\Google\\Chrome\\User Data — 지정.
        단, 그 경우 평소 쓰던 모든 탭 영향 받으니 신중.)

본 스크립트가 하는 일:
  • localhost:9222 에 CDP 로 attach
  • 어떤 탭이든 캡차 풀이 후 발행 완료 감지 (URL 변화)
  • 이후 같은 컨텍스트에서 자동 POST 발행 시도 — trust token 가설 확인
"""
from __future__ import annotations

import os
import re
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from dotenv import load_dotenv  # noqa: E402
load_dotenv(REPO_ROOT / ".env")


CDP_URL = os.getenv("CHROME_CDP_URL", "http://localhost:9222")


def main(blog: str) -> int:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("[ERROR] playwright 미설치")
        return 1

    blog_url = f"https://{blog}.tistory.com"
    blog_host = blog_url.replace("https://", "")

    print("=" * 70)
    print(f" CDP attach: {CDP_URL}")
    print("=" * 70)
    print()
    print(" 사전 확인:")
    print("  ✓ Chrome 이 --remote-debugging-port=9222 로 실행 중인가?")
    print("  ✓ 그 Chrome 에서 kkkseok.tistory.com 로그인 완료됐는가?")
    print()
    print(" 위 두 가지 확인 후 Enter")
    try:
        input(" >>> Enter: ")
    except (KeyboardInterrupt, EOFError):
        return 1

    with sync_playwright() as p:
        try:
            browser = p.chromium.connect_over_cdp(CDP_URL)
        except Exception as e:
            print(f"[ERROR] CDP attach 실패: {e}")
            print()
            print(" 가능한 원인:")
            print("  • Chrome 이 --remote-debugging-port=9222 로 실행되지 않음")
            print("  • 다른 프로세스가 9222 포트 점유 중")
            print("  • Chrome 이 idle 종료됨")
            return 1

        # 첫 context, 첫 page 가져오기 (사용자가 띄운 탭)
        if not browser.contexts:
            print("[ERROR] 연결된 context 없음")
            return 1
        ctx = browser.contexts[0]

        if not ctx.pages:
            page = ctx.new_page()
        else:
            # 가장 최근 활성 탭 선택 — tistory 가 떠있는 탭 우선
            page = ctx.pages[0]
            for p_ in ctx.pages:
                try:
                    if "tistory.com" in p_.url:
                        page = p_
                        break
                except Exception:
                    continue

        print(f" 연결 성공 — 현재 활성 탭 URL: {page.url[:120]}")
        print()

        # 1단계: 사용자가 직접 글 1건 발행 (캡차 풀이)
        print("=" * 70)
        print(" 1단계: Chrome 에서 직접 1건 발행 (캡차 풀이)")
        print("=" * 70)
        print(f"  (1) Chrome 의 {blog_url}/manage 또는 글쓰기 화면으로 이동")
        print("  (2) 제목/본문 작성 → 완료 → 공개 → 공개 발행")
        print("  (3) DKAPTCHA 풀이 → 답변 제출 → 발행 완료")
        print()
        print(" 최대 10분 대기 중...")

        success_url = ""
        last_logged: set[str] = set()
        deadline = time.time() + 600
        while time.time() < deadline:
            try:
                for p_ in ctx.pages:
                    try:
                        u = p_.url
                    except Exception:
                        continue
                    if u not in last_logged and "tistory.com" in u:
                        print(f"    URL: {u[:120]}")
                        last_logged.add(u)
                    if "/manage/posts" in u and "/newpost" not in u:
                        success_url = u
                        break
                    m = re.search(rf"https?://{re.escape(blog_host)}/(\d+)", u)
                    if m and "/manage" not in u:
                        success_url = u
                        break
                if success_url:
                    break
            except Exception:
                pass
            time.sleep(2)

        if not success_url:
            print("\n[ERROR] 1단계 timeout — 10분 안에 발행 완료 안 됨")
            return 1

        print(f"\n ✓ 1차 수동 발행 성공: {success_url}")

        # 캡차 trust 관련 쿠키 dump
        cookies = ctx.cookies()
        interesting = [c for c in cookies if any(
            k in c.get("name", "").lower()
            for k in ["dkap", "captcha", "trust", "verified", "tssession", "csrf", "token"]
        )]
        print(f"\n 관심 쿠키 ({len(interesting)}개):")
        for c in interesting:
            n = c.get("name", "?")
            v = c.get("value", "")[:40]
            d = c.get("domain", "")
            print(f"   {n} = {v}... domain={d}")

        # 2단계: 같은 CDP 컨텍스트로 자동 발행 (캡차 없이 통과하는지)
        print()
        print("=" * 70)
        print(" 2단계: 자동 발행 (캡차 trust 가설 검증)")
        print("=" * 70)
        print(" 10초 대기 후 자동 POST...")
        time.sleep(10)

        # 새 탭 열어서 newpost 진입
        try:
            test_page = ctx.new_page()
            test_page.goto(f"{blog_url}/manage/newpost/?type=post",
                            wait_until="domcontentloaded", timeout=20000)
            test_page.wait_for_selector("#publish-layer-btn", state="visible", timeout=15000)
            time.sleep(2)
        except Exception as e:
            print(f"[ERROR] newpost 진입 실패: {e}")
            return 1

        # CSRF 토큰 추출
        token = ""
        for c in ctx.cookies():
            if c.get("name") == "TOP-XSRF-TOKEN":
                token = c.get("value", "")
                break
        print(f"  CSRF 토큰: {token[:20]}... ({len(token)}자)")

        # 자동 발행 payload
        payload = {
            "id": "0",
            "title": f"[자동 trust 검증] {time.strftime('%Y-%m-%d %H:%M')}",
            "content": "<p>자동 발행 검증용. 비공개.</p>",
            "slogan": "",
            "visibility": 0,   # 비공개로 안전
            "category": 0,
            "tag": "",
            "published": 1,
            "password": "",
            "uselessMarginForEntry": 1,
            "daumLike": 401,
            "cclCommercial": 0,
            "cclDerive": 0,
            "thumbnail": None,
            "type": "post",
            "attachments": [],
            "recaptchaValue": "",
            "draftSequence": None,
        }

        # page.evaluate fetch — 진짜 브라우저 컨텍스트 그대로
        result = test_page.evaluate(
            r"""async ({url, payload, token}) => {
                const resp = await fetch(url, {
                    method: 'POST',
                    credentials: 'include',
                    headers: {
                        'accept': 'application/json, text/plain, */*',
                        'content-type': 'application/json;charset=UTF-8',
                        'x-csrf-token': token,
                    },
                    body: JSON.stringify(payload),
                });
                return {status: resp.status, body: (await resp.text()).slice(0, 300)};
            }""",
            {"url": f"{blog_url}/manage/post.json", "payload": payload, "token": token},
        )

        print()
        print("=" * 70)
        status = result.get("status", 0)
        body = result.get("body", "")
        if status == 200:
            print(" 🎉 자동 발행 성공!")
            print(f"    response body: {body[:200]}")
            print()
            print(" 결론: 사용자 수동 캡차 풀이 1회 → 같은 CDP 컨텍스트에서 자동 발행 가능")
            print("       publishers/tistory.py 를 CDP attach 모드로 전환하면 자동화 복구")
        else:
            print(f" ❌ 자동 발행 실패 (status={status})")
            print(f"    body: {body[:300]}")
            print()
            print(" 결론: per-publish 캡차 — CDP 라도 매번 사람 풀이 요구")
        print("=" * 70)

        return 0 if status == 200 else 2


if __name__ == "__main__":
    blog = sys.argv[1] if len(sys.argv) > 1 else os.getenv("TISTORY_BLOG_NAME", "kkkseok")
    sys.exit(main(blog))
