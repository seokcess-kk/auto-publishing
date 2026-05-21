"""DKAPTCHA 세션 신뢰 토큰 가설 검증.

가설: 한 번 캡차를 풀면 .sessions/tistory_shared_profile/ 의 쿠키에 trust 가
박혀 이후 동일 컨텍스트의 자동 POST /manage/post.json 이 캡차 없이 통과한다.

흐름:
  1. persistent profile 헤들리스 OFF 로 띄움
  2. /manage/newpost 진입, 제목/본문 미리 채워둠
  3. 사용자가 직접 '완료' → '공개' → '공개 발행' 클릭 → 캡차 풀이 → 발행 완료
  4. URL 이 /manage/posts 또는 /<post_id> 로 이동하면 1차 성공 감지
  5. 컨텍스트 살린 채 즉시 같은 publisher 로 두 번째 자동 발행 시도
  6. 두 번째 발행이 캡차 없이 통과하면 → 세션 trust 가설 참 (자동화 가능)
     400 떨어지거나 캡차 다시 뜨면 → per-publish, 자동화 불가

usage: python -m tools.test_dkaptcha_persist [blog_name]
"""
from __future__ import annotations

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
        print("[ERROR] login 실패")
        return 1
    assert pub._page is not None and pub._context is not None
    page = pub._page
    ctx = pub._context

    print()
    print("=" * 70)
    print(" 1단계: 수동 발행")
    print("=" * 70)
    print()
    print(" 브라우저가 /manage 까지 열려 있습니다.")
    print(" 다음을 직접 진행해주세요:")
    print("  (1) /manage 화면에서 '글쓰기' 버튼 (또는 좌측 메뉴) 으로 editor 진입")
    print("  (2) 제목 + 본문 직접 작성 (또는 아무 내용으로 1줄)")
    print("  (3) 우상단 '완료' 클릭")
    print("  (4) 모달에서 '공개' 라디오 선택")
    print("  (5) '공개 발행' 클릭 → DKAPTCHA 풀이 → '답변 제출'")
    print("  (6) 발행 완료까지 진행 (글 URL 로 이동하거나 글 목록 화면으로 가면 OK)")
    print()
    print(" ※ 스크립트가 자동으로 newpost 로 진입하면 라우팅이 막힌다는 보고가 있어,")
    print("   본인이 직접 '글쓰기' 버튼으로 진입하는 방식으로 진행합니다.")
    print()

    print("  최대 10분 대기 — 발행 완료 시 자동 감지합니다...")
    print()

    blog_host = pub.blog_url.replace("https://", "")
    deadline = time.time() + 600  # 10분
    success_url = ""
    last_url = ""
    import re as _re
    # 사용자가 어느 탭에서 작업할지 모르므로 context.pages 전체 순회
    while time.time() < deadline:
        try:
            urls = []
            for p in ctx.pages:
                try:
                    urls.append(p.url)
                except Exception:
                    continue
            for u in urls:
                if u != last_url:
                    print(f"    URL: {u[:120]}")
                    last_url = u
            # 발행 후 manage/posts 또는 글 URL 로 이동했는지 — 모든 탭에서 검사
            for u in urls:
                if "/manage/posts" in u and "/newpost" not in u:
                    success_url = u
                    break
                m = _re.search(rf"https?://{_re.escape(blog_host)}/(\d+)", u)
                if m and "/manage" not in u:
                    success_url = u
                    break
            if success_url:
                break
        except Exception:
            pass
        time.sleep(2)

    if not success_url:
        print("\n[ERROR] 수동 발행 timeout — 5분 안에 발행 완료 안 됨")
        pub.close()
        return 1

    print()
    print("=" * 70)
    print(f" ✓ 1차 수동 발행 성공: {success_url}")
    print("=" * 70)
    print()

    # 캡차 통과 직후 쿠키 dump
    cookies = ctx.cookies()
    interesting = [c for c in cookies if any(
        k in c["name"].lower() for k in ["dkap", "captcha", "trust", "verified", "tssession", "csrf", "token"]
    )]
    print(f"  관심 쿠키 ({len(interesting)}개):")
    for c in interesting:
        print(f"    {c['name']} = {c['value'][:40]}... domain={c['domain']}")

    # 잠시 대기 — 사용자가 결과 인지하도록
    print("\n  10초 후 2차 자동 발행 시도 (캡차 재출현 여부 확인)...")
    time.sleep(10)

    print()
    print("=" * 70)
    print(" 2단계: 자동 발행 시도 (캡차 trust 가설 검증)")
    print("=" * 70)
    print()

    # 같은 publisher 인스턴스로 post() 호출 — context.request.post 직접
    result = pub.post(
        title=f"[자동화 신뢰토큰 테스트 2] {time.strftime('%Y-%m-%d %H:%M')}",
        content="<p>두 번째 글 — 캡차 없이 통과하는지 검증.</p>",
        tags=["테스트"],
        visibility=0,  # 비공개로 안전하게
    )

    print()
    print("=" * 70)
    if result.success:
        print(" 🎉 결과: 자동 발행 성공! — DKAPTCHA 세션 trust 가설 참")
        print(f"    URL: {result.url}")
        print(f"    이후 publisher 가 동일 컨텍스트 살린 채로 자동 발행 가능")
    else:
        print(" ❌ 결과: 자동 발행 실패 — per-publish 캡차 (수동 1회 풀이 무의미)")
        print(f"    메시지: {result.message[:300]}")
    print("=" * 70)

    pub.close()
    return 0 if result.success else 2


if __name__ == "__main__":
    blog = sys.argv[1] if len(sys.argv) > 1 else os.getenv("TISTORY_BLOG_NAME", "kkkseok")
    sys.exit(main(blog))
