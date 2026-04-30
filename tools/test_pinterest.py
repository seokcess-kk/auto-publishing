"""
Pinterest Playwright 발행 단일 테스트.

흐름:
  1. 쿠팡에서 키워드 1개로 상품 1개 수집 (affiliate_url + image_url)
  2. 이미지 다운로드 → /tmp/pinterest_test_img.jpg
  3. Pinterest Playwright 로그인 (Google 계정)
  4. "How RU" 보드가 없으면 생성
  5. 핀 1개 발행 (제목/설명/affiliate link)

실행:
  python -m tools.test_pinterest [키워드]
"""
import os
import sys
import time
from pathlib import Path
from typing import Optional

import requests
from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from common.logger import log  # noqa: E402
from sources.coupang import CoupangSource  # noqa: E402
from publishers.pinterest_playwright import PinterestPlaywrightPublisher  # noqa: E402


TEST_IMAGE_PATH = "/tmp/pinterest_test_img.jpg"
BOARD_NAME = os.getenv("PINTEREST_BOARD_NAME", "How RU")


def fetch_one_product(keyword: str) -> Optional[dict]:
    src = CoupangSource()
    items = src.search(keyword, count=5)
    if not items:
        log("쿠팡 상품 0건", "error")
        return None
    # 이미지 URL 있는 것 중 첫 번째
    for it in items:
        if it.get("image") and it.get("affiliate_url"):
            return it
    return items[0]


def download_image(url: str, dest: str) -> bool:
    try:
        if url.startswith("//"):
            url = "https:" + url
        r = requests.get(url, timeout=15, headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) "
                          "Chrome/125.0.0.0 Safari/537.36"
        })
        r.raise_for_status()
        Path(dest).write_bytes(r.content)
        log(f"이미지 다운로드: {dest} ({len(r.content)} bytes)", "ok")
        return True
    except Exception as e:
        log(f"이미지 다운로드 실패: {e}", "error")
        return False


def main():
    keyword = sys.argv[1] if len(sys.argv) > 1 else "무선 이어폰"
    log(f"테스트 시작 — 키워드: {keyword}", "step")

    # 1) 쿠팡 상품 수집
    product = fetch_one_product(keyword)
    if not product:
        log("상품 수집 실패로 중단", "error")
        return 1
    log(f"상품: {product['name'][:50]}", "ok")
    log(f"가격: {product.get('price')}, 링크: {product.get('affiliate_url')[:80]}", "info")

    # 2) 이미지 다운로드
    if not download_image(product["image"], TEST_IMAGE_PATH):
        return 1

    # 3) Pinterest 로그인
    pub = PinterestPlaywrightPublisher()
    log(f"Pinterest 로그인 시작 (method={pub.login_method}, email={pub.email})", "step")
    if not pub.login():
        log("Pinterest 로그인 실패", "error")
        pub.close()
        return 1

    # 4) 보드 선택/생성은 _select_board가 드롭다운 내부에서 처리
    log(f"보드 준비: {BOARD_NAME} (드롭다운 내부에서 자동 선택/생성)", "step")

    # 5) 핀 발행
    title = product["name"][:100]
    price = product.get("price", "")
    rating = product.get("rating", "")
    review = product.get("review_count", "")
    description_parts = []
    if price:
        description_parts.append(f"💰 {price}")
    if rating:
        description_parts.append(f"⭐ {rating} ({review} 리뷰)")
    description_parts.append("👉 자세히 보기")
    description = "\n".join(description_parts)

    log("핀 발행 요청", "step")
    result = pub.post(
        title=title,
        content=description,
        tags=["쿠팡", "추천템", "가성비"],
        media_path=TEST_IMAGE_PATH,
        link=product["affiliate_url"],
        board_name=BOARD_NAME,
    )

    pub.close()

    if result.success:
        log(f"✅ 발행 성공: {result.url or result.message}", "ok")
        return 0
    log(f"❌ 발행 실패: {result.message}", "error")
    return 1


if __name__ == "__main__":
    sys.exit(main())
