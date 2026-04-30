"""티스토리 manage 페이지 진입 시 발생하는 네트워크 요청 캡처.

UI 수동 조작 없이도, 단순히 manage 페이지를 navigate 하는 것만으로 페이지가
보내는 모든 요청을 listen 해 헤더/쿠키/payload 패턴을 분석한다.

또한 post.json 을 직접 호출하되 다양한 헤더 조합을 시도해 어떤 게 통과하는지 탐색.

usage:
  .venv/bin/python -m tools.probe_tistory_post <blog_name>
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(REPO_ROOT / ".env")
except ImportError:
    pass

from publishers.tistory import TistoryPublisher  # noqa: E402


def main(blog: str) -> int:
    pub = TistoryPublisher(blog)
    if not pub.login():
        return 1

    page = pub._page
    ctx = pub._context

    # 1) /manage/newpost 진입 시 페이지가 보내는 요청 listen
    print("=== 1) manage/newpost 진입 시 요청 ===")
    captured = []

    def on_request(req):
        if "tistory.com" in req.url and "manage" in req.url:
            captured.append({
                "method": req.method,
                "url": req.url[:120],
                "headers": dict(req.headers),
                "post_data": (req.post_data or "")[:200],
            })

    page.on("request", on_request)

    page.goto(f"{pub.blog_url}/manage/newpost/?type=post", wait_until="domcontentloaded", timeout=15000)
    import time
    time.sleep(3)

    for c in captured[-10:]:
        print(f"  {c['method']} {c['url']}")
        for k, v in c['headers'].items():
            if any(t in k.lower() for t in ['csrf', 'token', 'origin', 'referer', 'cookie', 'accept', 'tiara', 'x-']):
                vv = v[:80] if v else ''
                print(f"    {k}: {vv}")

    # 쿠키 dump
    print("\n=== 쿠키 ===")
    for c in ctx.cookies():
        if any(t in c['name'].lower() for t in ['csrf', 'token', 'tiara', 't_', 'tssession']):
            print(f"  {c['name']} = {c['value'][:60]} domain={c['domain']}")

    # 2) post.json 빈 payload 호출 — 어떤 에러 메시지를 주는지
    print("\n=== 2) post.json 빈 payload + CSRF 토큰만 ===")
    csrf = ""
    for c in ctx.cookies():
        if c['name'] == "TOP-XSRF-TOKEN":
            csrf = c['value']
            break
    print(f"  CSRF: {csrf[:30]}...")

    # 발행 전 페이지가 호출하는 templates-v1 GET 한 번 — 세션 컨텍스트 활성화 시도
    print("\n  pre-warm: templates-v1.json GET")
    try:
        wresp = ctx.request.get(
            f"{pub.blog_url}/manage/post/templates-v1.json?page=1",
            headers={"accept": "application/json, text/plain, */*",
                     "referer": f"{pub.blog_url}/manage/newpost/?type=post"},
            timeout=10000,
        )
        print(f"  pre-warm status={wresp.status}")
    except Exception as e:
        print(f"  pre-warm exc: {e}")

    # 헤더 조합 시도
    trials = [
        ("기존 publisher 그대로", pub._api_headers()),
        ("x-tiara-token 추가", {**pub._api_headers(), "x-tiara-token": "y"}),
        ("x-requested-with 추가", {**pub._api_headers(), "x-requested-with": "XMLHttpRequest"}),
        ("x-csrf-token + accept-language", {**pub._api_headers(), "accept-language": "ko-KR,ko;q=0.9"}),
    ]
    minimal_payload = {
        "id": "0", "title": "probe", "content": "<p>probe</p>",
        "slogan": "", "visibility": 0, "category": 0, "tag": "",
        "published": 1, "password": "", "uselessMarginForEntry": 1,
        "daumLike": 401, "cclCommercial": 0, "cclDerive": 0,
        "thumbnail": None, "type": "post", "attachments": [],
        "recaptchaValue": "", "draftSequence": None,
    }
    for name, hdrs in trials:
        try:
            resp = ctx.request.post(
                f"{pub.blog_url}/manage/post.json",
                headers=hdrs,
                data=json.dumps(minimal_payload, ensure_ascii=False).encode("utf-8"),
                timeout=15000,
            )
            body = resp.text()[:150] if resp else ""
            print(f"  [{name}] status={resp.status} body={body}")
        except Exception as e:
            print(f"  [{name}] EXC: {e}")

    # 3) attach.json 호출 — 실제 응답 구조 확인
    print("\n=== 3) 이미지 업로드 응답 구조 ===")
    import io, urllib.request
    try:
        # 작은 dummy 이미지
        dummy = (b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00'
                 b'\x1f\x15\xc4\x89\x00\x00\x00\rIDATx\x9cc\xf8\xff\xff?\x00\x05\xfe\x02\xfe\xa3\x00\x00\x00\x00IEND\xaeB`\x82')
        resp = ctx.request.post(
            f"{pub.blog_url}/manage/post/attach.json",
            multipart={"file": {"name": "test.png", "mimeType": "image/png", "buffer": dummy}},
            headers={"accept": "application/json, text/plain, */*",
                     "origin": pub.blog_url,
                     "referer": f"{pub.blog_url}/manage/newpost/?type=post&returnURL=/manage/posts"},
            timeout=30000,
        )
        print(f"  attach status={resp.status}")
        body = resp.text()
        print(f"  attach body: {body[:500]}")
    except Exception as e:
        print(f"  attach exc: {e}")

    pub.close()
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: python -m tools.probe_tistory_post <blog_name>")
        sys.exit(1)
    sys.exit(main(sys.argv[1]))
