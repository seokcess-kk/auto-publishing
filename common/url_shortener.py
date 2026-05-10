"""
URL 단축 공통 모듈
- 여러 서비스에 폴백 방식으로 단축 시도
- 기존 wordpress/coupang 스크립트의 폴백 로직 통합
"""
import requests
from .logger import log


def shorten(url: str, timeout: int = 5) -> str:
    """URL을 단축하여 반환. 모든 서비스 실패 시 원본 URL 반환."""
    services = [
        _isgd,
        _tinyurl,
        _clckru,
    ]
    for fn in services:
        try:
            short = fn(url, timeout)
            if short:
                log(f"URL 단축 성공 ({fn.__name__}): {short}", "ok")
                return short
        except Exception as e:
            log(f"URL 단축 실패 ({fn.__name__}): {e}", "warn")
    log("모든 URL 단축 서비스 실패, 원본 URL 반환", "warn")
    return url


def _is_valid_short_url(text: str) -> bool:
    """단축 서비스가 'Error: ...' 같은 에러 메시지를 200 으로 반환하는 경우 거름."""
    if not text:
        return False
    if text.lower().startswith("error"):
        return False
    if not (text.startswith("http://") or text.startswith("https://")):
        return False
    if len(text) > 200:
        # 단축 URL 이 200자 이상이면 비정상 (HTML 페이지 등)
        return False
    return True


def _isgd(url: str, timeout: int) -> str:
    resp = requests.get(
        "https://is.gd/create.php",
        params={"format": "simple", "url": url},
        timeout=timeout,
    )
    resp.raise_for_status()
    candidate = resp.text.strip()
    return candidate if _is_valid_short_url(candidate) else ""


def _tinyurl(url: str, timeout: int) -> str:
    resp = requests.get(
        "https://tinyurl.com/api-create.php",
        params={"url": url},
        timeout=timeout,
    )
    resp.raise_for_status()
    candidate = resp.text.strip()
    return candidate if _is_valid_short_url(candidate) else ""


def _clckru(url: str, timeout: int) -> str:
    resp = requests.get(
        "https://clck.ru/--",
        params={"url": url},
        timeout=timeout,
    )
    resp.raise_for_status()
    candidate = resp.text.strip()
    return candidate if _is_valid_short_url(candidate) else ""
