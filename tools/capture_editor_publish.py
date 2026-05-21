"""editor 의 실제 publish fetch 를 인터셉터로 캡처.

UI 를 직접 자동화해서 '완료' → '공개 발행' 클릭. fetch 가 보내는 url/headers/body
를 캡처해 우리 publisher 가 무엇을 빠뜨리는지 확인.
"""
from __future__ import annotations
import json
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from dotenv import load_dotenv  # noqa: E402
load_dotenv(REPO_ROOT / ".env")

from publishers.tistory import TistoryPublisher  # noqa: E402


def main(blog: str) -> int:
    # headless=false 로 띄워 publish modal 이 실제로 렌더되도록
    import os as _os
    _os.environ["TISTORY_HEADLESS"] = "false"
    pub = TistoryPublisher(blog)
    if not pub.login():
        return 1
    assert pub._page is not None and pub._context is not None
    page = pub._page
    ctx = pub._context

    # 1) 모든 fetch / xhr 요청 캡처 (publish 관련만)
    captured: list[dict] = []
    def on_req(req):
        if "manage" in req.url and req.method == "POST":
            try:
                pd = req.post_data
            except Exception:
                pd = None
            captured.append({
                "method": req.method,
                "url": req.url,
                "headers": dict(req.headers),
                "body": (pd or "")[:600],
            })
    def on_resp(resp):
        if "manage" in resp.url and resp.request.method == "POST":
            try:
                body = resp.text()
            except Exception:
                body = ""
            captured.append({
                "method": "RESP",
                "url": resp.url,
                "status": resp.status,
                "body": body[:300],
            })
    ctx.on("request", on_req)
    ctx.on("response", on_resp)

    # 2) editor 진입
    page.goto(f"{pub.blog_url}/manage/newpost/?type=post",
              wait_until="domcontentloaded", timeout=15000)
    time.sleep(3)

    # 3) 제목 입력
    try:
        page.fill("input#post-title-inp, input[name='title'], input[placeholder*='제목']",
                  "[TEST 자동발행 캡처]", timeout=5000)
        print("✓ 제목 입력")
    except Exception as e:
        print(f"제목 입력 실패: {e}")

    # 4) 본문 입력 — tinymce iframe 안의 body 에 직접 typeset
    try:
        page.evaluate(r"""
            () => {
                const ed = window.tinymce && window.tinymce.activeEditor;
                if (ed) ed.setContent('<p>fetch 캡처용 본문 — 비공개 발행 예정</p>');
            }
        """)
        print("✓ 본문 setContent")
    except Exception as e:
        print(f"본문 입력 실패: {e}")
    time.sleep(1)

    # 5) '완료' 클릭 — publish 패널 열기
    try:
        page.click('button:has-text("완료")', timeout=3000)
        time.sleep(2)
        print("✓ '완료' 클릭")
    except Exception as e:
        print(f"'완료' 클릭 실패: {e}")

    # 6) publish 패널에서 '비공개' 선택 (안전)
    try:
        page.click('label:has-text("비공개"), input[value="0"]', timeout=2000)
        print("✓ 비공개 선택")
    except Exception:
        pass

    # 7) '공개 발행' / '발행' / '저장' 같은 최종 버튼 클릭
    final_btn_candidates = [
        'button:has-text("공개 발행")',
        'button:has-text("발행")',
        'button:has-text("출간")',
        'button:has-text("게시")',
        'button:has-text("저장")',
        'button.publish',
        'button[type="submit"]',
    ]
    final_clicked = False
    for sel in final_btn_candidates:
        try:
            loc = page.locator(sel).first
            if loc.count() == 0:
                continue
            if loc.is_visible():
                loc.click(timeout=2000)
                print(f"✓ 최종 발행 클릭: {sel}")
                final_clicked = True
                break
        except Exception:
            continue
    if not final_clicked:
        # 사용 가능한 모든 버튼 dump
        btns = page.evaluate(r"""
            () => Array.from(document.querySelectorAll('button, a[role="button"]'))
                .filter(b => b.offsetParent !== null)
                .map(b => ({text: (b.innerText||'').trim().slice(0,30), cls: b.className.slice(0,80), id: b.id}))
                .filter(b => b.text.length > 0)
                .slice(0, 30)
        """)
        print("최종 발행 버튼 미발견 — visible buttons:")
        for b in btns:
            print(f"  text={b['text']!r}  cls={b['cls']!r}  id={b['id']!r}")

    # 8) 응답 대기 + 캡처 출력
    time.sleep(5)
    print(f"\n=== 캡처된 POST {len([c for c in captured if c.get('method')=='POST'])}건, RESP {len([c for c in captured if c.get('method')=='RESP'])}건 ===")
    for c in captured:
        print()
        if c.get("method") == "POST":
            print(f"POST {c['url']}")
            for k in ("x-csrf-token", "content-type", "origin", "referer", "x-requested-with", "x-tiara-token", "cookie"):
                if k in c["headers"]:
                    v = c["headers"][k][:100]
                    print(f"  {k}: {v}")
            print(f"  body[:500]: {c['body']}")
        else:
            print(f"RESP [{c.get('status')}] {c['url']}")
            print(f"  body: {c['body']}")

    pub.close()
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: python -m tools.capture_editor_publish <blog>")
        sys.exit(1)
    sys.exit(main(sys.argv[1]))
