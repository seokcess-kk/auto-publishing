"""editor 페이지의 bundled JS 를 다운로드해 post 엔드포인트 후보를 grep.

usage: python -m tools.probe_editor_bundle kkkseok
"""
from __future__ import annotations
import re
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
        print("login FAILED")
        return 1
    assert pub._page is not None and pub._context is not None
    page = pub._page
    ctx = pub._context

    # editor 로 이동
    page.goto(f"{pub.blog_url}/manage/newpost/?type=post",
              wait_until="domcontentloaded", timeout=15000)
    import time; time.sleep(2)

    # 모든 script src 수집
    srcs = page.evaluate(
        "() => Array.from(document.scripts).map(s => s.src).filter(Boolean)"
    )
    print("=== scripts ===")
    for s in srcs[:30]:
        print(" ", s[:140])

    # 후보: editor.*.js, app.*.js, post.*.js
    candidates = [s for s in srcs if re.search(r'(editor|post|main|app|chunk)', s, re.I)]
    print(f"\n=== {len(candidates)} editor-related scripts ===")

    # 각 스크립트 다운로드해 'post.json|posts|/manage' 같은 URL 패턴 grep
    seen_urls: set[str] = set()
    for src in candidates[:6]:
        try:
            r = ctx.request.get(src, timeout=10000)
            if not r.ok:
                continue
            txt = r.text()
        except Exception as e:
            print(f"  fetch fail: {src[:80]}: {e}")
            continue
        # /manage/...json, /api/v2/post, 등 광범위 검색
        for m in re.finditer(r'["\'`](/(?:manage|api)/[^"\'`\s]+\.json[^"\'`\s]*|/(?:manage|api)/v?\d?/[^"\'`\s]+)["\'`]', txt):
            url = m.group(1)
            if url not in seen_urls:
                seen_urls.add(url)
                # 컨텍스트도 함께
                start = max(0, m.start() - 40)
                end = min(len(txt), m.end() + 40)
                ctx_snippet = txt[start:end].replace("\n", " ")
                print(f"  [{Path(src).name[:30]}] {url}")
                print(f"    ctx: ...{ctx_snippet}...")
    pub.close()
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: python -m tools.probe_editor_bundle <blog>")
        sys.exit(1)
    sys.exit(main(sys.argv[1]))
