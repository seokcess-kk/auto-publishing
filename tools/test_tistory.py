"""
티스토리 Playwright 로그인 + 발행 단일 테스트.

흐름:
  1. TISTORY_BLOG_NAME 블로그에 Playwright로 Kakao 로그인 (최초 수동)
  2. 카테고리 목록 조회 (세션 유효성 확인)
  3. 테스트 포스트 1건 발행 (이미지 포함)

실행:
  python -m tools.test_tistory                     # .env의 TISTORY_BLOG_NAME 사용
  python -m tools.test_tistory <blog_id>           # 블로그 ID 직접 지정
  python -m tools.test_tistory <blog_id> --reset   # 세션 파일 삭제 후 재로그인
"""
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from common.logger import log  # noqa: E402
from publishers.tistory import TistoryPublisher  # noqa: E402


TEST_IMAGE_URL = (
    "https://images.unsplash.com/photo-1506744038136-46273834b3fb"
    "?auto=format&fit=crop&w=800&q=80"
)


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    flags = [a for a in sys.argv[1:] if a.startswith("--")]

    blog_name = args[0] if args else os.getenv("TISTORY_BLOG_NAME", "")
    if not blog_name or blog_name == "your_blog_name":
        log("블로그 ID 필요: python -m tools.test_tistory <blog_name>", "error")
        log("또는 .env의 TISTORY_BLOG_NAME 설정", "info")
        return 1

    pub = TistoryPublisher(blog_name)

    if "--reset" in flags:
        pub.logout()

    log(f"테스트 시작 — 블로그: {blog_name}.tistory.com", "step")
    if os.getenv("TISTORY_EMAIL"):
        log(f"  계정(참고): {os.getenv('TISTORY_EMAIL')}", "info")

    # 1) 로그인
    log("[1/3] 로그인", "step")
    if not pub.login():
        log("로그인 실패 — 수동 Kakao 로그인 시간을 확인하세요", "error")
        pub.close()
        return 1

    try:
        # 2) 카테고리 조회 (세션 유효성 확인 겸)
        log("[2/3] 카테고리 조회", "step")
        cats = pub.get_categories()
        if cats:
            log(f"카테고리 {len(cats)}개:", "ok")
            for c in cats[:10]:
                log(f"  - {c.get('name', c.get('label', '?'))} (id={c.get('id')})", "info")
        else:
            log("카테고리 0건 — 블로그에 카테고리가 없거나 세션 만료 가능", "warn")

        # 3) 테스트 포스트 발행
        log("[3/3] 테스트 포스트 발행", "step")
        from datetime import datetime
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        title = f"[테스트] 자동 발행 동작 확인 {now}"
        content = (
            "<p data-ke-size='size16'>이 글은 Auto Publishing 시스템의 "
            "티스토리 발행 테스트 포스트입니다.</p>"
            "<p data-ke-size='size16'>정상 노출되면 publisher가 올바르게 동작하는 중입니다.</p>"
            "<p data-ke-size='size16'>&nbsp;</p>"
        )

        result = pub.post(
            title=title,
            content=content,
            tags=["테스트", "자동발행"],
            category=os.getenv("TISTORY_CATEGORY", ""),
            image_url=TEST_IMAGE_URL,
            visibility=0,  # 0=비공개 (테스트라 안전하게)
        )
    finally:
        pub.close()

    if result.success:
        log(f"✅ 발행 성공: {result.url}", "ok")
        log("   (visibility=0 비공개로 발행됨 — 관리자만 확인 가능)", "info")
        return 0
    log(f"❌ 발행 실패: {result.message}", "error")
    return 1


if __name__ == "__main__":
    sys.exit(main())
