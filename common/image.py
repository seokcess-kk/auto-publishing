"""
이미지 처리 공통 모듈
- 이미지 다운로드 및 임시 저장
- 플랫폼별 이미지 업로드는 각 publisher에서 처리

다운로드 정규화 — 알리 CDN(*.aliexpress-media.com) 등이 .jpg URL 인데
실제로는 AVIF/WebP 로 변환 응답하는 케이스가 많아, 티스토리/WordPress 의
이미지 업로드 API 가 400 으로 거부한다. download() 가 응답을 Pillow 로
열어 RGB JPEG 로 강제 정규화해 publisher 호환성을 확보한다.
"""
import os
import tempfile
from io import BytesIO
from pathlib import Path

import requests
from .logger import log


_FIXED_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


def download(url: str, suffix: str = ".jpg") -> str:
    """URL에서 이미지를 다운로드하고 JPEG 정규화 후 임시 파일 경로 반환.

    Pillow 가 webp/avif/png 모두 읽어 RGB JPEG 로 통일 → publisher 의
    multipart 업로드 API 가 200 으로 받음. 정규화 실패 시 raw 응답으로 폴백.
    """
    resp = requests.get(url, timeout=10, headers={"User-Agent": _FIXED_UA})
    resp.raise_for_status()

    try:
        from PIL import Image
        img = Image.open(BytesIO(resp.content))
        original_format = img.format or "?"
        if img.mode != "RGB":
            img = img.convert("RGB")
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".jpg")
        img.save(tmp.name, format="JPEG", quality=85, optimize=True)
        tmp.close()
        log(f"이미지 다운로드+정규화: {tmp.name} "
            f"({original_format} → JPEG, {img.size[0]}x{img.size[1]})", "ok")
        return tmp.name
    except Exception as e:
        log(f"이미지 정규화 실패 ({e}) — raw 저장 폴백", "warn")
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
        tmp.write(resp.content)
        tmp.close()
        return tmp.name


def cleanup(path: str) -> None:
    """임시 이미지 파일 삭제."""
    try:
        os.unlink(path)
    except OSError:
        pass


def get_suffix(url: str) -> str:
    """URL에서 파일 확장자 추출. 없으면 .jpg 반환."""
    ext = Path(url.split("?")[0]).suffix.lower()
    return ext if ext in {".jpg", ".jpeg", ".png", ".gif", ".webp"} else ".jpg"
