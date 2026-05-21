"""editor 의 reCAPTCHA 상태 진단 v2 — '완료' 버튼 클릭 후 publish 모달까지."""
from __future__ import annotations
import sys
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
    assert pub._page is not None
    page = pub._page

    page.goto(f"{pub.blog_url}/manage/newpost/?type=post",
              wait_until="domcontentloaded", timeout=15000)
    import time; time.sleep(3)

    print("=== 1) ___grecaptcha_cfg 구조 ===")
    cfg = page.evaluate(r"""
        () => {
            const cfg = window.___grecaptcha_cfg;
            if (!cfg) return {err: 'no cfg'};
            // 직접 stringify 안되니 deep traverse 로 sitekey 찾기
            const sitekeys = [];
            function walk(o, depth) {
                if (depth > 6 || !o || typeof o !== 'object') return;
                for (const k in o) {
                    try {
                        const v = o[k];
                        if (typeof v === 'string' && v.length > 30 && /^6L/.test(v)) {
                            sitekeys.push({path: k, val: v});
                        }
                        if (typeof v === 'object') walk(v, depth+1);
                    } catch (e) {}
                }
            }
            walk(cfg, 0);
            return {cfg_keys: Object.keys(cfg), sitekeys: sitekeys};
        }
    """)
    import json
    print(json.dumps(cfg, ensure_ascii=False, indent=2))

    # '완료' 클릭 후 reCAPTCHA 렌더
    print("\n=== 2) '완료' 버튼 클릭 ===")
    try:
        page.click('button:has-text("완료")', timeout=3000)
        time.sleep(3)
        print("  click OK")
    except Exception as e:
        print(f"  click fail: {e}")

    print("\n=== 3) 클릭 후 reCAPTCHA 상태 ===")
    info = page.evaluate(r"""
        () => {
            const out = {};
            out.iframes = Array.from(document.querySelectorAll('iframe[src*="recaptcha"]')).map(f => f.src.slice(0,150));
            out.sitekey_attrs = Array.from(document.querySelectorAll('[data-sitekey]')).map(e => ({sk: e.getAttribute('data-sitekey'), tag: e.tagName, cls: e.className}));
            out.recaptcha_widgets = Array.from(document.querySelectorAll('.g-recaptcha, [class*=recaptcha]')).slice(0,5).map(e => ({tag: e.tagName, cls: e.className, key: e.getAttribute('data-sitekey')}));
            const visBtns = Array.from(document.querySelectorAll('button, a[role="button"]')).filter(b => b.offsetParent !== null).map(b => (b.innerText||'').trim()).filter(t => t && t.length < 30);
            out.visible_buttons = visBtns.slice(0, 40);
            // 모달 / 다이얼로그
            out.dialogs = Array.from(document.querySelectorAll('[role=dialog], .modal, .layer_publish, [class*="publish"]')).map(e => ({cls: e.className, id: e.id}));
            return out;
        }
    """)
    print(json.dumps(info, ensure_ascii=False, indent=2))

    pub.close()
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: python -m tools.probe_recaptcha <blog>")
        sys.exit(1)
    sys.exit(main(sys.argv[1]))
