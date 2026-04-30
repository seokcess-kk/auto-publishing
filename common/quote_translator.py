"""
영문 명언 → 한글 번역 + 짧은 해석 생성 헬퍼.

Claude Code CLI(`claude -p`)를 호출하여 자연스러운 번역과 1~2문장 해석을 받는다.
실패 시 fallback 으로 None 반환 → 호출자는 영문만 노출.

사용 예:
    from common.quote_translator import translate_quote

    result = translate_quote("In the middle of difficulty lies opportunity.",
                              "Albert Einstein")
    # → {"translation": "어려움의 한가운데에 기회가 있다.",
    #    "interpretation": "위기는 ..."}
    # 또는 None
"""
from __future__ import annotations

import json
import re
import subprocess
from typing import Optional

from .logger import log


_TIMEOUT_SEC = 60


def _strip_code_fence(text: str) -> str:
    """```json ... ``` 코드 펜스 제거."""
    text = text.strip()
    # ```json ... ```
    m = re.match(r"^```(?:json)?\s*\n?(.*?)\n?```$", text, re.DOTALL)
    if m:
        return m.group(1).strip()
    return text


def translate_quote(quote: str, author: str = "") -> Optional[dict]:
    """영문 명언 → 한글 번역+해석 dict 반환. 실패 시 None.

    Returns:
        {"translation": "한글 번역", "interpretation": "1~2문장 해석"}
    """
    if not quote or not quote.strip():
        return None

    prompt = (
        f"다음 영문 명언을 한국어로 자연스럽게 번역하고, "
        f"1~2문장으로 짧고 명확한 해석을 추가하세요. "
        f"출력은 JSON 객체만, 다른 텍스트나 설명 없이:\n"
        f'{{"translation":"한글 번역","interpretation":"1~2문장 해석"}}\n\n'
        f'명언: "{quote.strip()}"\n'
    )
    if author:
        prompt += f"저자: {author.strip()}\n"

    try:
        result = subprocess.run(
            ["claude", "-p", prompt],
            capture_output=True, text=True,
            timeout=_TIMEOUT_SEC,
        )
        if result.returncode != 0:
            log(f"[quote_translator] claude CLI 실패 (rc={result.returncode}): "
                f"{result.stderr[:200]}", "warn")
            return None

        raw = result.stdout.strip()
        if not raw:
            log("[quote_translator] claude CLI 빈 응답", "warn")
            return None

        # JSON 파싱 시도 (코드 펜스 제거 후)
        cleaned = _strip_code_fence(raw)
        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError:
            # JSON 본체만 추출 시도 (응답 안에 다른 텍스트 섞인 경우)
            m = re.search(r"\{[^{}]*\"translation\"[^{}]*\}", cleaned, re.DOTALL)
            if not m:
                log(f"[quote_translator] JSON 파싱 실패: {cleaned[:200]}", "warn")
                return None
            try:
                data = json.loads(m.group(0))
            except json.JSONDecodeError:
                return None

        translation = (data.get("translation") or "").strip()
        interpretation = (data.get("interpretation") or "").strip()
        if not translation:
            return None

        log(f"[quote_translator] 번역 성공: {translation[:30]}…", "info")
        return {
            "translation": translation,
            "interpretation": interpretation,
        }
    except subprocess.TimeoutExpired:
        log(f"[quote_translator] claude CLI 타임아웃 ({_TIMEOUT_SEC}s)", "warn")
        return None
    except FileNotFoundError:
        log("[quote_translator] claude CLI 미설치 — 번역 생략", "warn")
        return None
    except Exception as e:
        log(f"[quote_translator] 번역 오류: {e}", "warn")
        return None


__all__ = ["translate_quote"]
