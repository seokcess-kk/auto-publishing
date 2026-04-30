"""
파이프라인: Google 트렌드 → 인포그래픽 카드 1장 → Instagram 발행.

Google Trends RSS 에서 일간 급상승 검색어 TOP N (기본 10) 을 받아
1080×1350 인포그래픽 1장으로 시각화하고, Instagram 에 이미지 발행한다.

이 파이프라인이 자동 스케줄(SCHEDULE_GOOGLE_TRENDS_INSTAGRAM)에 등록되는 유일한
"카드 발행" 파이프라인이다. 다른 *_to_image 파이프라인은 카드 생성만 한다.

실행:
    python -m pipelines.google_trends_to_instagram
    GOOGLE_TRENDS_COUNT=10 python -m pipelines.google_trends_to_instagram
"""
from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

from common.logger import log
from common.card_image import (
    CardCanvas, ACCENT, BG_LIGHT, FG_DARK, FG_MUTED, PADDING,
    DIVIDER, watermark, get_font, measure,
    GOLD, SILVER, BRONZE, RANK_ETC,
    GRADIENT_TRENDS, SAFE_TOP, CARD_BG_LIGHT,
)
from sources.entertainment import GoogleTrendsCrawler


SCHEDULE = {
    "env":  "SCHEDULE_GOOGLE_TRENDS_INSTAGRAM",
    "func": "run",
    "args_from_env": ("GOOGLE_TRENDS_COUNT:10:int",),
}


ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = ROOT / "data" / "cards" / "google_trends"

DEFAULT_COUNT = 10


# ─── 카드 빌더 ────────────────────────────────────────────────────────────────

# Top 3 위치별 메달 컬러 (1=금, 2=은, 3=동)
_MEDAL_COLORS = {1: GOLD, 2: SILVER, 3: BRONZE}

# 트래픽 문자열을 정수로 환산 (시각적 막대 길이 계산용)
def _traffic_to_int(traffic: str) -> int:
    s = (traffic or "").strip().replace(",", "").replace("+", "").upper()
    if not s:
        return 0
    mult = 1
    if s.endswith("K"):
        mult = 1_000
        s = s[:-1]
    elif s.endswith("M"):
        mult = 1_000_000
        s = s[:-1]
    try:
        return int(float(s) * mult)
    except ValueError:
        return 0


def _truncate(draw, text: str, font, max_width: int) -> str:
    """주어진 폭 안에 맞도록 키워드 말줄임."""
    if measure(draw, text, font)[0] <= max_width:
        return text
    while text and measure(draw, text + "…", font)[0] > max_width:
        text = text[:-1]
    return (text + "…") if text else "…"


def build_card(items: list[dict], date_str: str) -> CardCanvas:
    """TOP N 트렌드 인포그래픽 카드 1장 (피드 1:1 크롭 안전)."""
    cv = CardCanvas()
    # 라이트 배경
    cv.fill_bg("#F4F6FB")

    # ─── 상단 헤더 (다크) ─────────────────────────────────────────────────
    # 헤더 높이 420 — 인스타 피드 1:1 크롭(상단 135px 절단) 후에도
    # "TOP 10" 큰 텍스트가 정확히 SAFE_TOP(140) 아래에 들어와 살아남도록 설계.
    HEADER_H = 420
    cv.gradient_bg(*GRADIENT_TRENDS, direction="vertical")
    cv.rect((0, HEADER_H, cv.size[0], cv.size[1]), fill="#F4F6FB")

    # 헤더 좌측 액센트 라인
    cv.rect((0, 0, 8, HEADER_H), fill=ACCENT)

    # 상단 작은 라벨 (피드에서 잘려도 OK한 부수 정보)
    cv.text_centered(80, "오늘의 검색 트렌드", size=36, color="#F8FAFC", bold=True)

    # "TOP 10" 큰 텍스트 — 중심 y=240 (SAFE_TOP=140 아래로 안전)
    cv.text((cv.size[0] // 2, 240), f"TOP {len(items)}",
            size=140, color="#FFFFFF", bold=True, anchor="mm")
    # 강조 언더라인 (TOP X 아래)
    cv.rect((cv.size[0] // 2 - 90, 320, cv.size[0] // 2 + 90, 326), fill=ACCENT)
    # 날짜
    cv.text_centered(345, date_str, size=26, color="#CBD5E1")

    # ─── 본문 카드 컨테이너 ───────────────────────────────────────────────
    BODY_TOP = HEADER_H + 24
    BODY_BOTTOM = cv.size[1] - 100
    cv.shadow_card(
        (PADDING - 20, BODY_TOP, cv.size[0] - PADDING + 20, BODY_BOTTOM),
        fill=CARD_BG_LIGHT, radius=28,
        shadow_offset=(0, 6), shadow_blur=22, shadow_alpha=30,
    )

    # ─── 랭킹 리스트 ─────────────────────────────────────────────────────
    # 행 높이: TOP 3 = 78, 4-10 = 54 (시각 위계 차별화)
    list_x_left = PADDING
    list_x_right = cv.size[0] - PADDING
    y = BODY_TOP + 28

    # 트래픽 최대값 (막대 정규화용)
    traffic_max = max((_traffic_to_int(it.get("traffic", "")) for it in items), default=0)

    for item in items:
        rank = item.get("rank", 0)
        keyword = item.get("keyword", "")
        traffic = item.get("traffic", "")
        is_top3 = rank <= 3

        # 행별 높이/뱃지/폰트 사이즈
        row_h = 78 if is_top3 else 54
        badge_size = 50 if is_top3 else 36
        kw_size = 36 if is_top3 else 26
        tr_size = 20 if is_top3 else 17

        # 행 중심 y
        row_cy = y + row_h // 2

        # 뱃지 컬러: TOP 3 = 메달, 4-10 = RANK_ETC (옅은 회색)
        badge_color = _MEDAL_COLORS.get(rank, RANK_ETC)
        # TOP 3 는 둥근 사각형 + 반사 느낌 (살짝 더 강조)
        badge_x = list_x_left
        cv.rect(
            (badge_x, row_cy - badge_size // 2,
             badge_x + badge_size, row_cy + badge_size // 2),
            fill=badge_color, radius=badge_size // 2,
        )
        # 뱃지 안 숫자
        cv.text(
            (badge_x + badge_size // 2, row_cy),
            str(rank), size=int(badge_size * 0.55),
            color="#FFFFFF", bold=True, anchor="mm",
        )

        # 키워드
        kw_x = badge_x + badge_size + 24
        kw_font = get_font(kw_size, bold=is_top3)
        # 키워드 영역(우측 트래픽 영역 제외 폭)
        # 트래픽 영역: 막대(120px) + 텍스트(~110px) = 약 250px 확보
        kw_max_w = list_x_right - kw_x - 250
        kw_text = _truncate(cv.draw, keyword, kw_font, kw_max_w)
        kw_color = FG_DARK if is_top3 else "#1F2937"
        cv.draw.text((kw_x, row_cy), kw_text, font=kw_font,
                     fill=kw_color, anchor="lm")

        # 트래픽 시각 막대 + 텍스트
        if traffic:
            tr_value = _traffic_to_int(traffic)
            # 막대
            bar_max_w = 120
            bar_w = int(bar_max_w * (tr_value / traffic_max)) if traffic_max else 0
            bar_w = max(8, min(bar_max_w, bar_w))
            bar_h = 8 if is_top3 else 6
            bar_x_right = list_x_right - 90
            bar_x_left = bar_x_right - bar_w
            bar_y = row_cy - bar_h // 2
            # 막대 트랙(연한 배경)
            cv.rect(
                (bar_x_right - bar_max_w, bar_y, bar_x_right, bar_y + bar_h),
                fill="#E5E7EB", radius=bar_h // 2,
            )
            # 채워진 막대 (TOP 3 메달색, 그 외 액센트)
            fill_color = badge_color if is_top3 else "#60A5FA"
            cv.rect(
                (bar_x_left, bar_y, bar_x_right, bar_y + bar_h),
                fill=fill_color, radius=bar_h // 2,
            )
            # 텍스트
            tr_font = get_font(tr_size, bold=is_top3)
            cv.draw.text(
                (list_x_right, row_cy), traffic,
                font=tr_font, fill=FG_MUTED, anchor="rm",
            )

        # 구분선 (마지막 제외, TOP 3 끝나면 약간 더 진한 선)
        if rank < len(items):
            line_y = y + row_h + (4 if is_top3 else 1)
            line_color = "#CBD5E1" if rank == 3 else DIVIDER
            line_width = 2 if rank == 3 else 1
            cv.hline(line_y, x1=list_x_left, x2=list_x_right,
                     color=line_color, width=line_width)

        y += row_h + (10 if is_top3 else 2)

    # ─── 하단 ─────────────────────────────────────────────────────────────
    cv.text_centered(BODY_BOTTOM + 30, "출처 · Google Trends Korea",
                     size=22, color=FG_MUTED)
    watermark(cv, "@auto_publishing", y=cv.size[1] - 50)
    return cv


# ─── 캡션 / 해시태그 ──────────────────────────────────────────────────────────

def build_caption(items: list[dict], date_str: str) -> str:
    """인스타그램 캡션 (~2200자 제한). 전체 순위 노출."""
    # Top 3 메달 이모지 (텍스트로만)
    medals = {1: "🥇", 2: "🥈", 3: "🥉"}
    rank_lines = []
    for it in items:
        rank = it.get("rank", 0)
        kw = it.get("keyword", "")
        traffic = it.get("traffic", "")
        prefix = medals.get(rank, f"{rank}.")
        line = f"{prefix} {kw}"
        if traffic:
            line += f" ({traffic})"
        rank_lines.append(line)

    lines = [
        f"📊 {date_str} 구글 검색 트렌드 TOP {len(items)}",
        "",
        *rank_lines,
        "",
        "👉 전체 순위는 이미지에서 확인!",
        "🔗 자세한 내용은 프로필 링크",
    ]
    return "\n".join(lines)


def build_hashtags(items: list[dict]) -> list[str]:
    """검색 트렌드 키워드 + 일반 해시태그 조합 (최대 30개)."""
    base = [
        "구글트렌드", "검색트렌드", "오늘의이슈", "실시간이슈",
        "트렌드", "이슈정리", "오늘의키워드", "데일리트렌드",
        "정보스타그램", "인포그래픽",
    ]
    # 키워드 자체도 해시태그로 (공백 제거, 영문/한글만)
    kw_tags: list[str] = []
    for it in items[:10]:
        kw = it.get("keyword", "").strip()
        if not kw:
            continue
        # 공백/특수문자 제거
        cleaned = "".join(ch for ch in kw if ch.isalnum())
        if cleaned and cleaned not in kw_tags:
            kw_tags.append(cleaned)

    combined = base + kw_tags
    return combined[:30]


# ─── 메인 ─────────────────────────────────────────────────────────────────────

def run(count: int = DEFAULT_COUNT) -> None:
    """구글 트렌드 → 인포그래픽 → Instagram 발행."""
    log(f"Google 트렌드→Instagram 파이프라인 시작 (count={count})", "step")

    # 1. 트렌드 수집
    crawler = GoogleTrendsCrawler()
    items = crawler.fetch(count=count)
    if not items:
        log("구글 트렌드 데이터 없음", "error")
        from common.notifier import notify_pipeline_result
        notify_pipeline_result("Google 트렌드→Instagram", 0, 1, details="RSS 응답 없음")
        return

    # 2. 카드 이미지 생성
    today = datetime.now()
    date_str = today.strftime("%Y.%m.%d")
    cv = build_card(items, date_str)
    out_path = OUTPUT_DIR / f"{today:%Y-%m-%d}.png"
    image_path = cv.save(out_path)
    log(f"트렌드 카드 저장: {image_path}", "ok")

    # 3. 캡션/해시태그
    caption = build_caption(items, date_str)
    hashtags = build_hashtags(items)

    # 4. Instagram 발행
    if os.getenv("GOOGLE_TRENDS_DRYRUN", "0") == "1":
        log("[DRYRUN] Instagram 발행 생략 — 카드만 생성됨", "warn")
        from common.notifier import notify_pipeline_result
        notify_pipeline_result(
            "Google 트렌드→Instagram", 1, 1,
            details=f"DRYRUN · TOP {len(items)} · {image_path}",
        )
        return

    from publishers.instagram import InstagramPublisher
    pub = InstagramPublisher()

    if not pub.login():
        log("Instagram 로그인 실패 — 발행 중단", "error")
        try:
            pub.close()
        except Exception:
            pass
        from common.notifier import notify_pipeline_result
        notify_pipeline_result(
            "Google 트렌드→Instagram", 0, 1,
            details=f"로그인 실패 · 카드는 저장됨: {image_path}",
        )
        return

    try:
        result = pub.post(
            title="",
            content=caption,
            tags=hashtags,
            media_type="image",
            media_path=image_path,
        )
    finally:
        try:
            pub.close()
        except Exception:
            pass

    from common.notifier import notify_pipeline_result
    if result.success:
        notify_pipeline_result(
            "Google 트렌드→Instagram", 1, 1,
            details=f"TOP {len(items)} · {items[0].get('keyword', '')} 외",
        )
        log("Instagram 발행 완료", "ok")
    else:
        notify_pipeline_result(
            "Google 트렌드→Instagram", 0, 1,
            details=f"발행 실패: {result.message}",
        )
        log(f"Instagram 발행 실패: {result.message}", "error")


if __name__ == "__main__":
    run(count=int(os.getenv("GOOGLE_TRENDS_COUNT", str(DEFAULT_COUNT))))
