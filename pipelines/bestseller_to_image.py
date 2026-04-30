"""
파이프라인: 알라딘 베스트셀러 → 캐러셀 카드 N장 생성.

표지 1장 + 도서 5장 (기본). 알라딘 API는 표지 이미지를 직접 제공하지
않으므로 텍스트 기반 랭킹 카드로 구성한다 (순위 + 제목 + 저자/출판사).

실행:
    python -m pipelines.bestseller_to_image
    BESTSELLER_CATEGORY=소설 BESTSELLER_COUNT=5 python -m pipelines.bestseller_to_image
"""
from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

from common.instagram_publish import publish_card
from common.logger import log
from common.card_image import (
    CardCanvas, ACCENT, BG_LIGHT, FG_DARK, FG_MUTED, PADDING,
    page_indicator, watermark,
)
from sources.entertainment import AladinCrawler


SCHEDULE = {
    "env":  "SCHEDULE_BESTSELLER_INSTAGRAM",
    "func": "run",
    "args_from_env": (
        "BESTSELLER_CATEGORY:종합",
        "BESTSELLER_COUNT:5:int",
    ),
}

ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = ROOT / "data" / "cards" / "bestseller"

DEFAULT_CATEGORY = "종합"
DEFAULT_COUNT    = 5


def build_cover_card(category: str, week_str: str, total: int) -> CardCanvas:
    cv = CardCanvas()
    cv.gradient_bg("#7C2D12", "#DC2626")  # warm crimson

    cv.text_centered(360, week_str, size=34, color="#FED7AA")
    cv.text_centered(450, "이번 주 베스트셀러", size=72, color="#FFFFFF", bold=True)
    cv.text_centered(560, f"#{category}", size=44, color="#FED7AA")

    cv.rect((PADDING, 760, cv.size[0] - PADDING, 764), fill="#FB923C")
    cv.text_centered(820, f"TOP {total}", size=120, color="#FFFFFF", bold=True)

    page_indicator(cv, 1, total + 1)
    watermark(cv, "@auto_publishing")
    return cv


def build_book_card(rank: int, title: str, author: str, publisher: str,
                    page: int, total: int, category: str) -> CardCanvas:
    cv = CardCanvas()
    cv.fill_bg(BG_LIGHT)

    # 상단 컬러바
    cv.rect((0, 0, cv.size[0], 14), fill=ACCENT)

    # 카테고리 + 랭킹 라벨
    cv.text((PADDING, 100), f"#{category}", size=28, color=FG_MUTED)
    cv.text_centered(220, f"{rank}위", size=88, color=ACCENT, bold=True)

    # 제목 (큰 텍스트, 자동 줄바꿈)
    cv.text_block(
        (PADDING, 420), title,
        size=52, color=FG_DARK, bold=True,
        max_width=cv.size[0] - 2 * PADDING,
        line_height=1.35,
    )

    # 저자/출판사
    info_lines = []
    if author:
        info_lines.append(author)
    if publisher:
        info_lines.append(publisher)
    if info_lines:
        info_text = " · ".join(info_lines)
        cv.text_block(
            (PADDING, 1000), info_text,
            size=32, color=FG_MUTED,
            max_width=cv.size[0] - 2 * PADDING,
            line_height=1.4,
        )

    page_indicator(cv, page, total)
    watermark(cv)
    return cv


def run(category: str = DEFAULT_CATEGORY, count: int = DEFAULT_COUNT) -> list[Path]:
    """알라딘 베스트셀러 캐러셀 카드 생성."""
    crawler = AladinCrawler()
    books = crawler.fetch(category=category, count=count)
    if not books:
        log("베스트셀러 데이터 없음", "error")
        from common.notifier import notify_pipeline_result
        notify_pipeline_result("베스트셀러→카드", 0, count + 1, details="알라딘 응답 없음")
        return []

    today = datetime.now()
    week_str = f"{today.year}년 {today.isocalendar().week}주차"
    paths: list[Path] = []
    total_pages = len(books) + 1

    cover = build_cover_card(category, week_str, len(books))
    cover_path = OUTPUT_DIR / f"{today:%Y-%m-%d}_00_cover.png"
    paths.append(Path(cover.save(cover_path)))

    for book in books:
        card = build_book_card(
            rank=book.get("rank", 0),
            title=book.get("title", ""),
            author=book.get("author", ""),
            publisher=book.get("publisher", ""),
            page=book.get("rank", 0) + 1,
            total=total_pages,
            category=category,
        )
        out = OUTPUT_DIR / f"{today:%Y-%m-%d}_{book.get('rank', 0):02d}.png"
        paths.append(Path(card.save(out)))

    log(f"베스트셀러 카드 {len(paths)}장 저장 ({OUTPUT_DIR})", "ok")

    # Instagram 은 캐러셀 미지원 → 표지 1장 발행
    cover_path = paths[0]
    top1 = books[0]
    caption = (
        f"📚 {week_str} 알라딘 베스트셀러\n"
        f"#{category} TOP {len(books)}\n\n"
        f"1위 — {top1.get('title', '')}\n"
        f"{top1.get('author', '')} · {top1.get('publisher', '')}"
    )
    hashtags = ["베스트셀러", "이번주베스트셀러", "독서", "책추천", "알라딘",
                category.replace(" ", "")]

    publish_card(
        pipeline_name="베스트셀러→Instagram",
        image_path=cover_path,
        caption=caption,
        hashtags=hashtags,
        dryrun_env="BESTSELLER_DRYRUN",
        details_summary=f"{category} TOP {len(books)}",
    )
    return paths


if __name__ == "__main__":
    run(
        category=os.getenv("BESTSELLER_CATEGORY", DEFAULT_CATEGORY),
        count=int(os.getenv("BESTSELLER_COUNT", str(DEFAULT_COUNT))),
    )
