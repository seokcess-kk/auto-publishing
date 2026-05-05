"""
AI 도입부(intro) 생성 공통 모듈.

제공자:
- claude: Claude Code CLI (Haiku 모델)
- gemini: Gemini API (무료 티어)

선택:
- 함수 인자 provider 또는 AI_PROVIDER 환경변수로 지정 (기본 claude).
- 실패 시 반대 제공자로 자동 폴백.
"""
import os
import shutil
import subprocess

from common.logger import log
from sources.gemini_generator import GeminiGenerator


def _resolve_claude_cli() -> str:
    """Claude CLI 경로 결정: 환경변수 → PATH 탐색 → 기본값 'claude'."""
    return os.getenv("CLAUDE_CLI_PATH") or shutil.which("claude") or "claude"


_CLAUDE_CLI = _resolve_claude_cli()
_gemini = None


def _get_gemini():
    global _gemini
    if _gemini is None:
        api_key = os.getenv("GEMINI_API_KEY", "")
        if not api_key or api_key.startswith("your_"):
            return None
        _gemini = GeminiGenerator(api_key)
    return _gemini


def _generate_with_claude(prompt: str) -> str:
    """Claude Code CLI(Max 플랜)로 텍스트 생성. Haiku 모델 사용."""
    try:
        # Claude CLI 는 UTF-8 로 출력. Windows 기본 cp949 로 디코드하면
        # 한글 첫 바이트(0xeb 등)에서 UnicodeDecodeError 가 나므로 명시적으로
        # utf-8 + replace 로 강제. errors="replace" 는 깨진 바이트가 있어도
        # 도입부 생성 자체가 멈추지 않게 한다.
        result = subprocess.run(
            [_CLAUDE_CLI, "-p", prompt,
             "--output-format", "text",
             "--tools", "",
             "--model", "haiku",
             "--system-prompt", "요청된 텍스트만 출력하세요. 설명, 주석, 구분선(---), 메타 정보 없이 본문만 작성하세요."],
            capture_output=True, text=True, timeout=60,
            encoding="utf-8", errors="replace",
        )
        if result.returncode == 0 and result.stdout.strip():
            log("Claude 생성 완료", "ok")
            return result.stdout.strip()
        log(f"Claude 생성 실패: {(result.stderr or '')[:200]}", "error")
        return ""
    except Exception as e:
        log(f"Claude CLI 오류: {e}", "error")
        return ""


def _generate_with_gemini(prompt: str) -> str:
    """Gemini API로 텍스트 생성."""
    gemini = _get_gemini()
    if not gemini:
        log("Gemini API 키 없음", "warn")
        return ""
    return gemini.generate(prompt)


def generate_text(prompt: str, provider: str = None, max_len: int = 400) -> str:
    """주어진 프롬프트로 텍스트 생성. 실패 시 반대 provider 로 폴백.

    Args:
        prompt:   AI 에게 전달할 프롬프트
        provider: "claude" | "gemini" — None 이면 AI_PROVIDER env 또는 claude
        max_len:  반환 문자열 길이 상한 (초과 시 절단)
    """
    if not provider:
        provider = os.getenv("AI_PROVIDER", "claude").lower()

    try:
        if provider == "claude":
            text = _generate_with_claude(prompt)
        else:
            text = _generate_with_gemini(prompt)

        if not text:
            fallback = "gemini" if provider == "claude" else "claude"
            log(f"{provider} 실패, {fallback} 폴백", "warn")
            if fallback == "claude":
                text = _generate_with_claude(prompt)
            else:
                text = _generate_with_gemini(prompt)

        text = text.strip().replace("\n", " ")
        if len(text) > max_len:
            text = text[:max_len]
        return text
    except Exception as e:
        log(f"AI 텍스트 생성 실패: {e}", "warn")
        return ""


def generate_product_intro(keyword: str, products: list) -> str:
    """상품 리스트 키워드로 소개 도입부 생성 (쿠팡/알리 공용)."""
    top3 = [p.get("name", "") for p in products[:3]]
    prompt = (
        f"'{keyword}' 관련 쇼핑 추천 글의 도입부를 작성해줘.\n"
        f"대표 상품: {', '.join(top3)}\n\n"
        f"조건:\n"
        f"- 150~250자 내외\n"
        f"- '{keyword}'를 선택할 때 고려할 포인트 2~3가지 간단히 언급\n"
        f"- 자연스럽고 친근한 톤\n"
        f"- HTML 태그 사용하지 말 것, 순수 텍스트만\n"
        f"- 마크다운 서식(**, ## 등) 사용하지 말 것\n"
        f"- '~입니다', '~드립니다' 체 사용"
    )

    provider = os.getenv("AI_PROVIDER", "claude").lower()
    log(f"AI 상품 소개 생성 ({provider}): {keyword}", "step")
    return generate_text(prompt, provider=provider, max_len=400)
