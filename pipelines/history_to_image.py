"""
파이프라인: 오늘의 역사 → 캐러셀 카드 이미지 N장 생성.

ko.wikipedia.org/wiki/N월_D일 에서 오늘 날짜의 역사적 사건을 수집해,
표지 1장 + 사건 N장(기본 3장) 형태의 캐러셀 카드를 생성한다.

실행:
    python -m pipelines.history_to_image
    HISTORY_CARD_COUNT=5 python -m pipelines.history_to_image
"""
from __future__ import annotations

import os
import random
from datetime import datetime
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
load_dotenv()

from common.instagram_publish import publish_card
from common.logger import log
from common.card_image import (
    CardCanvas, ACCENT_2, BG_LIGHT, FG_DARK, FG_MUTED, PADDING,
    page_indicator, watermark, get_font, measure, wrap_text,
    GRADIENT_HISTORY, GOLD, TINT_GOLD, CARD_BG_LIGHT, SAFE_TOP,
)
from common.wiki_image import find_event_image_url
from sources.knowledge import TodayInHistoryCrawler


SCHEDULE = {
    "env":  "SCHEDULE_HISTORY_INSTAGRAM",
    "func": "run",
    "args_from_env": ("HISTORY_CARD_COUNT:3:int",),
}

ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = ROOT / "data" / "cards" / "history"

DEFAULT_COUNT = 3   # 표지 외 사건 카드 수


def _normalize_year(year: str) -> str:
    """'1865년' / '1865' 모두 → '1865년' 으로 통일."""
    if not year:
        return ""
    year = year.strip()
    return year if year.endswith("년") else f"{year}년"


def build_cover_card(date_str: str, total: int,
                     preview_year: str = "", preview_event: str = "") -> CardCanvas:
    """역사 캐러셀 표지 카드. 인스타 피드 1:1 미리보기에서도 핵심 정보가 보이도록 설계."""
    cv = CardCanvas()
    # 짙은 인디고 → 보라 그라데이션 배경
    cv.gradient_bg(*GRADIENT_HISTORY, direction="diagonal")
    # 우상단 글로우 (역사적 빛 느낌)
    cv.radial_glow((cv.size[0] - 120, 200), 700, "#FBBF24", alpha=70)
    # 좌하단 글로우 (보조)
    cv.radial_glow((150, cv.size[1] - 200), 500, "#7C3AED", alpha=80)
    # 은은한 점 패턴 (배경 깊이)
    cv.dotted_pattern(color="#FFFFFF", spacing=42, dot_size=2, alpha=18)
    # 가장자리 비네팅 (집중감)
    cv.vignette(strength=70)

    # 좌측 액센트 라인 (얇게)
    cv.rect((0, 0, 6, cv.size[1]), fill=GOLD)

    # ─── 상단 라벨 (피드에서 잘릴 수 있는 영역) ─────────────────────────
    cv.text_centered(80, "TODAY  IN  HISTORY", size=26, color="#FBBF24", bold=True)

    # ─── 메인 날짜 (SAFE_TOP=140 아래) ───────────────────────────────────
    # "오늘은" 작게
    cv.text_centered(190, "오늘은", size=42, color="#E2E8F0")
    # 큰 날짜 — 카드 시각적 중심
    cv.text_centered(290, date_str, size=130, color="#FFFFFF", bold=True)
    # 골드 언더라인
    cv.rect((cv.size[0] // 2 - 80, 460, cv.size[0] // 2 + 80, 466), fill=GOLD)
    cv.text_centered(490, "어떤 일이 있었을까요?", size=38, color="#E2E8F0")

    # ─── 미리보기 카드 (콘텐츠 호기심 유발) ────────────────────────────
    # 첫 사건을 살짝 보여주는 반투명 카드
    if preview_year and preview_event:
        # 카드 영역
        card_top = 620
        card_bottom = 960
        cv.shadow_card(
            (PADDING, card_top, cv.size[0] - PADDING, card_bottom),
            fill="#FFFFFF", radius=24,
            shadow_offset=(0, 8), shadow_blur=24, shadow_alpha=60,
        )
        # 카드 라벨
        cv.pill_badge(
            (PADDING + 110, card_top + 50), "PREVIEW",
            fill=GOLD, fg="#0F172A",
            size=22, padding_x=18, padding_y=8,
        )
        # 큰 연도
        cv.text((PADDING + 40, card_top + 100), _normalize_year(preview_year),
                size=58, color="#1E1B4B", bold=True)
        # 사건 1줄 미리보기 (긴 경우 잘라냄)
        ev_font = get_font(30, bold=False)
        max_w = cv.size[0] - 2 * PADDING - 80
        lines = wrap_text(cv.draw, preview_event, ev_font, max_w)[:2]
        ev_y = card_top + 195
        for line in lines:
            cv.draw.text((PADDING + 40, ev_y), line, font=ev_font, fill="#475569")
            ev_y += 42

    # ─── 하단 정보 ───────────────────────────────────────────────────────
    cv.pill_badge(
        (cv.size[0] // 2, 1040), f"역사 속 사건 {total}건",
        fill="#FFFFFF", fg="#1E1B4B",
        size=28, padding_x=28, padding_y=12,
    )
    cv.text_centered(1110, "Swipe →", size=28, color="#FBBF24", bold=True)

    page_indicator(cv, 1, total + 1)
    watermark(cv, "@auto_publishing")
    return cv


def build_event_card(year: str, event: str,
                     page: int, total: int,
                     date_str: str,
                     image_url: Optional[str] = None) -> CardCanvas:
    """이벤트 카드 — 헤더 + 이미지(있으면) + 연도 + 본문 인포그래픽."""
    cv = CardCanvas()
    cv.fill_bg(BG_LIGHT)

    # 상단 인디고 헤더 박스
    HEADER_H = 180
    cv.rect((0, 0, cv.size[0], HEADER_H), fill="#1E1B4B")
    cv.radial_glow((cv.size[0] - 80, 80), 280, "#FBBF24", alpha=60)
    # 좌측 골드 액센트
    cv.rect((0, 0, 8, cv.size[1]), fill=GOLD)

    # 헤더 텍스트
    cv.text_centered(45, "TODAY  IN  HISTORY", size=22, color="#FBBF24", bold=True)
    cv.text_centered(95, date_str, size=44, color="#FFFFFF", bold=True)

    year_text = _normalize_year(year)

    # ─── 레이아웃 분기: 이미지 있음 vs 없음 ────────────────────────────
    if image_url:
        # ── 이미지 카드 (상단) ────────────────────────────────────────
        IMG_CARD_TOP = 220
        IMG_W = 880
        IMG_H = 460
        IMG_X = (cv.size[0] - IMG_W) // 2
        # 이미지 밑 그림자 카드 (배경)
        cv.shadow_card(
            (IMG_X - 10, IMG_CARD_TOP - 10,
             IMG_X + IMG_W + 10, IMG_CARD_TOP + IMG_H + 10),
            fill=CARD_BG_LIGHT, radius=24,
            shadow_offset=(0, 6), shadow_blur=22, shadow_alpha=40,
        )
        # 실제 이미지 삽입 (라운드 코너)
        ok = cv.image(image_url, (IMG_X, IMG_CARD_TOP), (IMG_W, IMG_H), radius=20)
        if not ok:
            # 이미지 로딩 실패 시 placeholder
            cv.rect((IMG_X, IMG_CARD_TOP, IMG_X + IMG_W, IMG_CARD_TOP + IMG_H),
                    fill="#E5E7EB", radius=20)
            cv.text_centered(IMG_CARD_TOP + IMG_H // 2 - 20,
                             "IMAGE NOT AVAILABLE",
                             size=24, color=FG_MUTED, bold=True)
        # 이미지 라벨 (좌상단 골드 배지)
        cv.pill_badge(
            (IMG_X + 90, IMG_CARD_TOP + 30), "PHOTO",
            fill=GOLD, fg="#0F172A",
            size=18, padding_x=14, padding_y=6,
        )

        # ── 연도 + 본문 (이미지 아래) ─────────────────────────────────
        # 연도 (왼쪽 작게)
        cv.text((PADDING + 20, 730), year_text,
                size=58, color="#1E1B4B", bold=True)
        # 골드 구분선
        cv.rect((PADDING + 20, 800, PADDING + 100, 804), fill=GOLD)

        # 본문 (인용 부호 없이 더 컴팩트)
        cv.text_block(
            (PADDING + 20, 830), event,
            size=34, color=FG_DARK, bold=False,
            max_width=cv.size[0] - 2 * PADDING - 40,
            line_height=1.5,
        )
    else:
        # 이미지 없음 → 기존 풀 텍스트 레이아웃 (큰 연도 + 큰 본문)
        # ── 연도 카드 (강조) ─────────────────────────────────────────
        YEAR_CARD_TOP = 260
        YEAR_CARD_H = 200
        cv.shadow_card(
            (PADDING - 10, YEAR_CARD_TOP, cv.size[0] - PADDING + 10,
             YEAR_CARD_TOP + YEAR_CARD_H),
            fill=CARD_BG_LIGHT, radius=24,
            shadow_offset=(0, 6), shadow_blur=20, shadow_alpha=30,
        )
        cv.text_centered(YEAR_CARD_TOP + 35, "YEAR", size=22, color=GOLD, bold=True)
        cv.text((cv.size[0] // 2, YEAR_CARD_TOP + 130), year_text,
                size=110, color="#1E1B4B", bold=True, anchor="mm")

        # ── 본문 카드 ──────────────────────────────────────────────
        BODY_CARD_TOP = 520
        BODY_CARD_BOTTOM = 1180
        cv.shadow_card(
            (PADDING - 10, BODY_CARD_TOP, cv.size[0] - PADDING + 10, BODY_CARD_BOTTOM),
            fill=CARD_BG_LIGHT, radius=24,
            shadow_offset=(0, 6), shadow_blur=20, shadow_alpha=30,
        )
        cv.text((PADDING + 10, BODY_CARD_TOP + 30), "“",
                size=110, color=GOLD, bold=True)
        cv.text_block(
            (PADDING + 30, BODY_CARD_TOP + 130), event,
            size=40, color=FG_DARK, bold=False,
            max_width=cv.size[0] - 2 * PADDING - 40,
            line_height=1.5,
        )

    page_indicator(cv, page, total)
    watermark(cv)
    return cv


def run(count: int = DEFAULT_COUNT) -> list[Path]:
    """오늘의 역사 캐러셀 카드 생성 (표지 1 + 사건 N). 저장된 경로 리스트 반환."""
    crawler = TodayInHistoryCrawler()
    events = crawler.fetch(count=count * 3)  # 여유분 수집해 랜덤 선택
    if not events:
        log("오늘의 역사 데이터 없음", "error")
        from common.notifier import notify_pipeline_result
        notify_pipeline_result("오늘의역사→카드", 0, count + 1, details="위키 응답 없음")
        return []

    # 너무 짧거나 너무 긴 이벤트는 카드 가독성 떨어져서 필터
    eligible = [e for e in events if 15 <= len(e.get("event", "")) <= 90]
    if len(eligible) < count:
        eligible = events
    chosen = random.sample(eligible, k=min(count, len(eligible)))

    today = datetime.now()
    date_str = today.strftime("%m월 %d일")

    paths: list[Path] = []
    total_pages = len(chosen) + 1

    # 표지 — 첫 사건을 미리보기로 노출
    first = chosen[0]
    cover = build_cover_card(
        date_str, len(chosen),
        preview_year=first.get("year", ""),
        preview_event=first.get("event", ""),
    )
    cover_path = OUTPUT_DIR / f"{today:%Y-%m-%d}_00_cover.png"
    paths.append(Path(cover.save(cover_path)))

    # 이벤트 카드들 (이벤트 텍스트 → 위키 이미지 검색 → 카드 합성)
    for idx, ev in enumerate(chosen, start=1):
        ev_text = ev.get("event", "")
        # 위키 이미지 검색 (실패 시 None → 기존 텍스트 레이아웃)
        try:
            img_url = find_event_image_url(ev_text)
        except Exception as e:
            log(f"[history] 이미지 검색 실패: {e}", "warn")
            img_url = None

        card = build_event_card(
            year=ev.get("year", ""),
            event=ev_text,
            page=idx + 1,
            total=total_pages,
            date_str=date_str,
            image_url=img_url,
        )
        out = OUTPUT_DIR / f"{today:%Y-%m-%d}_{idx:02d}.png"
        paths.append(Path(card.save(out)))

    log(f"오늘의 역사 카드 {len(paths)}장 저장 ({OUTPUT_DIR})", "ok")

    # Instagram 캐러셀 발행 — 표지 + 이벤트 카드 N장 모두
    # 캡션에는 모든 사건을 연도-내용 형태로 모두 노출
    event_lines = []
    for ev in chosen:
        y = _normalize_year(ev.get("year", ""))
        e = ev.get("event", "").strip()
        event_lines.append(f"• {y} — {e}")

    caption = (
        f"📅 {date_str}, 역사 속 오늘\n\n"
        f"역사 속 사건 {len(chosen)}건을 모았습니다.\n\n"
        + "\n".join(event_lines)
    )
    hashtags = ["오늘의역사", "역사", "TIL", "역사덕후", "wiki", "TodayInHistory"]

    publish_card(
        pipeline_name="오늘의역사→Instagram",
        image_path=[str(p) for p in paths],   # 캐러셀로 발행
        caption=caption,
        hashtags=hashtags,
        dryrun_env="HISTORY_DRYRUN",
        details_summary=f"{date_str} {len(chosen)}건 · 캐러셀 {len(paths)}장",
    )
    return paths


if __name__ == "__main__":
    run(count=int(os.getenv("HISTORY_CARD_COUNT", str(DEFAULT_COUNT))))
