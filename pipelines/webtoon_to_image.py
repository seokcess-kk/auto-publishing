"""
파이프라인: 네이버 웹툰 인기 순위 → 캐러셀 카드 N장 생성.

표지 1장 + 웹툰 5장 (기본). 네이버 웹툰 API는 thumbnailUrl을 제공하므로
각 카드에 썸네일을 함께 렌더링한다.

실행:
    python -m pipelines.webtoon_to_image
    WEBTOON_WEEKDAY=mon WEBTOON_COUNT=5 python -m pipelines.webtoon_to_image
"""
from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
load_dotenv()

from common.instagram_publish import publish_card
from common.logger import log
from common.card_image import (
    CardCanvas, ACCENT_2, BG_LIGHT, FG_DARK, FG_MUTED, PADDING,
    page_indicator, watermark,
)
from sources.entertainment import NaverWebtoonCrawler


SCHEDULE = {
    "env":  "SCHEDULE_WEBTOON_INSTAGRAM",
    "func": "run_today",   # weekday 자동 결정 + Instagram 발행
}

ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = ROOT / "data" / "cards" / "webtoon"

DEFAULT_COUNT = 5

WEEKDAY_KOR = {
    "mon": "월요웹툰", "tue": "화요웹툰", "wed": "수요웹툰",
    "thu": "목요웹툰", "fri": "금요웹툰", "sat": "토요웹툰",
    "sun": "일요웹툰", "dailyPlus": "매일+",
}


def build_cover_card(weekday_label: str, total: int) -> CardCanvas:
    cv = CardCanvas()
    cv.gradient_bg("#065F46", "#10B981")  # green vibe

    cv.text_centered(360, "이번 주 인기", size=44, color="#A7F3D0")
    cv.text_centered(440, weekday_label, size=92, color="#FFFFFF", bold=True)

    cv.rect((PADDING, 700, cv.size[0] - PADDING, 704), fill="#34D399")
    cv.text_centered(780, f"TOP {total}", size=120, color="#FFFFFF", bold=True)
    cv.text_centered(940, "별점 기준 인기 순위", size=32, color="#A7F3D0")

    page_indicator(cv, 1, total + 1)
    watermark(cv, "@auto_publishing")
    return cv


def build_webtoon_card(rank: int, title: str, author: str, thumbnail: str,
                       page: int, total: int, weekday_label: str) -> CardCanvas:
    cv = CardCanvas()
    cv.fill_bg(BG_LIGHT)

    # 좌측 액센트 바
    cv.rect((0, 0, 14, cv.size[1]), fill=ACCENT_2)

    # 카테고리
    cv.text((PADDING, 100), f"#{weekday_label}", size=28, color=FG_MUTED)

    # 썸네일 (정사각, 라운드)
    thumb_size = 460
    thumb_x = (cv.size[0] - thumb_size) // 2
    thumb_y = 200
    if thumbnail:
        ok = cv.image(thumbnail, (thumb_x, thumb_y), (thumb_size, thumb_size), radius=24)
        if not ok:
            # 실패 시 placeholder
            cv.rect(
                (thumb_x, thumb_y, thumb_x + thumb_size, thumb_y + thumb_size),
                fill="#E5E7EB", radius=24,
            )
    else:
        cv.rect(
            (thumb_x, thumb_y, thumb_x + thumb_size, thumb_y + thumb_size),
            fill="#E5E7EB", radius=24,
        )

    # 랭킹 뱃지 (썸네일 좌상단)
    badge_size = 96
    cv.rect(
        (thumb_x - 20, thumb_y - 20,
         thumb_x - 20 + badge_size, thumb_y - 20 + badge_size),
        fill=ACCENT_2, radius=48,
    )
    cv.text(
        (thumb_x - 20 + badge_size // 2, thumb_y - 20 + badge_size // 2),
        str(rank), size=48, color="#FFFFFF", bold=True, anchor="mm",
    )

    # 제목 (자동 줄바꿈)
    cv.text_block(
        (PADDING, thumb_y + thumb_size + 60), title,
        size=46, color=FG_DARK, bold=True,
        max_width=cv.size[0] - 2 * PADDING,
        line_height=1.35,
        center=True,
    )

    # 저자
    if author:
        cv.text_centered(
            thumb_y + thumb_size + 200, author,
            size=30, color=FG_MUTED,
        )

    page_indicator(cv, page, total)
    watermark(cv)
    return cv


def _resolve_weekday(weekday: Optional[str]) -> tuple[Optional[str], str]:
    """weekday 인자 → (api_value, 한글 라벨)."""
    if weekday is None or weekday == "":
        weekday_idx = datetime.now().weekday()
        api_value = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"][weekday_idx]
    else:
        api_value = weekday
    label = WEEKDAY_KOR.get(api_value, "인기 웹툰")
    return api_value, label


def run(weekday: Optional[str] = None, count: int = DEFAULT_COUNT) -> list[Path]:
    """네이버 웹툰 인기 순위 캐러셀 카드 생성."""
    api_weekday, weekday_label = _resolve_weekday(weekday)
    crawler = NaverWebtoonCrawler()
    items = crawler.fetch(weekday=api_weekday, count=count)
    if not items:
        log("웹툰 데이터 없음", "error")
        from common.notifier import notify_pipeline_result
        notify_pipeline_result("웹툰→카드", 0, count + 1, details="네이버 응답 없음")
        return []

    today = datetime.now()
    paths: list[Path] = []
    total_pages = len(items) + 1

    cover = build_cover_card(weekday_label, len(items))
    cover_path = OUTPUT_DIR / f"{today:%Y-%m-%d}_{api_weekday}_00_cover.png"
    paths.append(Path(cover.save(cover_path)))

    for item in items:
        card = build_webtoon_card(
            rank=item.get("rank", 0),
            title=item.get("title", ""),
            author=item.get("author", ""),
            thumbnail=item.get("thumbnail", ""),
            page=item.get("rank", 0) + 1,
            total=total_pages,
            weekday_label=weekday_label,
        )
        out = OUTPUT_DIR / f"{today:%Y-%m-%d}_{api_weekday}_{item.get('rank', 0):02d}.png"
        paths.append(Path(card.save(out)))

    log(f"웹툰 카드 {len(paths)}장 저장 ({OUTPUT_DIR})", "ok")

    # Instagram 은 캐러셀 미지원 → 표지 1장 발행
    cover_path = paths[0]
    top1 = items[0]
    caption = (
        f"📚 오늘의 {weekday_label}\n"
        f"네이버 웹툰 인기 TOP {len(items)}\n\n"
        f"1위 — {top1.get('title', '')}\n"
        f"{top1.get('author', '')}"
    )
    hashtags = ["네이버웹툰", "웹툰", weekday_label, "웹툰추천", "Webtoon"]

    publish_card(
        pipeline_name="웹툰→Instagram",
        image_path=cover_path,
        caption=caption,
        hashtags=hashtags,
        dryrun_env="WEBTOON_DRYRUN",
        details_summary=f"{weekday_label} TOP {len(items)}",
    )
    return paths


def run_today(count: int = DEFAULT_COUNT) -> list[Path]:
    """SCHEDULE 진입점: 오늘 요일을 자동으로 잡아 run() 호출."""
    return run(weekday=None, count=count)


if __name__ == "__main__":
    run(
        weekday=os.getenv("WEBTOON_WEEKDAY") or None,
        count=int(os.getenv("WEBTOON_COUNT", str(DEFAULT_COUNT))),
    )
