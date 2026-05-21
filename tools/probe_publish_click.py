"""publish-btn 클릭 직후 발생하는 fetch/navigation/DOM 변화를 모니터링."""
from __future__ import annotations
import json
import os
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from dotenv import load_dotenv  # noqa: E402
load_dotenv(REPO_ROOT / ".env")

from publishers.tistory import TistoryPublisher  # noqa: E402


def main(blog: str) -> int:
    pub = TistoryPublisher(blog)
    if not pub.login():
        return 1
    assert pub._page is not None and pub._context is not None
    page = pub._page
    ctx = pub._context

    # 모든 POST + console + page error 모니터링
    posts: list[dict] = []
    def on_req(req):
        if req.method == "POST":
            posts.append({
                "ts": time.time(),
                "url": req.url[:150],
                "headers_csrf": req.headers.get("x-csrf-token", "")[:20],
            })
    def on_resp(resp):
        if resp.request.method == "POST":
            try:
                body = resp.text()[:200]
            except Exception:
                body = ""
            posts.append({
                "ts": time.time(),
                "url": resp.url[:150],
                "status": resp.status,
                "body": body,
                "_resp": True,
            })
    def on_console(msg):
        if msg.type in ("error", "warning"):
            posts.append({"ts": time.time(), "console": msg.type, "text": msg.text[:200]})
    def on_pageerror(err):
        posts.append({"ts": time.time(), "pageerror": str(err)[:200]})
    ctx.on("request", on_req)
    ctx.on("response", on_resp)
    page.on("console", on_console)
    page.on("pageerror", on_pageerror)

    page.goto(f"{pub.blog_url}/manage/newpost/?type=post",
              wait_until="domcontentloaded", timeout=20000)
    page.wait_for_selector("#publish-layer-btn", state="visible", timeout=15000)
    time.sleep(2)

    # 제목/본문
    page.fill("textarea#post-title-inp", "[probe] publish-btn 클릭 모니터링")
    page.evaluate(
        "(html) => window.tinymce.activeEditor.setContent(html, {format:'raw'})",
        "<p>probe 본문</p>",
    )

    # 완료 → publish modal
    page.click("#publish-layer-btn")
    page.wait_for_selector(".inner_editor_layer", state="visible", timeout=10000)
    time.sleep(1)

    # 공개 radio
    page.click('input[value="20"]')
    time.sleep(0.5)

    # publish-btn 클릭 — 이 시점부터 캡처
    t0 = time.time()
    print(f"=== {time.strftime('%H:%M:%S')} publish-btn 클릭 ===")
    posts.clear()
    page.click("#publish-btn")

    # 15초 모니터
    for sec in range(15):
        time.sleep(1)
        try:
            cur = page.url
        except Exception:
            cur = ""
        # reCAPTCHA challenge iframe 등장?
        iframes = page.evaluate(
            "() => Array.from(document.querySelectorAll('iframe')).map(f => ({src: f.src.slice(0,80), vis: f.offsetParent !== null}))"
        )
        rc_visible = [i for i in iframes if 'recaptcha' in i['src'] and i['vis']]
        if rc_visible:
            print(f"  T+{sec}s URL={cur[:60]}  ⚠️ reCAPTCHA iframe visible! {rc_visible}")
        else:
            n_rc = sum(1 for i in iframes if 'recaptcha' in i['src'])
            print(f"  T+{sec}s URL={cur[:60]}  recaptcha_iframes={n_rc} (hidden)")

    print("\n=== 캡처된 이벤트 (15초간) ===")
    for p in posts:
        rel = p.get("ts", 0) - t0
        if p.get("_resp"):
            print(f"  +{rel:.1f}s  RESP [{p.get('status')}] {p['url']} body={p.get('body','')[:120]}")
        elif "url" in p:
            print(f"  +{rel:.1f}s  POST {p['url']} csrf={p.get('headers_csrf','')}")
        elif "console" in p:
            print(f"  +{rel:.1f}s  CON.{p['console']} {p['text']}")
        elif "pageerror" in p:
            print(f"  +{rel:.1f}s  PAGE.ERR {p['pageerror']}")

    # 최종 화면 캡처
    try:
        page.screenshot(path=str(REPO_ROOT / "screenshots" / "publish_click_final.png"), full_page=True)
        print(f"\n스크린샷: screenshots/publish_click_final.png")
    except Exception as e:
        print(f"screenshot 예외: {e}")

    pub.close()
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: python -m tools.probe_publish_click <blog>")
        sys.exit(1)
    sys.exit(main(sys.argv[1]))
