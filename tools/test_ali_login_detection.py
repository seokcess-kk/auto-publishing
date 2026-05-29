"""알리 자동 로그인 진입 분기 검증 (자격증명 미입력).

common/aliexpress_login.py 의 수정된 page_state 판정 로직과 동일한 검사를
라이브 login.aliexpress.com 에 적용해, chooser 페이지를 'kakao' 로 분류하고
카카오 버튼 클릭 → accounts/kauth.kakao.com 도달까지 되는지만 확인한다.
ID/PW 입력·제출은 일절 하지 않으므로 2FA/세션 변경이 없다.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from playwright.sync_api import sync_playwright

LOGIN_URL = "https://login.aliexpress.com/"

# common/aliexpress_login.py 의 수정본과 동일한 판정 스크립트
DETECT_JS = r"""
    () => {
        const labels = ['동의 및 계속', '동의 및 시작'];
        const hasKakao = document.querySelectorAll('button[aria-label="kakao"]').length > 0;
        const hasAgree = Array.from(document.querySelectorAll('*'))
            .some(el => el.children.length === 0
                       && labels.includes((el.textContent || '').trim()));
        const hasNfmCheck = document.querySelectorAll('div.nfm-checkbox').length > 0;
        if (hasKakao) return 'kakao';
        if (hasAgree || hasNfmCheck) return 'terms';
        return null;
    }
"""

with sync_playwright() as p:
    headless = os.getenv("HEADLESS", "true").lower() == "true"
    print(f"[*] headless={headless}")
    browser = p.chromium.launch(
        headless=headless,
        args=["--disable-blink-features=AutomationControlled"],
    )
    ctx = browser.new_context(
        user_agent=("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"),
        locale="ko-KR",
    )
    page = ctx.new_page()
    page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=30000)

    # 수정본과 동일하게 10초 폴링으로 상태 판정
    page_state = None
    deadline = time.time() + 10
    while time.time() < deadline:
        try:
            page_state = page.evaluate(DETECT_JS)
        except Exception:
            page_state = None
        if page_state:
            break
        time.sleep(0.5)

    print(f"[1] 진입 URL: {page.url}")
    print(f"[2] page_state 판정: {page_state!r}  (기대값: 'kakao')")

    if page_state != "kakao":
        print("[X] 실패 — chooser 가 'kakao' 로 분류되지 않음")
        browser.close()
        sys.exit(1)

    # 수정본과 동일하게 카카오 버튼 클릭
    try:
        page.click('button[aria-label="kakao"]', timeout=5000)
        print("[3] 카카오 버튼 클릭 OK")
    except Exception as e:
        print(f"[X] 카카오 버튼 클릭 실패: {e}")
        browser.close()
        sys.exit(1)

    # 카카오 로그인 페이지 도달 폴링 (any page) — 자격증명은 입력하지 않음
    reached = None
    deadline = time.time() + 30
    seen = set()
    while time.time() < deadline:
        for pg in ctx.pages:
            try:
                u = pg.url
            except Exception:
                continue
            if u and u not in seen:
                seen.add(u)
                print(f"      redirect → {u[:90]}")
            if "kauth.kakao.com" in u or "accounts.kakao.com" in u:
                reached = pg
                break
        if reached:
            break
        time.sleep(0.5)

    if reached:
        # 카카오 로그인 폼 존재만 확인 (입력 X)
        try:
            id_cnt = reached.locator('input[name="loginId"]').count()
            pw_cnt = reached.locator('input[name="password"]').count()
        except Exception:
            id_cnt = pw_cnt = -1
        print(f"[4] 카카오 로그인 페이지 도달 OK: {reached.url[:80]}")
        print(f"    로그인 폼 — loginId 입력칸 {id_cnt}개, password 입력칸 {pw_cnt}개")
        print("[OK] 검증 성공 — chooser 판정 → 카카오 버튼 클릭 → 카카오 로그인 페이지 도달")
        rc = 0
    else:
        print("[X] 실패 — 카카오 버튼 클릭 후 카카오 페이지로 이동하지 못함")
        rc = 1

    browser.close()
    sys.exit(rc)
