"""
Bright Data Scraping Browser 진단 probe v2 (일회성).

핵심 가설: BD Scraping Browser 는 세션(연결)당 page.goto 내비게이션을 1회만
허용한다 ('Page.navigate domain limit reached'). 현재 coupang.py 는 한 세션에서
  goto(메인) → goto(검색)  으로 2회 내비게이션 → 두번째가 항상 실패.

검증 방법: URL 마다 '새 connect_over_cdp + 단일 goto' 로 도메인 도달성 자체를
세션-한도와 분리해 측정한다.

실행: python -u tools/probe_brightdata_coupang.py
"""
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")

from dotenv import load_dotenv
load_dotenv()

from playwright.sync_api import sync_playwright

BD = os.getenv("COUPANG_BRIGHTDATA_WSS", "").strip()
_safe = BD.split("@")[-1] if "@" in BD else BD
_zone = BD.split("zone-")[1].split(":")[0] if "zone-" in BD else ""
print(f"[cfg] BD host={_safe} zone={_zone} configured={bool(BD)}")


def _fresh_goto(p, url, label, timeout=25000):
    """URL 마다 완전히 새 연결 + 단일 goto — 세션 1회 한도와 도메인 도달성 분리."""
    try:
        browser = p.chromium.connect_over_cdp(BD)
    except Exception as e:
        print(f"[{label}] CONNECT-FAIL :: {type(e).__name__}: {str(e)[:160]}")
        return
    try:
        ctx = browser.contexts[0] if browser.contexts else browser.new_context()
        page = ctx.new_page()
        resp = page.goto(url, timeout=timeout, wait_until="domcontentloaded")
        status = resp.status if resp else "N/A"
        n = len(page.content())
        print(f"[{label}] OK status={status} html={n}B")
    except Exception as e:
        print(f"[{label}] GOTO-FAIL :: {str(e)[:160]}")
    finally:
        try:
            browser.close()
        except Exception:
            pass


def _double_goto_same_session(p, label):
    """현재 coupang.py 패턴 재현: 한 세션에서 goto 2회."""
    try:
        browser = p.chromium.connect_over_cdp(BD)
    except Exception as e:
        print(f"[{label}] CONNECT-FAIL :: {str(e)[:160]}")
        return
    try:
        ctx = browser.contexts[0] if browser.contexts else browser.new_context()
        page = ctx.new_page()
        r1 = page.goto("https://www.coupang.com", timeout=25000, wait_until="domcontentloaded")
        print(f"[{label}] nav#1(main) OK status={r1.status if r1 else 'N/A'}")
        r2 = page.goto("https://www.coupang.com/np/search?q=%EC%BB%A4%ED%94%BC%EB%A8%B8%EC%8B%A0&channel=user",
                       timeout=25000, wait_until="domcontentloaded")
        print(f"[{label}] nav#2(search) OK status={r2.status if r2 else 'N/A'}")
    except Exception as e:
        print(f"[{label}] nav#2(search) FAIL :: {str(e)[:160]}")
    finally:
        try:
            browser.close()
        except Exception:
            pass


def main():
    if not BD:
        print("COUPANG_BRIGHTDATA_WSS 미설정 — 중단")
        return
    with sync_playwright() as p:
        # A) 각 도메인을 '새 세션 + 단일 goto' 로 — 진짜 도달 가능한가?
        _fresh_goto(p, "https://geo.brdtest.com/welcome.txt?product=scraping_browser&method=cdp", "A1:brdtest")
        _fresh_goto(p, "https://example.com", "A2:example")
        _fresh_goto(p, "https://www.coupang.com", "A3:coupang-main")
        _fresh_goto(p, "https://www.coupang.com/np/search?q=%EC%BB%A4%ED%94%BC%EB%A8%B8%EC%8B%A0&channel=user", "A4:coupang-search")
        # B) 현재 코드 패턴(한 세션 2회 goto) 재현
        _double_goto_same_session(p, "B:double-goto")
    print("=== probe v2 완료 ===")


if __name__ == "__main__":
    main()
