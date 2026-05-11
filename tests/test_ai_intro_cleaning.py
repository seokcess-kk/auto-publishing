"""ai_intro 의 AI 응답 정리 로직 테스트.

목표: AI 가 자주 내는 메타/거부 패턴이 본문에 새지 않도록 가드 유지.
- 거부/되묻기 패턴 검출 (_looks_like_refusal)
- 마크다운 헤더/볼드/코드백틱 제거 (_clean_ai_output)
- 메타 prefix ("도입부입니다:", "다음과 같이…") 제거
"""
from common.ai_intro import _clean_ai_output, _looks_like_refusal


# ── _looks_like_refusal ────────────────────────────────────────────────

def test_refusal_marker_detected():
    assert _looks_like_refusal("사용자가 답변 거부했습니다") is True
    assert _looks_like_refusal("도와드릴 수 있을까요?") is True
    assert _looks_like_refusal("I cannot help with that") is True
    assert _looks_like_refusal("I'm not able to") is True


def test_refusal_only_checks_head_300():
    # 300자 이후의 거부 마커는 무시 (실제 콘텐츠로 봄)
    payload = ("정상 본문 " * 50) + "도와드릴까요"
    assert len(payload) > 300
    assert _looks_like_refusal(payload) is False


def test_refusal_empty_string():
    assert _looks_like_refusal("") is False


# ── _clean_ai_output ───────────────────────────────────────────────────

def test_clean_strips_markdown_bold():
    assert _clean_ai_output("이건 **중요한** 부분") == "이건 중요한 부분"


def test_clean_strips_markdown_headers():
    out = _clean_ai_output("## 헤더\n본문입니다")
    assert "##" not in out
    assert "본문입니다" in out


def test_clean_strips_inline_backticks():
    assert _clean_ai_output("이건 `코드` 입니다") == "이건 코드 입니다"


def test_clean_strips_all_backticks():
    out = _clean_ai_output("```\n블록\n```")
    assert "`" not in out


def test_clean_returns_empty_on_refusal():
    # 거부 패턴이면 빈 문자열 반환 — 호출 측이 폴백 처리하도록
    assert _clean_ai_output("도와드릴까요? 어떤 작업을 원하시는지") == ""
    assert _clean_ai_output("사용자가 답변 거부") == ""


def test_clean_empty_input():
    assert _clean_ai_output("") == ""
    assert _clean_ai_output(None) == ""  # type: ignore


def test_clean_preserves_normal_text():
    text = "이 무선이어폰은 가성비가 뛰어납니다. 매일 사용하기에도 부담 없습니다."
    assert _clean_ai_output(text) == text


def test_clean_strips_trailing_whitespace():
    assert _clean_ai_output("  본문  \n\n  ") == "본문"
