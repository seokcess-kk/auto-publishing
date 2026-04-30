"""
쿠팡 → 네이버 카페 발행 단일 테스트.

흐름:
  1. .sessions/naver_cafe_*.pkl 세션 로드 (없으면 로그인)
  2. 게시판 목록 조회 (세션 유효성 확인)
  3. 쿠팡 상품 1건 크롤링
  4. 쿠팡 상품 카드 HTML을 NAVER_CAFE_MENU_ID 게시판에 발행 (기본: 쇼핑정보=3)

실행:
  python -m tools.test_coupang_cafe                  # 기본 키워드 랜덤
  python -m tools.test_coupang_cafe "무선이어폰"      # 키워드 지정
  python -m tools.test_coupang_cafe --reset          # 세션 삭제 후 재로그인
"""
import os
import random
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from common.logger import log  # noqa: E402
from publishers.naver_cafe import NaverCafePublisher  # noqa: E402
from sources.coupang import CoupangSource  # noqa: E402


DEFAULT_KEYWORDS = [
    "무선이어폰", "블루투스 스피커", "USB 허브",
    "캠핑 의자", "보조배터리", "데스크 정리함",
]


def build_product_card(product: dict) -> str:
    """쿠팡 상품 1건을 카페용 HTML 카드로 변환."""
    name = product.get("name", "")
    price = product.get("price", "")
    rating = product.get("rating", "")
    review = product.get("review_count", "")
    image = product.get("image", "")
    link = product.get("affiliate_url") or product.get("url", "")

    parts = []
    if image:
        parts.append(f'<p><img src="{image}" style="max-width:100%"></p>')
    parts.append(f"<h3>{name}</h3>")
    meta = []
    if price:
        meta.append(f"💰 {price}")
    if rating and rating != "No data":
        meta.append(f"⭐ {rating} ({review} 리뷰)")
    if meta:
        parts.append(f"<p>{' | '.join(meta)}</p>")
    if link:
        parts.append(
            f'<p><a href="{link}" target="_blank" rel="nofollow">'
            f"👉 쿠팡에서 자세히 보기</a></p>"
        )
    parts.append(
        "<p style='color:#888;font-size:12px'>"
        "이 포스팅은 쿠팡 파트너스 활동의 일환으로, "
        "이에 따른 일정액의 수수료를 제공받습니다.</p>"
    )
    return "\n".join(parts)


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    flags = [a for a in sys.argv[1:] if a.startswith("--")]

    cafe_id = os.getenv("NAVER_CAFE_ID", "")
    username = os.getenv("NAVER_USERNAME", "")
    password = os.getenv("NAVER_PASSWORD", "")
    menu_id = os.getenv("NAVER_CAFE_MENU_ID", "3")
    if not all([cafe_id, username, password]):
        log("NAVER_CAFE_ID, NAVER_USERNAME, NAVER_PASSWORD 필요 (.env)", "error")
        return 1

    keyword = args[0] if args else random.choice(DEFAULT_KEYWORDS)

    log(f"테스트 시작 — 카페: {cafe_id}, 게시판: {menu_id}", "step")
    log(f"  계정: {username}", "info")
    log(f"  키워드: {keyword}", "info")

    cafe = NaverCafePublisher(cafe_id, username, password)

    if "--reset" in flags:
        sess = Path(".sessions") / f"naver_cafe_{cafe_id}.pkl"
        if sess.exists():
            sess.unlink()
            log(f"세션 파일 삭제: {sess}", "warn")

    # 1) 로그인
    log("[1/4] 카페 로그인", "step")
    if not cafe.login():
        log("카페 로그인 실패", "error")
        return 1

    # 2) 게시판 조회 (세션 유효성 확인)
    log("[2/4] 게시판 조회", "step")
    cats = cafe.get_categories()
    if cats:
        log(f"게시판 {len(cats)}개:", "ok")
        for c in cats[:10]:
            log(f"  - {c.get('name', '?')} (id={c.get('id')})", "info")
    else:
        log("게시판 0건 — 세션 만료 가능", "warn")

    # 3) 쿠팡 상품 크롤링
    log("[3/4] 쿠팡 상품 크롤링", "step")
    channel_id = os.getenv("COUPANG_CHANNEL_ID_NAVERCAFE", "navercafe")
    coupang = CoupangSource(channel_id=channel_id)
    products = coupang.search(keyword, count=5)
    products = [p for p in products if p.get("image") and p.get("name")]
    if not products:
        log(f"'{keyword}' 상품 없음", "error")
        return 1
    product = products[0]
    log(f"상품 선택: {product.get('name', '')[:50]}", "ok")

    # 4) 카페 발행
    log("[4/4] 카페 발행", "step")
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    title = f"[추천] {product.get('name', '')[:40]} - {now}"
    content = build_product_card(product)

    result = cafe.post(
        title=title,
        content=content,
        tags=["쿠팡", "추천템", keyword],
        menu_id=menu_id,
    )

    if result.success:
        log(f"✅ 발행 성공: {result.url}", "ok")
        return 0
    log(f"❌ 발행 실패: {result.message}", "error")
    return 1


if __name__ == "__main__":
    sys.exit(main())
