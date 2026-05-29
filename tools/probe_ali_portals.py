"""사용자가 방금 로그인한 영속 프로파일로 로그인/​portals 상태를 진단.

.sessions/aliexpress_login_profile 을 재오픈해:
  1) www.aliexpress.com 로그인 여부 (계정 메뉴/유저명)
  2) portals 제휴 API 가 무엇을 반환하는지 (JSON vs 로그인 HTML)
  3) link_generator 페이지 최종 URL (로그인 redirect 여부)
  4) portals.aliexpress.com 도메인 쿠키 유무
을 덤프한다. 입력/변경 없음.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
try:
    from dotenv import load_dotenv
    load_dotenv(REPO / ".env")
except Exception:
    pass

from playwright.sync_api import sync_playwright

PROFILE = REPO / ".sessions" / "aliexpress_login_profile"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
TRACK = os.getenv("ALIEXPRESS_TRACKING_ID", "wordpress")
print(f"[*] profile={PROFILE}  trackId={TRACK}")

with sync_playwright() as p:
    ctx = p.chromium.launch_persistent_context(
        user_data_dir=str(PROFILE),
        headless=True,
        user_agent=UA,
        locale="ko-KR",
        args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
    )
    page = ctx.pages[0] if ctx.pages else ctx.new_page()

    # 1) www 로그인 여부
    try:
        page.goto("https://www.aliexpress.com", wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(3000)
        acct = page.evaluate(r"""
            () => {
                const t = (document.body.innerText || '');
                return {
                    hasSignIn: /Sign in|로그인|Welcome to AliExpress/i.test(t.slice(0, 4000)) ,
                    hasAccount: /My Orders|내 주문|Account|계정|Hi,|환영/i.test(t.slice(0, 4000)),
                    url: location.href,
                };
            }
        """)
        print(f"[1] www: url={acct['url']}")
        print(f"    signin문구={acct['hasSignIn']}  account문구={acct['hasAccount']}")
    except Exception as e:
        print(f"[1] www 진단 예외: {e}")

    # 4) 쿠키 도메인 분포
    try:
        cookies = ctx.cookies()
        names = sorted({c["name"] for c in cookies})
        domains = sorted({c["domain"] for c in cookies})
        portals_cookies = [c["name"] for c in cookies if "portals" in c["domain"]]
        print(f"[4] 총 쿠키 {len(cookies)}개, 도메인 {len(domains)}개")
        print(f"    도메인들: {domains[:15]}")
        print(f"    portals 도메인 쿠키: {portals_cookies}")
        login_markers = {"xman_t","_hvn_login","x_user_id","x_alimid","ae_u_p_s","_ali_apache_session","xlly_s","intl_locale"}
        print(f"    로그인성 쿠키 보유: {sorted(set(names) & login_markers)}")
    except Exception as e:
        print(f"[4] 쿠키 진단 예외: {e}")

    # 3) link_generator 페이지 최종 URL
    try:
        page.goto("https://portals.aliexpress.com/affiportals/web/link_generator.htm",
                  wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(3000)
        print(f"[3] link_generator 최종 URL: {page.url}")
        is_login = "login" in page.url.lower() or "ug-login" in page.url.lower()
        print(f"    → 로그인 redirect? {is_login}")
    except Exception as e:
        print(f"[3] link_generator 진단 예외: {e}")

    # 2) generatePromotionLink — homepage & product URL 둘 다
    def probe_link(target, label):
        url = ("https://portals.aliexpress.com/tools/linkGenerate/generatePromotionLink.htm"
               f"?trackId={TRACK}&targetUrl={target}")
        try:
            res = ctx.request.get(url, headers={
                "accept": "application/json, text/plain, */*",
                "referer": "https://portals.aliexpress.com/affiportals/web/link_generator.htm",
                "user-agent": UA,
            }, timeout=15000)
            body = res.text()
            print(f"[2:{label}] status={res.status} ok={res.ok} url={res.url[:80]}")
            print(f"          body[:300]={body.strip()[:300]!r}")
        except Exception as e:
            print(f"[2:{label}] 예외: {e}")

    from urllib.parse import quote
    probe_link(quote("https://www.aliexpress.com", safe=""), "home")
    probe_link(quote("https://www.aliexpress.com/item/1005006284147500.html", safe=""), "item")

    out = REPO / "screenshots" / "aliexpress"
    out.mkdir(parents=True, exist_ok=True)
    try:
        page.screenshot(path=str(out / "probe_portals.png"), full_page=True)
        print(f"[*] screenshot: {out / 'probe_portals.png'}")
    except Exception:
        pass

    ctx.close()
