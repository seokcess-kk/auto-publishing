"""
네이버 카페 발행용 'HIT 상품' 스타일 카드 이미지 생성.

Old_Source naver_cafe/네이버카페_쿠팡파트너스/...adpick_ver6.py 의
make_thumb / set_text_thumb / set_tile_thumb 를 이식. 정사각형 상품
이미지에 검정 그라데이션 + 분홍 사각 테두리(tile.png) + 2줄 텍스트.

사용 예:
    from common.cafe_card import make_hit_card

    out = make_hit_card(
        product_image_url="https://...jpg",
        line1="나의 스타일 HIT 상품",
        line2=keyword,
        out_path="data/cafe/coupang/2026-04-26.png",
    )
"""
from __future__ import annotations

import io
import os
import random
from pathlib import Path
from typing import Optional

import requests
from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parent.parent
ASSETS_DIR = ROOT / "resources" / "cafe_assets"
TILES_DIR = ASSETS_DIR / "tiles"
FONTS_DIR = ASSETS_DIR / "fonts"

DEFAULT_TILE = TILES_DIR / "coupang_tile.png"
DEFAULT_FONT = FONTS_DIR / "NotoSansKR-ExtraBold.ttf"


# Old_Source 의 thumb_title_lists 에서 1번 가져온 컨셉
HIT_TITLES_COUPANG = [
    "쇼핑의 새로운 기준 HIT",
    "이거 어때? HIT 상품",
    "나의 스타일 HIT 상품",
    "나만의 선택 HIT 상품",
]
HIT_TITLES_REALESTATE = [
    "핫핫!! 분양정보",
    "임대->분양",
    "청약접수 경쟁률",
]
HIT_TITLES_RISESET = [
    "오늘의 일출일몰",
    "오늘의 시각",
]


def random_hit_title(category: str = "coupang") -> str:
    pool = {
        "coupang": HIT_TITLES_COUPANG,
        "realestate": HIT_TITLES_REALESTATE,
        "riseset": HIT_TITLES_RISESET,
    }.get(category, HIT_TITLES_COUPANG)
    return random.choice(pool)


# ─── 이미지 처리 ─────────────────────────────────────────────────────────────

def _download_image(url: str) -> Image.Image:
    """이미지 URL → PIL Image. 실패 시 흰 배경 fallback."""
    try:
        if url.startswith("//"):
            url = "https:" + url
        r = requests.get(url, timeout=15, headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) "
                          "Chrome/124.0.0.0 Safari/537.36",
        })
        r.raise_for_status()
        return Image.open(io.BytesIO(r.content))
    except Exception:
        # 흰 배경 800x800 fallback
        return Image.new("RGB", (800, 800), color="#ffffff")


def _resize_square(im: Image.Image, size: int = 600) -> Image.Image:
    """이미지를 정사각형으로 crop + 지정 크기 리사이즈."""
    w, h = im.size
    if w > h:
        gap = w - h
        left = gap // 2
        im = im.crop((left, 0, left + h, h))
    elif h > w:
        gap = h - w
        top = gap // 2
        im = im.crop((0, top, w, top + w))
    return im.resize((size, size), Image.LANCZOS)


def _gradient_overlay(im: Image.Image) -> Image.Image:
    """위는 투명 / 아래는 어두운 검정 페이드 오버레이 합성."""
    if im.mode != "RGBA":
        im = im.convert("RGBA")
    w, h = im.size
    gradient = Image.new("L", (1, 255), color=0xFFFFFF)
    for y in range(255):
        gradient.putpixel((0, y - 255), y)
    alpha = gradient.resize((w, h))
    black = Image.new("RGBA", (w, h), color=(0, 0, 0, 0))
    black.putalpha(alpha)
    return Image.alpha_composite(im, black)


def _paste_tile(im: Image.Image, tile_path: Path = DEFAULT_TILE) -> Image.Image:
    """중앙에 분홍 사각 테두리(tile.png) 합성."""
    if not tile_path.exists():
        return im
    tile = Image.open(tile_path).convert("RGBA")
    if im.mode != "RGBA":
        im = im.convert("RGBA")
    iw, ih = im.size
    tw, th = tile.size
    # 이미지 크기 대비 너무 크면 축소 (Old_Source 는 600x600 이미지에 맞춰 그려짐)
    if tw > iw * 0.85 or th > ih * 0.85:
        scale = min(iw * 0.85 / tw, ih * 0.85 / th)
        tile = tile.resize((int(tw * scale), int(th * scale)), Image.LANCZOS)
        tw, th = tile.size
    sx = (iw - tw) // 2
    sy = (ih - th) // 2
    im.paste(tile, (sx, sy), tile)
    return im


def _draw_two_line_text(im: Image.Image, line1: str, line2: str,
                       *, font_path: Path = DEFAULT_FONT,
                       img_fraction: float = 0.85) -> Image.Image:
    """이미지 중앙에 2줄 텍스트. 흰색 + 노란 강조 (Old_Source 스타일)."""
    if im.mode != "RGBA":
        im = im.convert("RGBA")
    overlay = Image.new("RGBA", im.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    # img_fraction 만큼 텍스트가 차지할 때까지 폰트 크기 증가
    text = f"{line1}\n{line2}"
    fontsize = 8
    while True:
        font = ImageFont.truetype(str(font_path), fontsize)
        bbox = draw.textbbox((0, 0), text, font=font)
        text_w = bbox[2] - bbox[0]
        if text_w >= img_fraction * im.size[0]:
            break
        fontsize += 2
        if fontsize > 200:
            break
    fontsize -= 2
    font = ImageFont.truetype(str(font_path), fontsize)

    # line1 / line2 개별 측정
    bb1 = draw.textbbox((0, 0), line1, font=font)
    bb2 = draw.textbbox((0, 0), line2, font=font)
    w1, h1 = bb1[2] - bb1[0], bb1[3] - bb1[1]
    w2, h2 = bb2[2] - bb2[0], bb2[3] - bb2[1]

    iw, ih = im.size
    cy = ih // 2
    # line1 위, line2 아래 (각각 텍스트 높이 절반씩 떨어짐)
    y1 = cy - h2 // 2 - h1
    y2 = cy + h2 // 2 - h2 // 4

    draw.text(((iw - w1) // 2, y1), line1, font=font, fill=(255, 255, 255, 255))
    draw.text(((iw - w2) // 2, y2), line2, font=font, fill=(255, 199, 21, 255))

    return Image.alpha_composite(im, overlay)


def make_hit_card(
    product_image_url: str,
    line1: str,
    line2: str,
    out_path: str | Path,
    *,
    tile_path: Optional[Path] = None,
) -> Path:
    """HIT 상품 카드 1장 생성. 저장 경로 반환.

    흐름: 다운로드 → 600×600 crop → 그라데이션 → tile 중앙 합성 → 2줄 텍스트.
    """
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    im = _download_image(product_image_url)
    im = _resize_square(im, 600)
    im = _gradient_overlay(im)
    im = _paste_tile(im, tile_path or DEFAULT_TILE)
    im = _draw_two_line_text(im, line1, line2)

    # PNG 로 저장 (RGBA 보존)
    im.save(out_path, "PNG")
    return out_path
