"""알리 로그인 진입 페이지 비대화형 진단.

login.aliexpress.com 으로 진입한 뒤 최종 착지 URL, 약관 체크박스 유무,
카카오 버튼 유무, 보이는 버튼 텍스트를 덤프한다. 클릭/입력 일절 안 함.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from playwright.sync_api import sync_playwright

LOGIN_URL = "https://login.aliexpress.com/"

with sync_playwright() as p:
    browser = p.chromium.launch(
        headless=True,
        args=["--disable-blink-features=AutomationControlled"],
    )
    ctx = browser.new_context(
        user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"),
        locale="ko-KR",
    )
    page = ctx.new_page()
    page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=30000)
    time.sleep(6)  # ug-login-page redirect 안정화

    info = page.evaluate(r"""
        () => {
            const vis = el => el.offsetParent !== null;
            const btns = Array.from(document.querySelectorAll(
                'button, a, [role="button"], div[class*="btn"], div[class*="social"]'))
                .filter(vis)
                .map(b => ({
                    tag: b.tagName,
                    aria: b.getAttribute('aria-label') || '',
                    cls: (b.className || '').toString().slice(0, 60),
                    txt: (b.innerText || '').trim().slice(0, 30),
                }))
                .filter(b => b.txt || b.aria);
            return {
                url: location.href,
                title: document.title,
                nfmCheckboxes: document.querySelectorAll('div.nfm-checkbox').length,
                anyCheckbox: document.querySelectorAll('input[type=checkbox]').length,
                kakaoBtn: document.querySelectorAll('button[aria-label="kakao"]').length,
                socialIcons: Array.from(document.querySelectorAll('[aria-label]'))
                    .filter(vis).map(e => e.getAttribute('aria-label')).slice(0, 20),
                buttons: btns.slice(0, 25),
                bodyText: (document.body.innerText || '').replace(/\s+/g, ' ').slice(0, 400),
            };
        }
    """)

    print("FINAL_URL:", info["url"])
    print("TITLE:", repr(info["title"]))
    print("nfm-checkbox:", info["nfmCheckboxes"], " input-checkbox:", info["anyCheckbox"])
    print("kakao button[aria-label=kakao]:", info["kakaoBtn"])
    print("aria-labels:", info["socialIcons"])
    print("--- visible buttons ---")
    for b in info["buttons"]:
        print(f"  <{b['tag']}> aria={b['aria']!r} cls={b['cls']!r} txt={b['txt']!r}")
    print("--- body text (400) ---")
    print(info["bodyText"])

    out = REPO / "screenshots" / "aliexpress"
    out.mkdir(parents=True, exist_ok=True)
    ss = out / "probe_landing.png"
    page.screenshot(path=str(ss), full_page=True)
    print("SCREENSHOT:", ss)

    browser.close()
