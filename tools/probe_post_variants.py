"""post.json payload 변형 일괄 테스트.

여러 (URL, payload, headers) 조합을 시도해 어떤 게 200 반환하는지 탐색.
모든 시도는 visibility=0 (비공개) 로 안전.
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


BASE_PAYLOAD = {
    "id": "0",
    "title": "[probe] payload test",
    "content": "<p>probe</p>",
    "slogan": "",
    "visibility": 0,        # 비공개
    "category": 0,
    "tag": "",
    "published": 1,
    "password": "",
    "uselessMarginForEntry": 1,
    "daumLike": 401,
    "cclCommercial": 0,
    "cclDerive": 0,
    "thumbnail": None,
    "type": "post",
    "attachments": [],
    "recaptchaValue": "",
    "draftSequence": None,
}


def main(blog: str) -> int:
    pub = TistoryPublisher(blog)
    if not pub.login():
        return 1
    assert pub._page is not None and pub._context is not None
    page = pub._page
    token = pub._csrf_token

    # editor 페이지 활성화 (page.evaluate fetch 의 same-origin 보장)
    page.goto(f"{pub.blog_url}/manage/newpost/?type=post",
              wait_until="domcontentloaded", timeout=15000)
    time.sleep(2)

    def call(url: str, payload: dict, label: str, extra_headers: dict | None = None) -> tuple[int, str]:
        try:
            res = page.evaluate(
                r"""async ({url, payload, token, extra}) => {
                    const hdrs = {
                        'accept': 'application/json, text/plain, */*',
                        'content-type': 'application/json;charset=UTF-8',
                        'x-csrf-token': token,
                    };
                    if (extra) Object.assign(hdrs, extra);
                    const resp = await fetch(url, {
                        method: 'POST',
                        credentials: 'include',
                        headers: hdrs,
                        body: JSON.stringify(payload),
                    });
                    return {status: resp.status, body: (await resp.text()).slice(0, 200)};
                }""",
                {"url": url, "payload": payload, "token": token, "extra": extra_headers or None},
            )
            print(f"[{label:50}] status={res['status']} body={res['body']}")
            return res["status"], res["body"]
        except Exception as e:
            print(f"[{label:50}] EXC: {e}")
            return 0, str(e)

    # 1) URL 변형
    print("=== URL 변형 ===")
    for url_suffix in (
        "/manage/post.json",
        "/manage/posts.json",
        "/manage/post/save.json",
        "/manage/post/publish.json",
        "/manage/v2/post.json",
        "/manage/v2/posts.json",
        "/manage/post/v2.json",
    ):
        call(pub.blog_url + url_suffix, BASE_PAYLOAD, f"URL: {url_suffix}")

    # 2) payload 변형
    print("\n=== payload 필드 변형 (URL=/manage/post.json) ===")
    url = pub.blog_url + "/manage/post.json"

    variants = [
        ("id=null", {**BASE_PAYLOAD, "id": None}),
        ("id 제거", {k: v for k, v in BASE_PAYLOAD.items() if k != "id"}),
        ("category=null", {**BASE_PAYLOAD, "category": None}),
        ("daumLike=0", {**BASE_PAYLOAD, "daumLike": 0}),
        ("uselessMarginForEntry=0", {**BASE_PAYLOAD, "uselessMarginForEntry": 0}),
        ("draftSequence 제거", {k: v for k, v in BASE_PAYLOAD.items() if k != "draftSequence"}),
        ("recaptchaValue 제거", {k: v for k, v in BASE_PAYLOAD.items() if k != "recaptchaValue"}),
        ("recaptchaValue=skip", {**BASE_PAYLOAD, "recaptchaValue": "skip"}),
        ("recaptchaValue=none", {**BASE_PAYLOAD, "recaptchaValue": "none"}),
        ("최소 payload", {"id": "0", "title": "min", "content": "<p>m</p>",
                       "visibility": 0, "category": 0, "tag": "",
                       "type": "post", "attachments": []}),
        ("minimal+published=0", {"id": "0", "title": "min", "content": "<p>m</p>",
                       "visibility": 0, "category": 0, "tag": "",
                       "published": 0, "type": "post", "attachments": []}),
    ]
    for label, payload in variants:
        call(url, payload, label)

    # 3) 헤더 변형
    print("\n=== 헤더 변형 (URL=/manage/post.json) ===")
    header_variants = [
        ("+x-requested-with", {"x-requested-with": "XMLHttpRequest"}),
        ("+x-tiara-token", {"x-tiara-token": "y"}),
        ("+x-app-id=tistory", {"x-app-id": "tistory"}),
        ("+accept-language=ko", {"accept-language": "ko-KR,ko;q=0.9"}),
        ("+pragma+cache", {"pragma": "no-cache", "cache-control": "no-cache"}),
    ]
    for label, extra in header_variants:
        call(url, BASE_PAYLOAD, label, extra)

    pub.close()
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: python -m tools.probe_post_variants <blog>")
        sys.exit(1)
    sys.exit(main(sys.argv[1]))
