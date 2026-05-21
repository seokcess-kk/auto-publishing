"""headed 모드로 editor 진입 → 제목/본문 작성 → '완료' 클릭 → publish modal
열린 상태에서 sitekey 추출 + grecaptcha.execute 토큰 생성 가능성 검증."""
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

# 반드시 publisher import 전에 헤들리스 끄기 — env 가 publisher __init__ 에서 읽힘
os.environ["TISTORY_HEADLESS"] = "false"

from publishers.tistory import TistoryPublisher  # noqa: E402


def main(blog: str) -> int:
    pub = TistoryPublisher(blog)
    if not pub.login():
        return 1
    assert pub._page is not None
    page = pub._page

    # 1) editor 진입
    page.goto(f"{pub.blog_url}/manage/newpost/?type=post",
              wait_until="domcontentloaded", timeout=20000)
    time.sleep(4)

    # 2) 제목 — textarea#post-title-inp 가 실제 form
    title_set = False
    for sel in [
        "textarea#post-title-inp",
        "#post-title-inp",
        "input#post-title-inp",
        "input[name='title']",
        "textarea[placeholder*='제목']",
    ]:
        try:
            loc = page.locator(sel).first
            if loc.count() == 0:
                continue
            loc.fill("[probe] reCAPTCHA 토큰 추출 시험", timeout=2000)
            print(f"✓ 제목 입력 ({sel})")
            title_set = True
            break
        except Exception:
            continue
    if not title_set:
        # DOM 에 보이는 input 모두 dump
        inputs = page.evaluate(r"""
            () => Array.from(document.querySelectorAll('input, textarea'))
                .filter(e => e.offsetParent !== null)
                .map(e => ({tag: e.tagName, id: e.id, name: e.name, ph: e.placeholder, cls: e.className.slice(0,60)}))
                .slice(0, 20)
        """)
        print("제목 input 미발견 — visible inputs:")
        for i in inputs:
            print(f"  {i}")

    # 3) 본문 — tinymce 또는 contenteditable
    try:
        page.evaluate(r"""
            () => {
                if (window.tinymce && window.tinymce.activeEditor) {
                    window.tinymce.activeEditor.setContent('<p>probe 본문</p>');
                    return true;
                }
                return false;
            }
        """)
        print("✓ tinymce setContent")
    except Exception as e:
        print(f"본문 setContent 실패: {e}")
    time.sleep(1)

    # 4) '완료' 클릭 (publish-layer-btn) — title 채워야 모달 열림
    try:
        page.locator('#publish-layer-btn').scroll_into_view_if_needed(timeout=2000)
        page.click('#publish-layer-btn', timeout=3000)
        print("✓ #publish-layer-btn 클릭")
    except Exception as e:
        print(f"클릭 실패: {e}")
    # publish modal 렌더 대기 — 길게
    for _ in range(10):
        time.sleep(1)
        n_iframe = page.evaluate(
            "() => document.querySelectorAll('iframe').length"
        )
        n_layer = page.evaluate(
            r"() => document.querySelectorAll('[class*=\"layer_post\"], [class*=\"layer_publish\"], [class*=\"layer_setting\"]').length"
        )
        if n_iframe > 0 or n_layer > 0:
            break

    # 5) modal 열린 상태에서 reCAPTCHA / sitekey 탐색
    info = page.evaluate(r"""
        () => {
            const out = {};
            out.iframes = Array.from(document.querySelectorAll('iframe')).map(f => f.src.slice(0,150)).filter(s => s.includes('recaptcha'));
            out.sitekey_attrs = Array.from(document.querySelectorAll('[data-sitekey]')).map(e => ({sk: e.getAttribute('data-sitekey'), tag: e.tagName}));
            // ___grecaptcha_cfg.clients 안쪽 깊게 walk
            const sks = [];
            function walk(o, depth, path) {
                if (depth > 8 || !o) return;
                if (typeof o === 'string' && /^6[LM][a-zA-Z0-9_-]{30,}/.test(o)) {
                    sks.push({path, val: o});
                    return;
                }
                if (typeof o !== 'object') return;
                for (const k in o) {
                    try { walk(o[k], depth+1, path + '.' + k); } catch(e) {}
                }
            }
            try { walk(window.___grecaptcha_cfg, 0, 'cfg'); } catch(e) {}
            out.sitekeys_found = sks;
            // publish modal 의 visible elements
            const visBtns = Array.from(document.querySelectorAll('button')).filter(b => b.offsetParent !== null).map(b => (b.innerText||'').trim()).filter(t => t && t.length < 30);
            out.visible_buttons_after = visBtns.slice(0, 50);
            // 모달 / 다이얼로그
            out.dialogs = Array.from(document.querySelectorAll('[class*="layer"], [class*="modal"], [class*="popup"], [role="dialog"]')).filter(e => e.offsetParent !== null).map(e => ({cls: e.className.slice(0,80), id: e.id}));
            return out;
        }
    """)
    print("\n=== publish modal 진입 후 ===")
    print(json.dumps(info, ensure_ascii=False, indent=2))

    # 6.5) 공개 radio 클릭 후 button 변화
    print("\n=== '공개' radio 클릭 시도 ===")
    for sel in [
        'label:has-text("공개")',
        'input[value="20"]',
        'input[name="visibility"][value="20"]',
        'label[for*="public"]',
        '[role="radio"]:has-text("공개")',
    ]:
        try:
            loc = page.locator(sel).first
            if loc.count() == 0:
                continue
            if loc.is_visible():
                loc.click(timeout=2000)
                print(f"  ✓ 공개 클릭 ({sel})")
                break
        except Exception as e:
            continue
    time.sleep(2)

    after_public = page.evaluate(r"""
        () => ({
            iframes: Array.from(document.querySelectorAll('iframe')).map(f => f.src.slice(0,150)).filter(s => s.includes('recaptcha')),
            sitekey_attrs: Array.from(document.querySelectorAll('[data-sitekey]')).map(e => ({sk: e.getAttribute('data-sitekey'), tag: e.tagName})),
            visible_buttons: Array.from(document.querySelectorAll('button')).filter(b => b.offsetParent !== null).map(b => ({text: (b.innerText||'').trim().slice(0,30), cls: b.className.slice(0,80), id: b.id})).filter(b => b.text).slice(0, 30),
        })
    """)
    print(json.dumps(after_public, ensure_ascii=False, indent=2))

    # 6) sitekey 가 발견되면 grecaptcha.execute 시도
    sk = ""
    if after_public.get("sitekey_attrs"):
        sk = after_public["sitekey_attrs"][0]["sk"]
    if not sk and info.get("sitekey_attrs"):
        sk = info["sitekey_attrs"][0]["sk"]
    if info.get("sitekey_attrs"):
        sk = info["sitekey_attrs"][0]["sk"]
    elif info.get("sitekeys_found"):
        sk = info["sitekeys_found"][0]["val"]
    if sk:
        print(f"\n=== grecaptcha.execute 시도 (sitekey={sk[:20]}...) ===")
        result = page.evaluate(
            r"""async (sk) => {
                if (typeof window.grecaptcha === 'undefined') return {err: 'grecaptcha undefined'};
                try {
                    const token = await window.grecaptcha.execute(sk, {action: 'submit'});
                    return {token: token ? token.slice(0,80) + '...(' + token.length + ')' : 'empty'};
                } catch (e) {
                    return {err: String(e)};
                }
            }""",
            sk,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print("\nsitekey 미발견 — grecaptcha.execute 시도 불가")

    print("\n5초 후 종료...")
    time.sleep(5)
    pub.close()
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: python -m tools.probe_recaptcha_headed <blog>")
        sys.exit(1)
    sys.exit(main(sys.argv[1]))
