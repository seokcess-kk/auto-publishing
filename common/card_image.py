"""
인스타그램 카드 이미지 생성 공통 모듈.

- 1080×1350 (4:5 portrait) 기본 캔버스
- 한글 폰트 자동 탐색 (macOS / Linux), .env INSTAGRAM_FONT_PATH 로 override
- 텍스트 자동 줄바꿈, 이미지 오버레이, 모서리 라운드 등 기본 레이아웃 헬퍼

사용 예:
    from common.card_image import CardCanvas, ACCENT

    cv = CardCanvas()
    cv.fill_bg("#FFFFFF")
    cv.title("오늘의 명언", y=120)
    cv.body_block("배움에는 끝이 없다.", y=480, max_width=900)
    cv.save("data/cards/quote/2026-04-25.png")
"""
from __future__ import annotations

import os
import textwrap
from pathlib import Path
from typing import Iterable, Optional

import requests
from PIL import Image, ImageDraw, ImageFont, ImageFilter

from .logger import log


# ─── 캔버스 기본값 ────────────────────────────────────────────────────────────

CANVAS_W = 1080
CANVAS_H = 1350           # 4:5 portrait (Instagram 추천)
PADDING  = 80

# 인스타 피드 1:1 자동 크롭 대응 — 1080×1350 카드는 피드에서 위/아래 약 135px씩
# 잘려 1080×1080(중앙) 영역만 미리보기로 노출. 핵심 정보는 SAFE_TOP ~ SAFE_BOTTOM
# 사이에 배치해야 잘리지 않는다.
SAFE_TOP    = 140         # 피드 미리보기 상단 안전선
SAFE_BOTTOM = 1210        # 피드 미리보기 하단 안전선
SAFE_HEIGHT = SAFE_BOTTOM - SAFE_TOP

# 컬러 팔레트 (밝고 채도 낮은 톤) — 기존 호환 유지
BG_LIGHT  = "#FAFAFA"
BG_DARK   = "#0F172A"
FG_DARK   = "#0F172A"
FG_MUTED  = "#64748B"
FG_LIGHT  = "#F8FAFC"
ACCENT    = "#EF4444"     # 강조색 (빨강)
ACCENT_2  = "#3B82F6"     # 보조 강조 (블루)
DIVIDER   = "#E5E7EB"

# 확장 팔레트 — 카테고리별 시그니처 컬러
# Top 3 메달 컬러 (금/은/동)
GOLD      = "#F5B500"
SILVER    = "#A0A6B0"
BRONZE    = "#C97A4A"
RANK_ETC  = "#94A3B8"     # 4위 이하 뱃지 컬러

# 그라데이션 프리셋 (top, bottom)
GRADIENT_HISTORY  = ("#1E1B4B", "#4338CA")   # 짙은 인디고 → 보라
GRADIENT_QUOTE    = ("#0F172A", "#1E293B")   # 짙은 슬레이트 (모노크롬)
GRADIENT_TRENDS   = ("#0B1220", "#1E293B")   # 다크 네이비

# 카드 박스 컬러
CARD_BG_LIGHT     = "#FFFFFF"
CARD_SHADOW       = "#0F172A"   # 그림자 (alpha 적용용)
TINT_RED          = "#FEE2E2"   # 빨강 틴트 배경
TINT_BLUE         = "#DBEAFE"   # 파랑 틴트 배경
TINT_GOLD         = "#FEF3C7"   # 골드 틴트 배경


# ─── 폰트 탐색 ────────────────────────────────────────────────────────────────

# 사용자 환경에서 가장 가능성 높은 한글 폰트 후보 (macOS → Linux 순)
_FONT_CANDIDATES = [
    # macOS
    "/System/Library/Fonts/AppleSDGothicNeo.ttc",
    "/Library/Fonts/AppleGothic.ttf",
    "/System/Library/Fonts/Supplemental/AppleGothic.ttf",
    # Linux (Noto, Nanum 등이 깔린 경우)
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
    "/usr/share/fonts/truetype/nanum/NanumBarunGothic.ttf",
    # fallback (일부 docker 이미지)
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
]


def _resolve_font_path() -> str:
    """한글 가능 폰트 경로 결정. .env INSTAGRAM_FONT_PATH 우선."""
    override = os.getenv("INSTAGRAM_FONT_PATH", "").strip()
    if override and Path(override).exists():
        return override
    for c in _FONT_CANDIDATES:
        if Path(c).exists():
            return c
    raise FileNotFoundError(
        "한글 폰트를 찾을 수 없습니다. "
        ".env에 INSTAGRAM_FONT_PATH=/path/to/font.ttf 를 설정하세요."
    )


_FONT_PATH = None
def font_path() -> str:
    global _FONT_PATH
    if _FONT_PATH is None:
        _FONT_PATH = _resolve_font_path()
        log(f"[card_image] 폰트 사용: {_FONT_PATH}", "info")
    return _FONT_PATH


def get_font(size: int, *, bold: bool = False) -> ImageFont.FreeTypeFont:
    """폰트 객체 반환. ttc 인 경우 weight index 선택."""
    path = font_path()
    if path.endswith(".ttc"):
        # Apple SD Gothic Neo: 0=Thin, 2=Regular, 5=Bold (대략)
        index = 5 if bold else 2
        try:
            return ImageFont.truetype(path, size=size, index=index)
        except Exception:
            return ImageFont.truetype(path, size=size, index=0)
    return ImageFont.truetype(path, size=size)


# ─── 텍스트 측정/줄바꿈 ───────────────────────────────────────────────────────

def measure(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont) -> tuple[int, int]:
    """텍스트 가로/세로 픽셀 크기 (Pillow 10+ getbbox 사용)."""
    bbox = draw.textbbox((0, 0), text, font=font)
    return bbox[2] - bbox[0], bbox[3] - bbox[1]


def wrap_text(draw: ImageDraw.ImageDraw, text: str,
              font: ImageFont.FreeTypeFont, max_width: int) -> list[str]:
    """주어진 픽셀 폭에 맞춰 한글/영문 자동 줄바꿈."""
    if not text:
        return []

    # 한글은 어절 단위 wrap이 잘 안 되므로 글자 단위로 시도
    # 단, 영문 단어는 끊지 않도록 단어 우선 → 못 들어가면 글자 단위로 폴백
    lines: list[str] = []
    for paragraph in text.split("\n"):
        if not paragraph:
            lines.append("")
            continue
        words = paragraph.split(" ")
        current = ""
        for word in words:
            candidate = (current + " " + word).strip() if current else word
            w, _ = measure(draw, candidate, font)
            if w <= max_width:
                current = candidate
                continue
            # 단어 자체가 max_width 초과면 글자 단위로 잘라야 함
            if current:
                lines.append(current)
                current = ""
            # 글자 단위 분할
            buf = ""
            for ch in word:
                test = buf + ch
                w2, _ = measure(draw, test, font)
                if w2 <= max_width:
                    buf = test
                else:
                    if buf:
                        lines.append(buf)
                    buf = ch
            current = buf
        if current:
            lines.append(current)
    return lines


# ─── CardCanvas ───────────────────────────────────────────────────────────────

class CardCanvas:
    """인스타용 카드 캔버스. PIL Image 위에 레이아웃 헬퍼 제공."""

    def __init__(self, size: tuple[int, int] = (CANVAS_W, CANVAS_H),
                 bg: str = BG_LIGHT):
        self.size = size
        self.img  = Image.new("RGB", size, bg)
        self.draw = ImageDraw.Draw(self.img)

    # ---- 배경/장식 ----------------------------------------------------------

    def fill_bg(self, color: str) -> None:
        self.draw.rectangle([(0, 0), self.size], fill=color)

    def gradient_bg(self, top: str, bottom: str,
                    *, direction: str = "vertical") -> None:
        """수직/대각 그라데이션. direction: vertical | diagonal."""
        top_rgb = _hex_to_rgb(top)
        bot_rgb = _hex_to_rgb(bottom)
        w, h = self.size
        if direction == "diagonal":
            # 대각선 그라데이션 — 가로폭 2배 짜리 수직 그라데이션을 생성 후 회전
            # (픽셀 루프 회피로 대용량 캔버스에서도 수십 ms 내 처리)
            steps = w + h
            stripe = Image.new("RGB", (steps, 1))
            for i in range(steps):
                t = i / steps
                r = int(top_rgb[0] + (bot_rgb[0] - top_rgb[0]) * t)
                g = int(top_rgb[1] + (bot_rgb[1] - top_rgb[1]) * t)
                b = int(top_rgb[2] + (bot_rgb[2] - top_rgb[2]) * t)
                stripe.putpixel((i, 0), (r, g, b))
            stripe = stripe.resize((steps, steps), Image.NEAREST)
            stripe = stripe.rotate(45, expand=True, resample=Image.BILINEAR)
            sw, sh = stripe.size
            left = (sw - w) // 2
            top_y = (sh - h) // 2
            stripe = stripe.crop((left, top_y, left + w, top_y + h))
            self.img.paste(stripe, (0, 0))
            return
        # vertical (기본)
        for y in range(h):
            r = int(top_rgb[0] + (bot_rgb[0] - top_rgb[0]) * y / h)
            g = int(top_rgb[1] + (bot_rgb[1] - top_rgb[1]) * y / h)
            b = int(top_rgb[2] + (bot_rgb[2] - top_rgb[2]) * y / h)
            self.draw.line([(0, y), (self.size[0], y)], fill=(r, g, b))

    def radial_glow(self, center: tuple[int, int], radius: int,
                    color: str, *, alpha: int = 80) -> None:
        """배경에 빛나는 원형 글로우 효과를 더한다 (반투명 합성)."""
        cx, cy = center
        glow = Image.new("RGBA", self.size, (0, 0, 0, 0))
        gd = ImageDraw.Draw(glow)
        c = _hex_to_rgb(color)
        # 동심원으로 alpha 점차 감소 (radius → 0)
        steps = 18
        for i in range(steps, 0, -1):
            r = int(radius * i / steps)
            a = int(alpha * (1 - i / steps) ** 2 * 1.4)
            a = max(0, min(255, a))
            gd.ellipse(
                (cx - r, cy - r, cx + r, cy + r),
                fill=(c[0], c[1], c[2], a),
            )
        glow = glow.filter(ImageFilter.GaussianBlur(radius=radius // 8))
        self.img.paste(glow, (0, 0), glow)

    def rect(self, xy: tuple[int, int, int, int], fill: str,
             *, radius: int = 0, outline: Optional[str] = None,
             outline_width: int = 2) -> None:
        if radius > 0:
            self.draw.rounded_rectangle(xy, radius=radius, fill=fill,
                                        outline=outline, width=outline_width)
        else:
            self.draw.rectangle(xy, fill=fill, outline=outline, width=outline_width)

    def hline(self, y: int, *, x1: int = PADDING, x2: Optional[int] = None,
              color: str = DIVIDER, width: int = 2) -> None:
        if x2 is None:
            x2 = self.size[0] - PADDING
        self.draw.line([(x1, y), (x2, y)], fill=color, width=width)

    def shadow_card(self, xy: tuple[int, int, int, int], *,
                    fill: str = CARD_BG_LIGHT, radius: int = 24,
                    shadow_offset: tuple[int, int] = (0, 8),
                    shadow_blur: int = 18, shadow_alpha: int = 38) -> None:
        """드롭 섀도우가 있는 둥근 카드 박스를 그린다."""
        x1, y1, x2, y2 = xy
        # 섀도우 레이어
        sh_layer = Image.new("RGBA", self.size, (0, 0, 0, 0))
        sd = ImageDraw.Draw(sh_layer)
        ox, oy = shadow_offset
        sd.rounded_rectangle(
            (x1 + ox, y1 + oy, x2 + ox, y2 + oy),
            radius=radius, fill=(0, 0, 0, shadow_alpha),
        )
        sh_layer = sh_layer.filter(ImageFilter.GaussianBlur(radius=shadow_blur))
        self.img.paste(sh_layer, (0, 0), sh_layer)
        # 본 카드
        self.draw.rounded_rectangle((x1, y1, x2, y2), radius=radius, fill=fill)

    def pill_badge(self, center: tuple[int, int], text: str, *,
                   fill: str = ACCENT, fg: str = "#FFFFFF",
                   size: int = 28, padding_x: int = 24, padding_y: int = 10,
                   bold: bool = True) -> None:
        """둥근 알약 모양 배지 (중앙 정렬)."""
        font = get_font(size, bold=bold)
        tw, th = measure(self.draw, text, font)
        cx, cy = center
        w = tw + padding_x * 2
        h = th + padding_y * 2
        x1 = cx - w // 2
        y1 = cy - h // 2
        self.draw.rounded_rectangle(
            (x1, y1, x1 + w, y1 + h),
            radius=h // 2, fill=fill,
        )
        # 텍스트 anchor='mm' 으로 중앙 정렬
        self.draw.text((cx, cy), text, font=font, fill=fg, anchor="mm")

    def dotted_pattern(self, color: str = "#1F2937", *,
                       spacing: int = 28, dot_size: int = 2,
                       alpha: int = 70) -> None:
        """배경에 은은한 점 패턴 (도트 그리드) 효과."""
        layer = Image.new("RGBA", self.size, (0, 0, 0, 0))
        ld = ImageDraw.Draw(layer)
        c = _hex_to_rgb(color) + (alpha,)
        w, h = self.size
        for y in range(spacing, h, spacing):
            for x in range(spacing, w, spacing):
                ld.ellipse((x, y, x + dot_size, y + dot_size), fill=c)
        self.img.paste(layer, (0, 0), layer)

    def vignette(self, *, strength: int = 90) -> None:
        """가장자리를 어둡게 만드는 비네팅 효과."""
        w, h = self.size
        layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        ld = ImageDraw.Draw(layer)
        # 큰 동심원으로 점차 alpha 증가
        steps = 24
        max_r = int(((w / 2) ** 2 + (h / 2) ** 2) ** 0.5)
        cx, cy = w // 2, h // 2
        for i in range(steps):
            r = int(max_r * (i + 1) / steps)
            a = int(strength * (i / steps) ** 2)
            ld.ellipse((cx - r, cy - r, cx + r, cy + r),
                       outline=(0, 0, 0, a), width=max(1, max_r // steps + 2))
        layer = layer.filter(ImageFilter.GaussianBlur(radius=40))
        self.img.paste(layer, (0, 0), layer)

    # ---- 텍스트 -------------------------------------------------------------

    def text(self, xy: tuple[int, int], text: str, *,
             size: int = 40, color: str = FG_DARK, bold: bool = False,
             anchor: str = "la") -> None:
        """단일 라인 텍스트. anchor: la(좌상), mm(중앙), rt(우상) 등 PIL 표준."""
        font = get_font(size, bold=bold)
        self.draw.text(xy, text, font=font, fill=color, anchor=anchor)

    def text_centered(self, y: int, text: str, *,
                      size: int = 40, color: str = FG_DARK,
                      bold: bool = False) -> None:
        font = get_font(size, bold=bold)
        w, _ = measure(self.draw, text, font)
        x = (self.size[0] - w) // 2
        self.draw.text((x, y), text, font=font, fill=color)

    def text_block(self, xy: tuple[int, int], text: str, *,
                   size: int = 36, color: str = FG_DARK, bold: bool = False,
                   max_width: Optional[int] = None,
                   line_height: float = 1.45,
                   center: bool = False) -> int:
        """다중 라인 텍스트 블록. 그린 후 다음 y 좌표 반환."""
        font = get_font(size, bold=bold)
        x, y = xy
        if max_width is None:
            max_width = self.size[0] - 2 * PADDING
        lines = wrap_text(self.draw, text, font, max_width)
        line_gap = int(size * line_height)
        for line in lines:
            if center:
                w, _ = measure(self.draw, line, font)
                draw_x = (self.size[0] - w) // 2
            else:
                draw_x = x
            self.draw.text((draw_x, y), line, font=font, fill=color)
            y += line_gap
        return y

    # ---- 이미지 -------------------------------------------------------------

    def image(self, source: str, xy: tuple[int, int], size: tuple[int, int],
              *, radius: int = 0) -> bool:
        """URL 또는 파일 경로의 이미지를 그림. 실패 시 False."""
        try:
            if source.startswith("http"):
                resp = requests.get(source, timeout=10,
                                    headers={"User-Agent": "Mozilla/5.0"})
                resp.raise_for_status()
                from io import BytesIO
                im = Image.open(BytesIO(resp.content))
            else:
                im = Image.open(source)
            im = im.convert("RGB")
            im.thumbnail(size, Image.LANCZOS)
            # cover 방식: 부족한 쪽은 crop 으로 채움
            im = _cover_resize(im, size)
            if radius > 0:
                im = _round_corners(im, radius)
                self.img.paste(im, xy, im)
            else:
                self.img.paste(im, xy)
            return True
        except Exception as e:
            log(f"[card_image] 이미지 삽입 실패: {e}", "warn")
            return False

    # ---- 저장 ---------------------------------------------------------------

    def save(self, path: str | Path, *, quality: int = 92) -> str:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        # JPG 는 RGB 모드 필요. PNG 는 그대로.
        if path.suffix.lower() in {".jpg", ".jpeg"}:
            self.img.convert("RGB").save(path, "JPEG", quality=quality)
        else:
            self.img.save(path)
        return str(path)


# ─── 내부 헬퍼 ────────────────────────────────────────────────────────────────

def _hex_to_rgb(hex_str: str) -> tuple[int, int, int]:
    h = hex_str.lstrip("#")
    return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


def _cover_resize(im: Image.Image, size: tuple[int, int]) -> Image.Image:
    """대상 크기를 꽉 채우도록 비율 유지 + 중앙 crop."""
    target_w, target_h = size
    src_w, src_h = im.size
    ratio = max(target_w / src_w, target_h / src_h)
    new_w = int(src_w * ratio)
    new_h = int(src_h * ratio)
    im = im.resize((new_w, new_h), Image.LANCZOS)
    left = (new_w - target_w) // 2
    top  = (new_h - target_h) // 2
    return im.crop((left, top, left + target_w, top + target_h))


def _round_corners(im: Image.Image, radius: int) -> Image.Image:
    """이미지에 라운드 코너 알파 마스크 적용."""
    mask = Image.new("L", im.size, 0)
    md = ImageDraw.Draw(mask)
    md.rounded_rectangle([(0, 0), im.size], radius=radius, fill=255)
    im = im.convert("RGBA")
    im.putalpha(mask)
    return im


# ─── 빌더 헬퍼 (각 파이프라인에서 자주 쓰는 패턴) ─────────────────────────────

def page_indicator(canvas: CardCanvas, current: int, total: int,
                   *, y: int = 1280) -> None:
    """캐러셀 페이지 인디케이터 (· · · 1/3)."""
    text = f"{current} / {total}"
    canvas.text_centered(y, text, size=26, color=FG_MUTED)


def watermark(canvas: CardCanvas, text: str = "@auto_publishing",
              *, y: Optional[int] = None) -> None:
    """좌하단/우하단 워터마크. 기본은 우하단."""
    if y is None:
        y = canvas.size[1] - 60
    font = get_font(22, bold=False)
    w, _ = measure(canvas.draw, text, font)
    canvas.draw.text((canvas.size[0] - PADDING - w, y), text,
                     font=font, fill=FG_MUTED)


__all__ = [
    "CANVAS_W", "CANVAS_H", "PADDING",
    "SAFE_TOP", "SAFE_BOTTOM", "SAFE_HEIGHT",
    "BG_LIGHT", "BG_DARK", "FG_DARK", "FG_MUTED", "FG_LIGHT",
    "ACCENT", "ACCENT_2", "DIVIDER",
    "GOLD", "SILVER", "BRONZE", "RANK_ETC",
    "GRADIENT_HISTORY", "GRADIENT_QUOTE", "GRADIENT_TRENDS",
    "CARD_BG_LIGHT", "TINT_RED", "TINT_BLUE", "TINT_GOLD",
    "CardCanvas", "get_font", "measure", "wrap_text",
    "page_indicator", "watermark",
]
