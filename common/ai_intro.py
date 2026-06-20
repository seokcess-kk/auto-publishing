"""
AI 도입부(intro) 생성 공통 모듈.

제공자:
- claude: Anthropic API (Haiku 4.5) — ANTHROPIC_API_KEY 있으면 우선 사용.
          키가 없으면 Claude Code CLI(Max 구독, 느림) 로 자동 폴백.
- gemini: Gemini API (무료 티어, 5 req/min 한도)

선택:
- 함수 인자 provider 또는 AI_PROVIDER 환경변수로 지정 (기본 claude).
- 실패 시 반대 제공자로 자동 폴백 (claude ↔ gemini).

※ '_generate_with_claude' 는 디스패처다: ANTHROPIC_API_KEY 가 있으면
  _generate_with_claude_api (직접 API, ~1-3초), 없으면 _generate_with_claude_cli
  (서브프로세스, 최대 60초 타임아웃) 를 호출한다. 모든 호출부는 이 디스패처만
  쓰므로 키 유무에 따라 경로가 자동 전환된다.
"""
import os
import re
import shutil
import subprocess
import tempfile

from common.logger import log
from sources.gemini_generator import GeminiGenerator


# ─── 출력 정제 헬퍼 ────────────────────────────────────────────────────────
# Claude/Gemini 가 가끔 메타 설명("...도입부입니다", "한 줄 후킹입니다") 또는
# 마크다운(**bold**, 1. 번호, ` 백틱 코드)으로 응답을 감싸 보낸다. SE 에디터/
# HTML 본문에 그대로 박히면 사용자에게 노출되므로 본문 단계에서 일괄 제거한다.

_META_PREFIX_RE = re.compile(
    r"^[^\n]*?(도입부입니다|후킹 멘트입니다|후킹입니다|한 줄 후킹|"
    r"멘트입니다|작성한 글입니다|글입니다|추천 글입니다|"
    r"제목입니다|제목들입니다|제목들을|블로그 제목|"
    r"키워드와 .* 상품을 활용|이 글은|다음은|아래는|"
    r"상품 \d+종에 대한|상품 \d+개에 대한)"
    r"[^\n]*?[:\.]?\s*",
    re.M,
)


# 응답 본문에 등장하면 명백히 메타/안내 — 제목 검증 시 폴백 트리거
_TITLE_META_KEYWORDS = (
    "키워드와", "상품을 활용",
    "제목을", "제목들", "제목 추천", "제목 후보", "제목 5개", "제목 3개",
    "블로그 제목", "발행 제목", "추천 제목", "메인 추천",
    "쇼핑 블로그", "한국어 쇼핑",
    "추천 글", "세 가지 상품", "다음과 같", "아래와 같", "출력 형식",
)
_TITLE_FORBIDDEN_TOKENS = ("##", "**", "###", "[1]", "[2]", "1.", "2.", "3.")
_BOLD_RE = re.compile(r"\*\*([^*]+)\*\*")
_HEAD_RE = re.compile(r"^[#-]+\s*", re.M)
_NUMBER_PREFIX_RE = re.compile(r"^\s*\d+\s*[\.\)\-:]\s*")
_INLINE_BACKTICK_RE = re.compile(r"`([^`]+)`")


# 모델이 콘텐츠 생성 대신 사용자에게 되묻는 메타 응답의 시그니처.
# ⚠️ "도와드릴"/"도와드리겠" 단독은 정상 카피("선택을 도와드리겠습니다")도 오탐하므로
#    되묻기 문맥("무엇을 도와…", "어떻게 도와드릴…")으로 한정한다.
_REFUSAL_MARKERS = (
    "사용자가 답변", "답변을 거부",
    "무엇을 도와", "어떻게 도와드릴", "어떤 작업을 원하",
    "명확히 알려주세요", "원하시는지 파악",
    "I cannot", "I can't help", "I'm not able",
    "예시:\n",
)


def _looks_like_refusal(text: str) -> bool:
    """모델이 콘텐츠 생성 대신 사용자에게 되묻는 응답인지 검출."""
    if not text:
        return False
    head = text[:300]
    return any(m in head for m in _REFUSAL_MARKERS)


def _clean_ai_output(text: str) -> str:
    """모델 응답에서 메타 설명/마크다운/코드 백틱 제거.

    거부/되묻기 응답이면 빈 문자열 반환 — 호출 측이 빈 결과를 폴백 처리하도록.
    """
    if not text:
        return ""
    if _looks_like_refusal(text):
        log("AI 응답이 거부/되묻기 패턴 — 빈 결과로 폴백", "warn")
        return ""
    text = _META_PREFIX_RE.sub("", text, count=1)
    text = _BOLD_RE.sub(r"\1", text)
    text = _INLINE_BACKTICK_RE.sub(r"\1", text)
    text = text.replace("`", "")
    text = _HEAD_RE.sub("", text)
    return text.strip()


def _resolve_claude_cli() -> str:
    """Claude CLI 경로 결정: 환경변수 → PATH 탐색 → 기본값 'claude'."""
    return os.getenv("CLAUDE_CLI_PATH") or shutil.which("claude") or "claude"


_CLAUDE_CLI = _resolve_claude_cli()
_gemini = None
_anthropic_client = None

# Claude/Gemini 공용 시스템 프롬프트 — 모델이 콘텐츠 생성 대신 되묻기·메타 응답을
# 하지 않도록 강제. CLI/API 양쪽에서 동일하게 사용.
_COPYWRITER_SYSPROMPT = (
    "당신은 한국어 카피라이터입니다. 사용자가 보낸 입력은 항상 "
    "콘텐츠 생성 요청이며, 결코 명령 모호성·역할 확인·되묻기·"
    "도움이 필요한지 묻기 같은 메타 응답을 하지 않습니다. "
    "요청된 텍스트만 출력하세요. 설명·주석·구분선·메타 정보·"
    "예시 안내·인사말·자기소개 없이 본문만 작성하세요. "
    "어떤 경우에도 '사용자', '답변 거부', '도와드릴', '명확히 "
    "알려주세요', '예시:' 같은 표현을 사용하지 마세요."
)


def _get_gemini():
    global _gemini
    if _gemini is None:
        api_key = os.getenv("GEMINI_API_KEY", "")
        if not api_key or api_key.startswith("your_"):
            return None
        _gemini = GeminiGenerator(api_key)
    return _gemini


def _get_anthropic():
    """ANTHROPIC_API_KEY 가 있으면 anthropic.Anthropic 클라이언트 반환, 없으면 None."""
    global _anthropic_client
    if _anthropic_client is None:
        api_key = os.getenv("ANTHROPIC_API_KEY", "")
        if not api_key or api_key.startswith("your_") or api_key.startswith("sk-ant-..."):
            return None
        try:
            import anthropic
            _anthropic_client = anthropic.Anthropic(api_key=api_key)
        except Exception as e:
            log(f"Anthropic SDK 초기화 실패: {e}", "warn")
            return None
    return _anthropic_client


def _generate_with_claude_api(prompt: str) -> str:
    """Anthropic API(Haiku 4.5)로 텍스트 생성. 직접 HTTP — CLI 대비 빠르고 안정적.

    Haiku 는 effort/adaptive thinking 미지원 → 기본 messages.create 사용.
    실패(키 없음·429·네트워크) 시 빈 문자열 반환 → 상위가 Gemini 폴백.
    """
    client = _get_anthropic()
    if not client:
        return ""
    model = os.getenv("ANTHROPIC_MODEL", "claude-haiku-4-5")
    try:
        msg = client.messages.create(
            model=model,
            max_tokens=2048,
            system=_COPYWRITER_SYSPROMPT,
            messages=[{"role": "user", "content": prompt}],
        )
        text = "".join(
            getattr(b, "text", "") for b in msg.content
            if getattr(b, "type", "") == "text"
        ).strip()
        if text:
            log("Claude API 생성 완료", "ok")
        return text
    except Exception as e:
        log(f"Claude API 오류: {e}", "error")
        return ""


def _generate_with_claude_cli(prompt: str) -> str:
    """Claude Code CLI(Max 플랜)로 텍스트 생성. Haiku 모델 사용. (API 키 없을 때 폴백)"""
    try:
        # Claude CLI 는 UTF-8 로 출력. Windows 기본 cp949 로 디코드하면
        # 한글 첫 바이트(0xeb 등)에서 UnicodeDecodeError 가 나므로 명시적으로
        # utf-8 + replace 로 강제. errors="replace" 는 깨진 바이트가 있어도
        # 도입부 생성 자체가 멈추지 않게 한다.
        # cwd 를 임시 디렉토리로 격리 — claude CLI 가 프로젝트의 CLAUDE.md
        # /코드 파일을 컨텍스트로 자동 흡수해 "이건 개발 작업" 으로 오해하고
        # 콘텐츠 생성을 거부하는 케이스를 방지한다.
        result = subprocess.run(
            [_CLAUDE_CLI, "-p", prompt,
             "--output-format", "text",
             "--tools", "",
             "--model", "haiku",
             "--system-prompt", _COPYWRITER_SYSPROMPT],
            capture_output=True, text=True, timeout=60,
            encoding="utf-8", errors="replace",
            cwd=tempfile.gettempdir(),
        )
        if result.returncode == 0 and result.stdout.strip():
            log("Claude 생성 완료", "ok")
            return result.stdout.strip()
        log(f"Claude 생성 실패: {(result.stderr or '')[:200]}", "error")
        return ""
    except Exception as e:
        log(f"Claude CLI 오류: {e}", "error")
        return ""


def _generate_with_claude(prompt: str) -> str:
    """Claude 생성 디스패처 — API 키 있으면 Anthropic API(빠름), 없으면 CLI(느림).

    API 키가 있는데 호출이 실패하면 빈 문자열을 반환해 상위가 Gemini 로 폴백하게
    둔다 (느린 CLI 60초 타임아웃을 핫패스에서 회피). 키 자체가 없을 때만 CLI 사용.
    """
    if _get_anthropic():
        return _generate_with_claude_api(prompt)
    return _generate_with_claude_cli(prompt)


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


def generate_newspick_hook(title: str, category: str = "") -> str:
    """뉴스픽 기사 제목으로 클릭을 유도하는 후킹 헤드라인 2~3줄 생성.

    줄바꿈을 보존해서 반환한다 (generate_text 와 달리 \\n 을 공백으로
    치환하지 않음). publisher 의 build_newspick_naver_document 가 줄별로
    SE 컴포넌트를 만든다.
    """
    cat_part = f" / 카테고리 '{category}'" if category else ""
    prompt = (
        f"기사 제목 '{title}'{cat_part} 에 대한 후킹 멘트 3줄을 한국어로 출력하세요.\n"
        f"\n"
        f"출력 형식 (각 줄 30~50자, 줄바꿈 \\n 으로 구분):\n"
        f"1줄차: 호기심을 자극하는 도입 문장 (제목의 핵심 포인트 암시)\n"
        f"2줄차: 독자가 궁금할 만한 디테일 한 가지 (구체적 사실·반전·의외성)\n"
        f"3줄차: 클릭 유도 마무리 문장 (예: '👇 자세한 내용은 아래 기사에서 확인해 보세요')\n"
        f"\n"
        f"제약:\n"
        f"- 출력은 정확히 3줄, 각 줄은 한 문장이며 \\n 로 분리\n"
        f"- 번호 매기기, 라벨('1줄차:'), 마크다운, HTML 모두 금지\n"
        f"- 자극적·낚시성 단어 (충격, 경악, 헉, !!) 자제\n"
        f"- 본문만 출력, 추가 설명·확인 질문·인사말 금지\n"
        f"\n"
        f"예시 (다른 제목 기준):\n"
        f"공무원 시험 합격률이 갑자기 두 배로 뛰었다고 합니다.\n"
        f"이번 변화의 배경에는 한 가지 정책 개편이 있었는데요.\n"
        f"👇 자세한 내용은 아래 기사에서 확인해 보세요"
    )
    provider = os.getenv("AI_PROVIDER", "claude").lower()
    log(f"AI 후킹 멘트 생성 ({provider}): {title[:30]}", "step")

    # generate_text 는 \n 제거하므로 직접 호출
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

    # 줄바꿈 보존, 빈 줄 제거
    lines = [l.strip() for l in (text or "").split("\n") if l.strip()]
    # 마크다운 잔재 제거 — 코드블록·강조 등
    cleaned = []
    for l in lines:
        l = l.lstrip("-•*0123456789. ").strip()
        if l.startswith("```") or l.startswith("---"):
            continue
        cleaned.append(l)
    return "\n".join(cleaned[:4])  # 최대 4줄까지 (안전)


def generate_threads_caption(keyword: str, product: dict,
                              short_url: str = "",
                              max_chars: int = 350) -> str:
    """Threads 톤의 쿠팡 상품 캡션 생성.

    스타일:
        - 첫 줄: 후킹 (질문/숫자/반전, 30자 이내)
        - 빈 줄
        - 본문 1~3 짧은 문장 (각 50자 이내)
        - 빈 줄
        - 한 줄 요약 또는 추천 이유
        - 빈 줄
        - 링크 (publisher 가 하단에 추가하므로 여기선 생략)
        - 해시태그 2~4개 (publisher 가 추가)

    Args:
        keyword:   검색 키워드
        product:   {name, price, rating, review_count, ...}
        short_url: 어필리에이트 단축링크 (참고용, 본문엔 안 박힘)
        max_chars: 최대 글자 수 (해시태그·링크 자리 빼고)
    """
    name   = (product.get("name", "") or "")[:60]
    price  = product.get("price", "") or ""
    rating = product.get("rating", "") or ""
    review = str(product.get("review_count", "") or "")

    meta_bits = []
    if price:
        meta_bits.append(f"가격 {price}")
    if rating:
        meta_bits.append(f"평점 {rating}")
    if review and review != "0":
        meta_bits.append(f"리뷰 {review}")
    meta_str = " / ".join(meta_bits) if meta_bits else "(메타 정보 없음)"

    prompt = (
        f"제품 '{name}' (키워드 '{keyword}', {meta_str}) 에 대한 Threads SNS "
        f"게시물 본문을 한국어 반말로 출력하세요.\n"
        f"\n"
        f"출력 형식 (이 구조 그대로, 빈 줄 포함 정확히 6줄 또는 7줄):\n"
        f"[1줄차] 후킹 — 질문·숫자·반전 중 하나, 30자 이내\n"
        f"[2줄차] (빈 줄)\n"
        f"[3줄차] 본문 1 — 제품 특징 한 가지 구체적으로, 50자 이내\n"
        f"[4줄차] 본문 2 — 추가 디테일 (가격·평점·사용감 등 메타 활용), 50자 이내\n"
        f"[5줄차] (빈 줄)\n"
        f"[6줄차] 마무리 — 가벼운 추천 또는 호기심 자극 한 문장, 50자 이내, 끝에 이모지 1개\n"
        f"\n"
        f"톤 (가장 중요):\n"
        f"- 무조건 반말 — '~이야', '~더라', '~인 듯', '~네', '~어/아', '~지'\n"
        f"- 존댓말 절대 금지 — '~요', '~습니다', '~세요', '~죠', '~이에요' 다 금지\n"
        f"- 친구한테 카톡 보내듯 자연스럽게\n"
        f"- 트위터/인스타 스레드 스타일 (가벼운 일상 언어)\n"
        f"\n"
        f"제약:\n"
        f"- 라벨('[1줄차]' 등) 출력 금지, 본문만 출력\n"
        f"- 해시태그·URL·링크·구매 CTA('구매해', '클릭해', '바로가') 금지\n"
        f"- 마크다운(**, ##), 큰따옴표로 감싸기 금지\n"
        f"- 자극적 단어(충격, 헉, 미친) 금지\n"
        f"- 전체 {max_chars}자 이내\n"
        f"- 메타 코멘트나 안내 문구 절대 금지\n"
        f"- 출력은 곧바로 1줄차 후킹 문장으로 시작\n"
        f"\n"
        f"좋은 출력 예시 (다른 제품 기준):\n"
        f"리뷰 5천 개 넘는 텀블러, 다들 왜 사는지 알겠더라.\n"
        f"\n"
        f"보온이 12시간 간다는 게 진짜 빈말 아니야.\n"
        f"평점 4.8에 가격도 2만원 초반대.\n"
        f"\n"
        f"이런 가성비면 하나쯤 두는 게 맞는 듯 ☕"
    )

    provider = os.getenv("AI_PROVIDER", "claude").lower()
    log(f"AI Threads 캡션 생성 ({provider}): {keyword}", "step")

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

    # 정리: 마크다운/번호 잔재 제거, 빈 줄은 보존, 해시태그·CTA 줄 제거
    import re as _re
    forbidden_phrases = [
        "프로필 링크", "프로필링크", "지금 확인", "지금확인",
        "구매하세요", "구매 하세요", "클릭하세요", "클릭 하세요",
        "바로가기", "바로 가기", "링크에서", "링크 에서",
        "쿠팡파트너스", "광고 포함", "Disclosure",
    ]

    cleaned_lines: list = []
    hashtag_block_started = False
    for raw in (text or "").split("\n"):
        line = raw.rstrip()
        # 마크다운 강조 표시 제거
        line = line.replace("**", "").replace("__", "")
        # 줄 첫 머리 번호/대시 제거
        line = line.lstrip("-•* ").rstrip()
        # 줄 양쪽 따옴표 제거 (Claude 가 가끔 본문을 따옴표로 감쌈)
        line = line.strip("\"'`")

        # 해시태그 줄 (한 줄에 # 가 2개 이상) → 본문 종료 신호로 보고 컷
        if line.count("#") >= 2:
            hashtag_block_started = True
            break
        # 단일 #해시태그 단독 줄도 제거
        stripped = line.lstrip()
        if stripped.startswith("#") and " " not in stripped:
            continue
        # 금지 어휘 포함 줄 제거
        if any(p in line for p in forbidden_phrases):
            continue

        cleaned_lines.append(line)

    cleaned = "\n".join(cleaned_lines).strip()
    # 연속 빈 줄 3개 이상은 2개로 압축
    cleaned = _re.sub(r"\n{3,}", "\n\n", cleaned)

    if len(cleaned) > max_chars:
        cleaned = cleaned[:max_chars - 3] + "..."
    return cleaned


def generate_newspick_threads_caption(title: str, category: str = "",
                                        max_chars: int = 230) -> str:
    """뉴스픽 기사 제목으로 Threads 단일 게시물 본문 생성 (반말).

    구조:
        - 1줄차: 호기심 유발 후킹 (제목 핵심 1포인트)
        - 빈 줄
        - 2~3줄차: 짧은 안내·디테일 (사람들 반응 / 배경 / 의외성)
        - 빈 줄
        - 마지막: 클릭 유도 한 줄 (질문형 또는 이모지)

    링크·해시태그·의무 고지는 caller (pipeline) 가 별도 부착.
    """
    cat_part = f" ({category} 카테고리)" if category else ""
    prompt = (
        f"뉴스 기사 제목 '{title}'{cat_part} 에 대한 Threads SNS 게시물 본문을 "
        f"한국어 반말로 출력하세요.\n"
        f"\n"
        f"출력 형식 (정확히 5~6줄, 빈 줄 포함):\n"
        f"[1줄차] 후킹 — 호기심 자극 한 문장 (질문·반전·숫자)\n"
        f"[2줄차] (빈 줄)\n"
        f"[3줄차] 본문 1 — 사람들 반응 / 배경 한 줄\n"
        f"[4줄차] 본문 2 — 의외성·디테일 추가 한 줄\n"
        f"[5줄차] (빈 줄)\n"
        f"[6줄차] 마무리 — '👇 자세한 내용은 아래에서' 류 클릭 유도 한 줄\n"
        f"\n"
        f"톤 (가장 중요):\n"
        f"- 무조건 반말 ('~이야', '~더라', '~네', '~지', '~어/아')\n"
        f"- 존댓말 ('~요', '~습니다', '~세요') 절대 금지\n"
        f"- 친구한테 카톡 보내듯 자연스럽게\n"
        f"\n"
        f"제약:\n"
        f"- 라벨('[1줄차]') 출력 금지, 본문만\n"
        f"- 해시태그·URL·구매 CTA 금지\n"
        f"- 마크다운(**, ##) 금지, 큰따옴표로 감싸기 금지\n"
        f"- 자극적 단어 (충격, 헉, 미친) 금지\n"
        f"- 전체 {max_chars}자 이내\n"
        f"- 메타 코멘트·안내 문구 금지, 즉시 1줄차로 시작\n"
        f"\n"
        f"좋은 출력 예시 (다른 제목 기준):\n"
        f"이 두 사람이 부부였다고? 진짜?\n"
        f"\n"
        f"방송에선 한 번도 같이 안 나왔던 사이인데\n"
        f"알고 보니 결혼 12년차라 다들 충격 받는 중\n"
        f"\n"
        f"👇 누구인지 자세한 내용은 아래에서 확인해봐"
    )

    provider = os.getenv("AI_PROVIDER", "claude").lower()
    log(f"AI 뉴스픽 Threads 캡션 생성 ({provider}): {title[:30]}", "step")

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

    # 후처리 — 마크다운/금지 어휘 제거 (generate_threads_caption 와 동일)
    import re as _re
    forbidden_phrases = [
        "프로필 링크", "프로필링크", "지금 확인", "지금확인",
        "구매하세요", "구매 하세요", "클릭하세요", "클릭 하세요",
        "바로가기", "바로 가기", "광고 포함", "Disclosure",
    ]
    cleaned_lines: list = []
    for raw in (text or "").split("\n"):
        line = raw.rstrip()
        line = line.replace("**", "").replace("__", "")
        line = line.lstrip("-•* ").rstrip()
        line = line.strip("\"'`")
        if line.count("#") >= 2:
            break
        stripped = line.lstrip()
        if stripped.startswith("#") and " " not in stripped:
            continue
        if any(p in line for p in forbidden_phrases):
            continue
        cleaned_lines.append(line)
    cleaned = "\n".join(cleaned_lines).strip()
    cleaned = _re.sub(r"\n{3,}", "\n\n", cleaned)
    if len(cleaned) > max_chars:
        cleaned = cleaned[:max_chars - 3] + "..."
    return cleaned


def generate_threads_chain(keyword: str, product: dict,
                            short_url: str = "",
                            max_chars_each: int = 220) -> list:
    """Threads reply chain 용 3개 캡션 생성.

    구조:
        [1] 후킹 + '↓ 더보기' (참여 유도, 90~150자)
        [2] 상품 디테일 + 평점/가격 (150~220자)
        [3] 링크 + 가벼운 마무리 + 질문형 CTA (100~180자)

    Returns:
        [hook_text, detail_text, link_text] 형태 리스트.
        각 항목은 publisher 의 post() / post_reply() 에 그대로 전달.
        실패 시 길이 1짜리 폴백 리스트 반환.
    """
    name   = (product.get("name", "") or "")[:60]
    price  = product.get("price", "") or ""
    rating = product.get("rating", "") or ""
    review = str(product.get("review_count", "") or "")
    discount = product.get("discount_rate", "") or ""

    meta_bits = []
    if discount:
        meta_bits.append(f"할인 {discount}")
    if price:
        meta_bits.append(f"가격 {price}원")
    if rating:
        meta_bits.append(f"평점 {rating}")
    if review and review != "0":
        meta_bits.append(f"리뷰 {review}개")
    meta_str = " / ".join(meta_bits) if meta_bits else "(메타 없음)"

    prompt = (
        f"제품 '{name}' (키워드 '{keyword}', {meta_str}) 에 대한 Threads "
        f"reply chain 3편을 한국어 반말로 출력하세요.\n"
        f"\n"
        f"출력 형식 (반드시 이 구분자로):\n"
        f"=== 1 ===\n"
        f"<후킹 게시물 본문>\n"
        f"=== 2 ===\n"
        f"<상품 디테일 본문>\n"
        f"=== 3 ===\n"
        f"<링크/마무리 본문>\n"
        f"\n"
        f"각 게시물 가이드:\n"
        f"[1] 후킹 (90~150자):\n"
        f"  - 첫 줄 강한 후킹 (질문·숫자·반전·공감)\n"
        f"  - 1~2 문장만, 마지막 줄에 '↓ 자세한 정보' 또는 '↓ 더 알려줄게' 같은 유도\n"
        f"  - 끝에 이모지 1개\n"
        f"\n"
        f"[2] 디테일 (150~220자):\n"
        f"  - 제품 핵심 특징 2~3가지를 짧은 문장으로 나열\n"
        f"  - 가격·평점·리뷰 수 자연스럽게 끼워넣기\n"
        f"  - 사용 상황 묘사 ('출퇴근', '캠핑', '잠자기 전' 등)\n"
        f"\n"
        f"[3] 링크/마무리 (100~180자):\n"
        f"  - 추가 한 줄 정보 또는 추천 이유\n"
        f"  - 마지막에 '👉 ' 표시는 넣지 마라 (publisher 가 별도로 링크 추가)\n"
        f"  - 끝에 질문형 CTA ('이거 써본 사람 있어?', '추천 더 있으면 댓글 부탁')\n"
        f"  - 끝에 이모지 1개\n"
        f"\n"
        f"공통 톤:\n"
        f"- 무조건 반말 ('~이야', '~더라', '~네', '~지')\n"
        f"- 존댓말 ('~요', '~습니다') 절대 금지\n"
        f"- 친구한테 카톡 하듯 자연스럽게\n"
        f"- 이모지 각 게시물당 1~2개 (남발 금지)\n"
        f"- 해시태그·URL·구매 CTA('구매해', '클릭') 금지\n"
        f"- 마크다운(**, ##) 금지\n"
        f"- 출력은 즉시 '=== 1 ===' 로 시작, 메타 안내 금지\n"
        f"- 각 게시물 {max_chars_each}자 이내\n"
    )

    provider = os.getenv("AI_PROVIDER", "claude").lower()
    log(f"AI Threads chain 캡션 생성 ({provider}): {keyword}", "step")

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
    if not text:
        return []

    # 파싱 — Claude 가 다양한 마커 형식 사용 가능:
    #   === 1 ===, **1편**, ###1, [1], [1/3], 1편:, 1., (1)
    # 한 줄 전체가 섹션 마커인 경우만 split (본문 중간의 숫자 보호)
    import re as _re
    section_marker = _re.compile(
        r"^\s*"
        r"(?:=+\s*)?(?:\*{1,2}|#{1,3}|\[|\()?\s*"        # 시작 장식 ===, **, ##, [, (
        r"(?:thread|post|part|section|글|편|챕터)?\s*"    # 영어/한글 접두 (옵션)
        r"\d{1,2}"                                       # 숫자 1~99
        r"(?:\s*/\s*\d{1,2})?"                           # /3 같은 분모 (옵션)
        r"\s*(?:편|번|st|nd|rd|th|화|차|:)?"             # 한국어/영어 접미
        r"\s*(?:\*{1,2}|\]|\))?\s*[:\.\-]?\s*"           # 끝 장식 **, ], ), :, ., -
        r"(?:=+\s*)?$",                                  # 트레일링 ===
        flags=_re.IGNORECASE,
    )
    lines = text.split("\n")
    parts: list = []
    buffer: list = []
    saw_marker = False
    for line in lines:
        if section_marker.match(line):
            if buffer and saw_marker:
                parts.append("\n".join(buffer).strip())
            buffer = []
            saw_marker = True
            continue
        # 마커 전 텍스트는 버림 (Claude 가 헤더 안내 붙인 경우)
        if saw_marker:
            buffer.append(line)
    if buffer and saw_marker:
        parts.append("\n".join(buffer).strip())

    # 마커 패턴이 전혀 안 맞은 경우 — 빈 줄 2개 이상으로 분리해 폴백
    if len(parts) < 2:
        parts = _re.split(r"\n\s*\n\s*\n", text.strip())  # 3개 이상 빈 줄로
        if len(parts) < 2:
            # 빈 줄 2개로도 시도
            blocks = _re.split(r"\n\s*\n", text.strip())
            if len(blocks) >= 3:
                parts = blocks  # 단락 단위로 사용

    parts = [p.strip() for p in parts if p.strip()]

    # 후처리 — 각 파트의 마크다운/금지 어휘 제거 (single 함수 로직 재사용)
    forbidden_phrases = [
        "프로필 링크", "프로필링크", "지금 확인", "지금확인",
        "구매하세요", "구매 하세요", "클릭하세요", "클릭 하세요",
        "바로가기", "바로 가기", "쿠팡파트너스", "광고 포함", "Disclosure",
    ]
    cleaned: list = []
    for p in parts[:3]:
        lines: list = []
        for raw in p.split("\n"):
            line = raw.rstrip()
            line = line.replace("**", "").replace("__", "")
            line = line.lstrip("-•* ").rstrip()
            line = line.strip("\"'`")
            # 본문에 섭벅 남은 섹션 마커 제거 ([1/3] 같은 첫 줄)
            if section_marker.match(line):
                continue
            if line.count("#") >= 2:
                break
            stripped = line.lstrip()
            if stripped.startswith("#") and " " not in stripped:
                continue
            if any(fp in line for fp in forbidden_phrases):
                continue
            lines.append(line)
        body = "\n".join(lines).strip()
        body = _re.sub(r"\n{3,}", "\n\n", body)
        if len(body) > max_chars_each:
            body = body[:max_chars_each - 3] + "..."
        if body:
            cleaned.append(body)

    return cleaned


def generate_related_tags(title: str, context: str = "",
                           n: int = 4, exclude: list = None) -> list:
    """제목/컨텍스트에서 검색 친화적인 한국어 태그 n 개 추출.

    네이버 블로그·티스토리·카페의 검색 노출에 도움이 되는 짧은 명사 위주
    태그를 만든다. 공백·특수문자 없이 단어 1~3개 길이.

    Args:
        title:   기사/포스트 제목
        context: 카테고리·키워드 등 추가 힌트 (선택)
        n:       생성할 태그 수
        exclude: 결과에서 제외할 태그 (이미 정적으로 들어갈 태그 중복 방지)

    Returns:
        ["태그1", "태그2", ...] 형태 리스트. 실패 시 빈 리스트.
    """
    exclude = exclude or []
    ctx_part = f" / 컨텍스트: {context}" if context else ""
    prompt = (
        f"제목 '{title}'{ctx_part} 에서 SEO 검색 태그 {n}개를 한국어로 추출하세요.\n"
        f"\n"
        f"출력 형식: 한 줄에 하나, 태그만 출력 (앞뒤 # 없이)\n"
        f"\n"
        f"제약:\n"
        f"- 정확히 {n}개 줄 (한 줄 = 한 태그)\n"
        f"- 각 태그는 공백·특수문자 없는 명사 (1~3 단어 합쳐서 8자 이내)\n"
        f"- 인명·고유명사·핵심 키워드 우선 (제목에 등장하는 단어 활용)\n"
        f"- 카테고리/플랫폼 일반어 (뉴스, 정보, 추천 등) 제외\n"
        f"- 마크다운·번호·라벨·따옴표 금지, 본문만 출력\n"
        f"\n"
        f"예시 (제목 '삼성전자 노조 성과급 너머의 책임' 기준):\n"
        f"삼성전자\n"
        f"노조\n"
        f"성과급\n"
        f"기업책임"
    )
    provider = os.getenv("AI_PROVIDER", "claude").lower()
    log(f"AI 관련 태그 생성 ({provider}): {title[:30]}", "step")

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

    # Claude 가 가끔 한 줄에 콤마로 묶어 출력 → \n 과 ,/、/· 모두로 split
    import re as _re
    tokens = _re.split(r"[\n,，、·•/]+", text or "")
    cleaned: list = []
    for tok in tokens:
        tok = tok.strip()
        # 마크다운/번호 잔재 제거
        tok = tok.lstrip("-•*0123456789. #").strip()
        tok = tok.strip("#\"'`「」『』<>()[]【】 ").strip()
        if not tok or tok.startswith("```") or tok.startswith("---"):
            continue
        # 공백·특수문자 제거 (한글·영문·숫자만 유지)
        tok = _re.sub(r"\s+", "", tok)
        tok = _re.sub(r"[^\w가-힣0-9]", "", tok, flags=_re.UNICODE)
        if not tok or len(tok) > 12:
            continue
        if tok in exclude or tok in cleaned:
            continue
        cleaned.append(tok)
        if len(cleaned) >= n:
            break
    return cleaned


def generate_product_intro(keyword: str, products: list) -> str:
    """상품 리스트 키워드로 소개 도입부 생성 (쿠팡/알리 공용).

    톤: 후킹 우선 + 자연스러운 키워드 반복 → SEO 신호 + 클릭 유도.
    분량은 짧게 유지(250~300자) — 이후 카드별 픽 이유로 키워드 spread.
    """
    top3 = [p.get("name", "") for p in products[:3]][:3]
    n = len(products)
    prompt = (
        f"키워드 '{keyword}' 와 대표 상품 ({', '.join(top3)}) 에 대한 한국어 "
        f"쇼핑 블로그 도입부를 250~300자로 작성하세요.\n\n"
        f"조건:\n"
        f"- 첫 문장은 후킹 (질문/숫자/공감 중 하나)\n"
        f"- '{keyword}' 키워드를 본문에 2~3회 자연스럽게 반복\n"
        f"- 1줄짜리 짧은 문장 섞어 호흡 끊기\n"
        f"- 마지막은 '아래 TOP{n}을 보시죠' 같은 카드 유도\n"
        f"- 종결은 '~입니다'/'~드립니다' 체\n"
        f"- 도입부 본문만 출력 (메타 설명·마크다운·따옴표·prefix 금지)"
    )

    provider = os.getenv("AI_PROVIDER", "claude").lower()
    log(f"AI 상품 소개 생성 ({provider}): {keyword}", "step")
    raw = generate_text(prompt, provider=provider, max_len=380)
    cleaned = _clean_ai_output(raw)
    # 길이 보정 — 모델이 300자 제약 어기면 자연 문장 경계에서 절단
    if len(cleaned) > 300:
        cut = cleaned[:300]
        last_dot = max(cut.rfind("."), cut.rfind("!"), cut.rfind("?"))
        if last_dot > 180:
            cleaned = cut[: last_dot + 1]
        else:
            cleaned = cut
    return cleaned


def generate_product_title(keyword: str, products: list) -> str:
    """35~45자 한국어 발행 제목 생성. 키워드 + 상품 핵심어 + 후킹.

    네이버 블로그 모바일/PC, 티스토리, 구글 SERP 모두 안 잘리는 길이.
    AI 실패/거부 시 빈 문자열 반환 — 호출 측이 폴백 템플릿 사용.
    """
    if not products:
        return ""
    top3 = [p.get("name", "") for p in products[:3]][:3]
    n = len(products)
    prompt = (
        f"키워드 '{keyword}' 와 상품 ({', '.join(top3)}) 로 한국어 쇼핑 블로그 "
        f"발행 제목을 작성하세요.\n\n"
        f"제약 (반드시 지킬 것):\n"
        f"- 정확히 35~45자 (절대 45자 초과 금지)\n"
        f"- 키워드 '{keyword}' 를 제목 앞쪽 절반에 1회 노출\n"
        f"- 상품 핵심어 1개를 본문에 자연스럽게 녹임\n"
        f"- 클릭 유도 요소 — 숫자(TOP{n} 등), 감정어(꿀템/베스트/인기/추천), "
        f"질문, 구체 효용 중 1가지 포함\n"
        f"- 마침표·따옴표·이모지·해시태그·괄호 라벨·마크다운 금지\n"
        f"- 제목 1줄만 출력 (메타 설명 금지)"
    )
    provider = os.getenv("AI_PROVIDER", "claude").lower()
    log(f"AI 제목 생성 ({provider}): {keyword}", "step")
    raw = generate_text(prompt, provider=provider, max_len=60)
    cleaned = _clean_ai_output(raw)
    # 응답 전체 검사 — 마크다운/번호 매김(여러 후보 응답 신호)이 1개라도
    # 등장하면 폴백 (모델이 제목 1개 대신 안내문 + 후보 여러 개를 뱉은 것)
    if cleaned:
        if any(tok in cleaned for tok in _TITLE_FORBIDDEN_TOKENS):
            log(f"AI 제목 응답에 마크다운/번호 매김 — 폴백으로 전환: "
                f"{cleaned[:50]}...", "warn")
            return ""
        # 줄바꿈 2개 이상 = 다중 후보 응답
        if cleaned.count("\n") >= 2:
            log(f"AI 제목 응답이 다중 줄 — 폴백으로 전환: "
                f"{cleaned[:50]}...", "warn")
            return ""

    # 첫 줄만 사용 + 길이 절단
    first = cleaned.split("\n")[0].strip(" \t-•·\"'") if cleaned else ""
    if not first:
        return ""
    # 메타/안내 응답 패턴이 본문에 박혔으면 폴백 트리거
    if any(kw in first for kw in _TITLE_META_KEYWORDS):
        log(f"AI 제목이 메타 응답 — 폴백으로 전환: {first[:40]}...", "warn")
        return ""
    if len(first) > 45:
        first = first[:45].rsplit(" ", 1)[0] if " " in first[:45] else first[:45]
    if len(first) < 20:  # 너무 짧으면 폴백 트리거
        return ""
    return first


def generate_product_pick_reasons(keyword: str, products: list) -> list:
    """각 상품의 한 줄 픽 이유 N개 생성 — 카드 직전 paragraph 로 사용.

    카드와 카드 사이 자연어 텍스트를 분산 삽입해 SEO + 재미 + 클릭 유도.
    각 줄은 50~80자, 후킹/리액션 톤으로 키워드 자연 노출.

    Returns:
        len(products) 와 동일 길이 리스트. 실패 시 빈 리스트.
    """
    if not products:
        return []

    n = len(products)
    bullets = []
    for i, p in enumerate(products[:n], 1):
        nm = (p.get("name", "") or "")[:60]
        pr = p.get("price", "") or ""
        rt = p.get("rating", "") or ""
        bullets.append(f"{i}. {nm} (가격 {pr or '-'}, 평점 {rt or '-'})")

    prompt = (
        f"키워드 '{keyword}' 의 추천 상품 {n}개 각각에 대한 한국어 한 줄 후킹 "
        f"멘트를 작성하세요. 각 멘트는 50~80자, 그 상품을 클릭하고 싶게 만드는 "
        f"짧은 문장입니다.\n\n"
        f"상품 {n}개:\n" + "\n".join(bullets) + "\n\n"
        f"⚠ 매우 중요: 각 줄은 반드시 해당 상품 이름의 핵심 단어 1~2개를 본문에 "
        f"녹여 써야 합니다. 키워드 '{keyword}' 만 가지고 일반화된 멘트를 만들면 "
        f"안 됩니다 (예: 상품이 '코스프레 의상' 인데 멘트가 '자유이용권' 이면 X).\n\n"
        f"출력 형식 (반드시 이 구조):\n"
        f"1. <첫 번째 상품에 맞춘 한 줄 후킹>\n"
        f"2. <두 번째 상품에 맞춘 한 줄 후킹>\n"
        f"... ({n}번까지, 순서대로 1번 = 위 1번 상품)\n\n"
        f"조건:\n"
        f"- 정확히 {n}줄. 각 줄 '1. ', '2. ' 식 번호로 시작 (1줄에 다 몰지 말 것)\n"
        f"- 50~80자 / 의외성·감탄·공감 톤\n"
        f"- 해당 상품 이름의 핵심 단어 1~2개를 본문에 자연스럽게 노출\n"
        f"- 종결 '~입니다'/'~네요'/'~죠' 체\n"
        f"- 따옴표·이모지·해시태그·마크다운·메타 prefix 금지\n"
        f"- 후킹 본문만 출력 (\"한 줄 후킹입니다\" 같은 안내 금지)"
    )

    provider = os.getenv("AI_PROVIDER", "claude").lower()
    log(f"AI 픽 이유 생성 ({provider}): {keyword} ({n}개)", "step")
    text = generate_text(prompt, provider=provider, max_len=80 * n + 200)
    if not text:
        return []

    text = _clean_ai_output(text)

    # 줄바꿈이 부족하고 inline 번호("1. ... 2. ... 3. ...") 만 있는 경우
    # number prefix 로 split 하여 강제 분리
    if text.count("\n") < n - 1:
        # "숫자. " 가 문장 중간에 등장하면 그 앞에서 줄바꿈 삽입
        text = re.sub(r"(?<=[가-힣\.\!\?\)])\s+(\d+)\s*[\.\)]\s*", r"\n\1. ", text)

    lines: list = []
    for raw in text.split("\n"):
        ln = raw.strip()
        if not ln:
            continue
        # "1. ", "1) ", "1- ", "1: " 번호 prefix 제거
        ln = _NUMBER_PREFIX_RE.sub("", ln)
        ln = ln.strip(" \t-•·\"'")
        if len(ln) < 10:
            continue
        # 메타 라인 스킵
        meta_kw = ("한 줄 후킹", "도입부", "추천 글", "다음과 같",
                    "아래와 같", "후킹 멘트", "멘트입니다")
        if any(kw in ln for kw in meta_kw) and len(ln) < 50:
            continue
        # 너무 긴 라인 절단 (모델이 80자 어겨도 안전망)
        if len(ln) > 100:
            ln = ln[:100].rsplit(" ", 1)[0] if " " in ln[:100] else ln[:100]
        lines.append(ln)

    if len(lines) >= n:
        return lines[:n]
    return lines + [""] * (n - len(lines))


def generate_newspick_title(raw_title: str, category: str = "") -> str:
    """뉴스픽 기사 원문 제목을 검색·클릭 친화 제목으로 재작성.

    원문 헤드라인을 그대로 발행하면 같은 기사를 올린 수많은 블로그와 중복돼
    SERP/피드에서 묻힌다. 의미·사실은 보존하되 호기심 갭·구체성을 더한
    30~45자 제목으로 바꾼다. 실패/거부 시 빈 문자열 → 호출 측이 원문 폴백.
    """
    if not raw_title:
        return ""
    cat_part = f" / 카테고리 '{category}'" if category else ""
    prompt = (
        f"뉴스 기사 원문 제목 '{raw_title}'{cat_part} 를 검색·클릭을 더 유도하는 "
        f"블로그 제목으로 다시 쓰세요.\n\n"
        f"제약 (반드시 지킬 것):\n"
        f"- 정확히 30~45자 (45자 초과 금지)\n"
        f"- 원문의 핵심 의미·사실 유지 (없는 내용 지어내기 금지)\n"
        f"- 원문과 단어가 최대한 겹치지 않게 표현을 바꿔 차별화\n"
        f"- 호기심 갭·구체성·숫자 중 1가지로 클릭 유도\n"
        f"- 마침표·따옴표·이모지·해시태그·괄호 라벨·마크다운 금지\n"
        f"- 자극적 낚시성 단어(충격/경악/헉) 금지\n"
        f"- 제목 1줄만 출력 (메타 설명 금지)"
    )
    log(f"AI 뉴스픽 제목 재작성: {raw_title[:30]}", "step")

    def _validate(raw: str) -> str:
        cleaned = _clean_ai_output(raw)
        if not cleaned:
            return ""
        if any(tok in cleaned for tok in _TITLE_FORBIDDEN_TOKENS):
            return ""
        if cleaned.count("\n") >= 2:
            return ""
        first = cleaned.split("\n")[0].strip(" \t-•·\"'")
        if not first or any(kw in first for kw in _TITLE_META_KEYWORDS):
            return ""
        if len(first) > 45:
            first = first[:45].rsplit(" ", 1)[0] if " " in first[:45] else first[:45]
        if len(first) < 18:
            return ""
        return first

    # AI_PROVIDER 우선순위대로 시도(기본 claude=Anthropic API). 검증을 통과한
    # 응답만 채택하고, 비거나 형식 위반이면 반대 제공자로 폴백 — 단순 'empty 시
    # 폴백'의 사각지대(응답은 있으나 형식 위반)를 메운다.
    provider = os.getenv("AI_PROVIDER", "claude").lower()
    order = ((_generate_with_claude, _generate_with_gemini)
             if provider == "claude"
             else (_generate_with_gemini, _generate_with_claude))
    for gen in order:
        result = _validate(gen(prompt))
        if result:
            return result
    return ""


def generate_newspick_article(raw_title: str, category: str = "") -> str:
    """뉴스픽 기사 제목으로 검색 색인용 본문(HTML) 생성.

    뉴스픽 원문 본문은 수집되지 않으므로(소스가 title/링크만 제공) 제목을
    근거로 '왜 이 소식이 화제인지' 맥락·궁금증을 풀어쓴 400~600자 본문을
    만든다. 목적:
      - thin content 탈출 (검색 색인·체류시간 확보)
      - 단정/허위 사실 생성 금지 — 제목에서 알 수 있는 범위의 맥락만
      - <h2> 소제목 + <p> 단락 구조 (SEO heading 신호)

    링크·CTA·해시태그는 호출 측(파이프라인)이 별도 부착한다.
    실패 시 빈 문자열 → 호출 측이 기존 단순 구조로 폴백.
    """
    if not raw_title:
        return ""
    cat_part = f" ({category} 카테고리)" if category else ""
    prompt = (
        f"뉴스 기사 제목 '{raw_title}'{cat_part} 에 대한 한국어 블로그 본문을 "
        f"HTML 로 작성하세요. 원문 본문은 제공되지 않으니, 제목에서 알 수 있는 "
        f"범위의 맥락·배경·사람들이 궁금해할 포인트를 풀어 쓰세요.\n\n"
        f"출력 형식 (HTML, 이 구조):\n"
        f"<p>도입 — 이 소식이 왜 화제인지 2~3문장</p>\n"
        f"<h2>핵심 포인트</h2>\n"
        f"<p>궁금증을 자아내는 배경·맥락 2~3문장</p>\n"
        f"<h2>왜 주목받나</h2>\n"
        f"<p>독자 관점의 의미·반응 2~3문장</p>\n\n"
        f"제약 (반드시 지킬 것):\n"
        f"- 전체 400~600자\n"
        f"- 구체적 수치·인용·날짜·실명 발언을 지어내지 말 것. 단정 대신 "
        f"'~로 알려졌다', '~해 화제다', '~라는 반응이 나온다' 같은 표현 사용\n"
        f"- <p> 와 <h2> 태그만 사용 (다른 태그·마크다운·코드블록 금지)\n"
        f"- 마지막에 링크·CTA·해시태그 넣지 말 것 (호출 측이 별도 추가)\n"
        f"- 본문 HTML 만 출력 (메타 설명·인사말 금지)"
    )
    # AI_PROVIDER 우선순위대로 생성(기본 claude=Anthropic API, ~1-3초). 실패 시
    # 반대 제공자 폴백. (과거 'claude'가 Claude CLI 였을 땐 400~600자 본문이 60초
    #  타임아웃을 자주 초과해 Gemini 우선이었으나, 이제 claude 는 API 라 빠르고 안정적.)
    log(f"AI 뉴스픽 본문 생성: {raw_title[:30]}", "step")
    provider = os.getenv("AI_PROVIDER", "claude").lower()
    if provider == "claude":
        text = _generate_with_claude(prompt) or _generate_with_gemini(prompt)
    else:
        text = _generate_with_gemini(prompt) or _generate_with_claude(prompt)
    if not text or _looks_like_refusal(text):
        return ""

    import re as _re
    body = text.strip()
    # 코드펜스 제거
    body = _re.sub(r"^```[a-zA-Z]*\n?", "", body)
    body = body.replace("```", "").strip()
    body = _BOLD_RE.sub(r"\1", body)
    # 위험 태그 방어 제거
    body = _re.sub(r"(?is)<\s*script.*?>.*?<\s*/\s*script\s*>", "", body)
    # <p>/<h2> 가 전혀 없으면 줄단위로 <p> 래핑해 최소 구조 보장
    if "<p" not in body and "<h2" not in body:
        paras = [p.strip() for p in body.split("\n") if p.strip()]
        body = "".join(f"<p>{p}</p>" for p in paras)
    return body.strip()
