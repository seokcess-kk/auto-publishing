"""알리익스프레스 카카오 로그인 흐름 격리 관찰.

common/aliexpress_login.py 의 흐름을 그대로 따르되 ID/PW 자동입력은
일절 안 하고 모든 page 의 url/title/주요 input 셀렉터 존재 여부를 추적.

모드:
    기본 (FULL=0): 알리 진입 + 카카오 버튼 클릭만, 30초 관찰
    FULL=1:       카카오 버튼 → 약관 자동 동의 → 카카오 redirect 까지 60초 관찰
                  ID/PW 입력은 안 함

목적: 약관 동의 화면 후 카카오 redirect 가 실제 발생하는지 검증해
common/aliexpress_login.py 자동 흐름 재구성에 필요한 패턴 도출.

사용법:
    .venv/bin/python -m tools.debug_aliexpress_login          # 기본
    FULL=1 .venv/bin/python -m tools.debug_aliexpress_login  # 약관 동의까지
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(REPO_ROOT / ".env")
except ImportError:
    pass


def main() -> None:
    from playwright.sync_api import sync_playwright

    LOGIN_URL = "https://login.aliexpress.com/"
    out_dir = REPO_ROOT / "screenshots" / "aliexpress"
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = int(time.time())

    print(f"[debug] LOGIN_URL={LOGIN_URL}")

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=False,
            args=["--disable-blink-features=AutomationControlled"],
        )
        ctx = browser.new_context(
            user_agent=("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/124.0.0.0 Safari/537.36"),
            locale="ko-KR",
        )
        page = ctx.new_page()

        # 모든 page 의 변화를 기록
        nav_events: list[tuple[float, str, str]] = []
        ctx.on("page", lambda p:
            nav_events.append((time.time() - t0, "popup", p.url)))
        page.on("framenavigated", lambda f:
            nav_events.append((time.time() - t0, "framenavigated", f.url)) if f == page.main_frame else None)

        t0 = time.time()
        try:
            page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=30000)
        except Exception as e:
            print(f"[debug] goto 예외: {e}")

        elapsed = time.time() - t0
        print(f"[debug] goto 완료 {elapsed:.2f}s, page.url={page.url}")

        # 카카오 버튼 존재 확인
        try:
            cnt = page.locator('button[aria-label="kakao"]').count()
            print(f"[debug] button[aria-label='kakao'] count={cnt}")
        except Exception as e:
            print(f"[debug] kakao 버튼 카운트 예외: {e}")

        # 카카오 버튼 클릭 (publisher 와 동일)
        click_ts = None
        try:
            page.click('button[aria-label="kakao"]', timeout=5000)
            click_ts = time.time() - t0
            print(f"[debug] [t={click_ts:.2f}s] 카카오 버튼 클릭 OK")
        except Exception as e:
            print(f"[debug] 카카오 버튼 클릭 실패: {e}")

        # FULL 모드 — 약관 동의 자동 처리 + DOM 분석
        full_mode = os.getenv("FULL", "0") == "1"
        if full_mode:
            time.sleep(3)
            # DOM 진단 — 약관 화면 요소 catalogue
            try:
                dom_info = page.evaluate("""
                    () => {
                        const checkboxes = Array.from(document.querySelectorAll('div.nfm-checkbox, input[type="checkbox"]'));
                        const buttons = Array.from(document.querySelectorAll('button, a, [role="button"], div[class*="btn"]'));
                        const button_texts = buttons
                            .filter(b => b.offsetParent !== null)
                            .map(b => (b.innerText || '').trim().slice(0, 40))
                            .filter(t => t)
                            .slice(0, 20);
                        return {
                            checkbox_count: checkboxes.length,
                            checkbox_classes: checkboxes.slice(0, 5).map(c => c.className),
                            visible_button_texts: button_texts,
                            url: location.href,
                            title: document.title,
                        };
                    }
                """)
                print(f"[debug] DOM: title={dom_info.get('title')!r}")
                print(f"[debug] DOM: checkbox_count={dom_info.get('checkbox_count')}")
                print(f"[debug] DOM: checkbox_classes={dom_info.get('checkbox_classes')}")
                print(f"[debug] DOM: visible buttons (top 20):")
                for t in dom_info.get('visible_button_texts', []):
                    print(f"           - {t!r}")
            except Exception as e:
                print(f"[debug] DOM 진단 실패: {e}")

            # publisher 코드와 동일한 약관 처리 시도
            try:
                page.evaluate("""
                    () => {
                        document.querySelectorAll('div.nfm-checkbox').forEach(c => c.click());
                    }
                """)
                time.sleep(1)
                clicked_agree = page.evaluate("""
                    () => {
                        const labels = ['동의 및 계속', '동의 및 시작', '동의하고 시작', '동의 및 가입', '동의하고 계속'];
                        const all = Array.from(document.querySelectorAll('*'));
                        for (const lbl of labels) {
                            const target = all.find(el => el.textContent?.trim() === lbl && el.children.length === 0);
                            if (target) {
                                let cur = target;
                                for (let i = 0; i < 5 && cur; i++) {
                                    cur.click();
                                    cur = cur.parentElement;
                                }
                                return lbl;
                            }
                        }
                        return null;
                    }
                """)
                print(f"[debug] 약관 동의 클릭 시도: matched_label={clicked_agree!r}")
            except Exception as e:
                print(f"[debug] 약관 동의 처리 실패: {e}")

            print(f"[debug] === 약관 클릭 후 60초 관찰 시작 ===")
            deadline = time.time() + 60
        else:
            # 30초 동안 모든 페이지 URL 변화 추적
            deadline = time.time() + 30
        last_snap: list[tuple[str, str]] = []
        while time.time() < deadline:
            snap = []
            for pg in ctx.pages:
                try:
                    snap.append((pg.url, pg.title()[:40]))
                except Exception:
                    snap.append(("<unreadable>", ""))
            if snap != last_snap:
                t = time.time() - t0
                for url, title in snap:
                    kind = "OTHER"
                    if "kauth.kakao.com" in url or "accounts.kakao.com" in url:
                        kind = "KAKAO"
                    elif "ug-login-page" in url:
                        kind = "ALI_LOGIN"
                    elif "aliexpress.com" in url and "login" not in url.lower():
                        kind = "ALI_OTHER"
                    elif "login.aliexpress.com" in url:
                        kind = "ALI_LOGIN_PORTAL"
                    print(f"[t={t:6.2f}s] {kind:18s} | url={url[:120]} | title={title!r}")
                last_snap = snap

                # 카카오 페이지 발견하면 input 필드 셀렉터 존재 검사 (1회)
                for pg in ctx.pages:
                    if "kakao.com" in pg.url and pg not in [_p for _p, _ in getattr(main, '_inspected', [])]:
                        try:
                            id_cnt = pg.locator('input[name="loginId"]').count()
                            pw_cnt = pg.locator('input[name="password"]').count()
                            simple_cnt = pg.locator('a.wrap_profile').count()
                            print(f"  KAKAO inspect: loginId={id_cnt} password={pw_cnt} simpleAccount={simple_cnt}")
                            if not hasattr(main, '_inspected'):
                                main._inspected = []
                            main._inspected.append((pg, time.time()))
                        except Exception as e:
                            print(f"  KAKAO inspect error: {e}")

            time.sleep(0.5)

        # 종료 시점 스크린샷
        for i, pg in enumerate(ctx.pages):
            try:
                out = out_dir / f"DEBUG_ali_login_{ts}_page{i}.png"
                pg.screenshot(path=str(out), full_page=True)
                print(f"[debug] 스크린샷: {out}")
            except Exception as e:
                print(f"[debug] 스크린샷 page{i} 실패: {e}")

        # nav events 정리
        if nav_events:
            print("\n=== framenavigated/popup events ===")
            for t, kind, url in nav_events:
                print(f"  t={t:6.2f}s  {kind:14s}  {url[:120]}")

        try:
            browser.close()
        except Exception:
            pass


if __name__ == "__main__":
    main()
