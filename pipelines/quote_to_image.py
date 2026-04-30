"""
파이프라인: 오늘의 명언 → 카드 이미지 1장 생성 → Instagram 발행.

ZenQuotes API 에서 오늘의 명언을 받아 1080×1350 카드 이미지로 저장하고
Instagram 에 자동 발행한다.

실행:
    python -m pipelines.quote_to_image
    QUOTE_DRYRUN=1 python -m pipelines.quote_to_image  # 카드만, 발행 생략
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

from common.instagram_publish import publish_card
from common.logger import log
from common.card_image import (
    CardCanvas, ACCENT, BG_LIGHT, FG_DARK, FG_MUTED, PADDING, watermark,
    GRADIENT_QUOTE, GOLD, get_font, measure, wrap_text,
)
from sources.knowledge import QuoteCrawler


SCHEDULE = {
    "env":  "SCHEDULE_QUOTE_INSTAGRAM",
    "func": "run",
}

ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = ROOT / "data" / "cards" / "quote"


def build_card(quote: str, author: str, date_str: str) -> CardCanvas:
    """오늘의 명언 카드 — 다크 + 골드 미니멀 럭셔리 톤."""
    cv = CardCanvas()
    # 다크 그라데이션 배경
    cv.gradient_bg(*GRADIENT_QUOTE, direction="vertical")
    # 좌상단 골드 글로우
    cv.radial_glow((180, 200), 600, GOLD, alpha=55)
    # 우하단 보조 글로우
    cv.radial_glow((cv.size[0] - 180, cv.size[1] - 200), 500, "#7C3AED", alpha=50)
    # 점 패턴 (배경 텍스처)
    cv.dotted_pattern(color="#FFFFFF", spacing=44, dot_size=2, alpha=15)
    # 비네팅
    cv.vignette(strength=80)

    # 좌측 골드 액센트 라인
    cv.rect((0, 0, 6, cv.size[1]), fill=GOLD)

    # ─── 상단 라벨 (인스타 피드 1:1 크롭 안전영역 내) ──────────────────
    # SAFE_TOP=140 이라 모든 헤더 텍스트는 y >= 150 이어야 잘리지 않음
    cv.text_centered(180, "DAILY  QUOTE", size=26, color=GOLD, bold=True)
    cv.text_centered(235, "오늘의 명언", size=42, color="#FFFFFF", bold=True)
    cv.text_centered(295, date_str, size=24, color="#94A3B8")

    # 골드 구분선 (짧게 중앙)
    cv.rect(
        (cv.size[0] // 2 - 50, 340, cv.size[0] // 2 + 50, 344),
        fill=GOLD,
    )

    # ─── 명언 본문 영역 정의 ─────────────────────────────────────────
    # 본문 좌우 여백은 PADDING(80) + 인덴트(60) = 140px 확보 → 가장자리 여유
    BODY_LEFT = PADDING + 60
    BODY_RIGHT = cv.size[0] - PADDING - 60
    BODY_AREA_TOP = 480       # 따옴표 아래
    BODY_AREA_BOTTOM = 1000   # 닫는 따옴표 위

    # ─── 큰 따옴표 (좌상단, 디자인 요소) ──────────────────────────────
    quote_font = get_font(160, bold=True)
    cv.draw.text(
        (BODY_LEFT - 10, 380), "“",
        font=quote_font, fill=GOLD,
    )

    # ─── 본문 명언 (중앙 영역) ────────────────────────────────────────
    # 명언 길이에 따라 폰트 사이즈 동적 조정
    quote_len = len(quote)
    if quote_len <= 50:
        body_size = 56
        line_h = 1.5
    elif quote_len <= 100:
        body_size = 48
        line_h = 1.5
    elif quote_len <= 180:
        body_size = 40
        line_h = 1.55
    else:
        body_size = 34
        line_h = 1.55

    body_font = get_font(body_size, bold=True)
    max_w = BODY_RIGHT - BODY_LEFT
    lines = wrap_text(cv.draw, quote, body_font, max_w)
    line_gap = int(body_size * line_h)
    total_h = line_gap * len(lines)

    # 수직 중앙 정렬 (BODY_AREA_TOP ~ BODY_AREA_BOTTOM 영역 안)
    area_h = BODY_AREA_BOTTOM - BODY_AREA_TOP
    body_top = BODY_AREA_TOP + max(0, (area_h - total_h) // 2)
    y = body_top
    for line in lines:
        cv.draw.text((BODY_LEFT, y), line, font=body_font, fill="#F8FAFC")
        y += line_gap

    # ─── 우하단 닫는 따옴표 (디자인 페어) ─────────────────────────────
    close_font = get_font(160, bold=True)
    close_w, _ = measure(cv.draw, "”", close_font)
    # 본문 아래로 충분히 띄움 (최소 980, 최대 BODY_AREA_BOTTOM)
    close_y = max(y + 30, 980)
    close_y = min(close_y, BODY_AREA_BOTTOM)
    cv.draw.text(
        (BODY_RIGHT - close_w + 30, close_y - 110), "”",
        font=close_font, fill=GOLD,
    )

    # ─── 저자 (인스타 피드 1:1 크롭 안전영역 내) ──────────────────────
    # SAFE_BOTTOM=1210 이라 마지막 텍스트는 y <= 1200 이어야 잘리지 않음
    if author:
        # 저자 위 골드 라인
        cv.rect(
            (cv.size[0] // 2 - 30, 1080, cv.size[0] // 2 + 30, 1083),
            fill=GOLD,
        )
        cv.text_centered(1105, author, size=34, color="#FBBF24", bold=True)
        cv.text_centered(1155, "— Author", size=18, color="#94A3B8")

    watermark(cv, "@auto_publishing")
    return cv


def run() -> Path:
    """오늘의 명언 카드 1장 생성. 출력 파일 경로 반환."""
    crawler = QuoteCrawler()
    data = crawler.fetch_today()
    quote  = (data.get("quote")  or "").strip()
    author = (data.get("author") or "").strip()

    if not quote:
        log("명언 데이터 없음 — 카드 생성 중단", "error")
        from common.notifier import notify_pipeline_result
        notify_pipeline_result("명언→카드", 0, 1, details="ZenQuotes 응답 없음")
        return Path("")

    today = datetime.now()
    date_str = today.strftime("%Y년 %m월 %d일")
    cv = build_card(quote, author, date_str)

    out_path = OUTPUT_DIR / f"{today:%Y-%m-%d}.png"
    saved = cv.save(out_path)
    log(f"명언 카드 저장: {saved}", "ok")

    # 한글 번역+해석 (Claude CLI). 실패해도 영문 캡션은 그대로 발행.
    from common.quote_translator import translate_quote
    ko = translate_quote(quote, author)

    # 캡션 + 해시태그
    parts: list[str] = [f'"{quote}"']
    if author:
        parts.append(f"— {author}")
    if ko:
        parts.append("")  # blank line
        parts.append("📖 한글 번역")
        parts.append(ko["translation"])
        if ko.get("interpretation"):
            parts.append("")
            parts.append("💡 해석")
            parts.append(ko["interpretation"])
    caption = "\n".join(parts)
    hashtags = ["오늘의명언", "명언", "동기부여", "인생명언", "Quote", "DailyQuote"]

    publish_card(
        pipeline_name="명언→Instagram",
        image_path=saved,
        caption=caption,
        hashtags=hashtags,
        dryrun_env="QUOTE_DRYRUN",
        details_summary=f"{quote[:30]}… — {author}" if author else quote[:40],
    )
    return Path(saved)


if __name__ == "__main__":
    run()
