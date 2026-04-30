"""
파이프라인: 환율·코인·지수 → 데이터 인포그래픽 카드 1장.

FinanceSource.fetch_all() 결과를 한 장의 1080×1350 카드로 시각화한다.
- 환율: USD / JPY / EUR
- 지수: KOSPI / KOSDAQ
- 코인: BTC / ETH / SOL / XRP

실행:
    python -m pipelines.finance_to_image
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Iterable

from dotenv import load_dotenv
load_dotenv()

from common.instagram_publish import publish_card
from common.logger import log
from common.card_image import (
    CardCanvas, ACCENT, ACCENT_2, BG_LIGHT, FG_DARK, FG_MUTED, PADDING,
    DIVIDER, watermark, get_font, measure,
)
from sources.finance import FinanceSource


SCHEDULE = {
    "env":  "SCHEDULE_FINANCE_INSTAGRAM",
    "func": "run",
}

ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = ROOT / "data" / "cards" / "finance"


# 표시할 환율 통화 (이름에 포함되어 있으면 매칭)
_EXCHANGE_FILTER = ["미국 USD", "일본 JPY", "유럽연합 EUR", "USD", "JPY", "EUR"]
# 표시할 코인 (시장 코드)
_CRYPTO_PRIORITY = ["KRW-BTC", "KRW-ETH", "KRW-SOL", "KRW-XRP"]


def _pick_exchange(items: list[dict], limit: int = 3) -> list[dict]:
    """우선순위 통화만 필터."""
    picked: list[dict] = []
    for keyword in ["USD", "JPY", "EUR"]:
        for it in items:
            if keyword in it.get("name", ""):
                if it not in picked:
                    picked.append(it)
                break
        if len(picked) >= limit:
            break
    return picked[:limit]


def _pick_crypto(items: list[dict], limit: int = 4) -> list[dict]:
    by_market = {it.get("market", ""): it for it in items}
    picked = [by_market[m] for m in _CRYPTO_PRIORITY if m in by_market]
    if len(picked) < limit:
        rest = [it for it in items if it.get("market") not in _CRYPTO_PRIORITY]
        picked.extend(rest[: limit - len(picked)])
    return picked[:limit]


def _change_color(text: str, *, default: str = FG_MUTED) -> str:
    """+/- 부호로 빨강/파랑(한국 관행: 상승 빨강, 하락 파랑) 결정."""
    if not text:
        return default
    if text.startswith("-") or "하락" in text:
        return ACCENT_2  # 파랑 (하락)
    if text.startswith("+") or "상승" in text:
        return ACCENT    # 빨강 (상승)
    return default


def _format_change(item: dict) -> str:
    """환율/원자재 항목의 변동을 ±N.NN 형태로 정규화."""
    raw = (item.get("change") or "").replace(",", "").strip()
    direction = item.get("direction", "")
    if not raw:
        return ""
    if direction == "하락":
        return f"-{raw}"
    if direction == "상승":
        return f"+{raw}"
    return raw


def _draw_row(cv: CardCanvas, y: int, label: str, value: str, change: str,
              *, label_size: int = 36, value_size: int = 44) -> None:
    """좌: label, 우: value (change)"""
    # 라벨 (좌)
    cv.text((PADDING, y), label, size=label_size, color=FG_DARK, bold=True)

    # 값 (우정렬)
    val_font = get_font(value_size, bold=True)
    val_w, _ = measure(cv.draw, value, val_font)
    val_x = cv.size[0] - PADDING - val_w
    cv.draw.text((val_x, y - 6), value, font=val_font, fill=FG_DARK)

    # 변동률 (값 아래)
    if change:
        change_font = get_font(26)
        change_w, _ = measure(cv.draw, change, change_font)
        cv.draw.text(
            (cv.size[0] - PADDING - change_w, y + value_size + 6),
            change, font=change_font, fill=_change_color(change),
        )


def build_card(market: dict, crypto: list[dict], index: dict,
               date_str: str) -> CardCanvas:
    cv = CardCanvas()
    cv.fill_bg(BG_LIGHT)

    # 헤더 ─────────────────────────────────────────────────────────────────
    cv.rect((0, 0, cv.size[0], 200), fill="#0F172A")
    cv.text_centered(60, "오늘의 시황", size=46, color="#F8FAFC", bold=True)
    cv.text_centered(130, date_str, size=30, color="#94A3B8")

    # 섹션 1: 환율 ──────────────────────────────────────────────────────────
    y = 260
    cv.text((PADDING, y), "💱 환율", size=38, color=ACCENT, bold=True)
    y += 80

    exchanges = _pick_exchange(market.get("exchange", []))
    if not exchanges:
        cv.text((PADDING, y), "데이터 없음", size=32, color=FG_MUTED)
        y += 60
    else:
        for item in exchanges:
            _draw_row(
                cv, y,
                label=item.get("name", ""),
                value=item.get("price", ""),
                change=_format_change(item),
            )
            y += 110

    cv.hline(y, color=DIVIDER)

    # 섹션 2: 지수 ──────────────────────────────────────────────────────────
    y += 50
    cv.text((PADDING, y), "📈 주식 지수", size=38, color=ACCENT, bold=True)
    y += 80

    if index.get("kospi"):
        _draw_row(cv, y, "KOSPI", index.get("kospi", ""), "")
        y += 100
    if index.get("kosdaq"):
        _draw_row(cv, y, "KOSDAQ", index.get("kosdaq", ""), "")
        y += 100

    cv.hline(y, color=DIVIDER)

    # 섹션 3: 코인 ──────────────────────────────────────────────────────────
    y += 50
    cv.text((PADDING, y), "🪙 가상화폐 (KRW)", size=38, color=ACCENT, bold=True)
    y += 80

    coins = _pick_crypto(crypto)
    if not coins:
        cv.text((PADDING, y), "데이터 없음", size=32, color=FG_MUTED)
    else:
        for c in coins:
            price = c.get("price", 0)
            try:
                price_str = f"₩{int(price):,}"
            except Exception:
                price_str = str(price)
            change_rate = c.get("change_rate", 0)
            change_str = f"{change_rate:+.2f}%" if change_rate else ""
            _draw_row(
                cv, y,
                label=c.get("name", ""),
                value=price_str,
                change=change_str,
            )
            y += 100

    watermark(cv)
    return cv


def run() -> Path:
    """환율·코인·지수 인포그래픽 카드 1장 생성."""
    source = FinanceSource()
    data = source.fetch_all()

    market = data.get("market", {}) or {}
    crypto = data.get("crypto", []) or []
    index  = data.get("index", {}) or {}

    if not (market.get("exchange") or crypto or index.get("kospi")):
        log("금융 데이터 모두 비어있음 — 카드 생성 중단", "error")
        from common.notifier import notify_pipeline_result
        notify_pipeline_result("금융→카드", 0, 1, details="모든 소스 응답 실패")
        return Path("")

    today = datetime.now()
    date_str = today.strftime("%Y년 %m월 %d일 %H:%M")

    cv = build_card(market, crypto, index, date_str)
    out_path = OUTPUT_DIR / f"{today:%Y-%m-%d_%H%M}.png"
    saved = cv.save(out_path)
    log(f"금융 인포그래픽 저장: {saved}", "ok")

    summary_parts = []
    if market.get("exchange"):
        summary_parts.append(f"환율 {len(_pick_exchange(market['exchange']))}건")
    if index.get("kospi"):
        summary_parts.append("지수")
    if crypto:
        summary_parts.append(f"코인 {len(_pick_crypto(crypto))}개")
    summary = " / ".join(summary_parts) or "일일 마감 요약"

    caption = (
        f"💹 오늘의 시황 요약 ({today.strftime('%m월 %d일')})\n"
        f"환율 · 지수 · 코인 한눈에 보기"
    )
    hashtags = ["오늘의시황", "환율", "코스피", "코스닥", "암호화폐",
                "Bitcoin", "주식", "재테크", "FinanceDaily"]

    publish_card(
        pipeline_name="금융→Instagram",
        image_path=saved,
        caption=caption,
        hashtags=hashtags,
        dryrun_env="FINANCE_DRYRUN",
        details_summary=summary,
    )
    return Path(saved)


if __name__ == "__main__":
    run()
