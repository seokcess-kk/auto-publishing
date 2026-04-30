"""
이미지 처리 공통 모듈
- 이미지 다운로드 및 임시 저장
- 플랫폼별 이미지 업로드는 각 publisher에서 처리
"""
import os
import tempfile
from pathlib import Path

import requests
from .logger import log


def download(url: str, suffix: str = ".jpg") -> str:
    """URL에서 이미지를 다운로드하고 임시 파일 경로를 반환."""
    resp = requests.get(url, timeout=10)
    resp.raise_for_status()

    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    tmp.write(resp.content)
    tmp.close()
    log(f"이미지 다운로드 완료: {tmp.name}", "ok")
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
