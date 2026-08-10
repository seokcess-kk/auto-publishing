"""제휴(어필리에이트) 대가성 문구 — 전 채널 단일 진실 원천.

쿠팡 파트너스 최종 승인 반려(2026-08-10) 사유가 이 모듈이 존재하는 이유다:

    "대가성 문구는 활동 게시물 최상단 혹은 제목에 기재하셔야 합니다."

기존 구현은 (a) 본문 맨 아래 푸터에, (b) "제공받을 수 있습니다" 라는 조건부
표현으로 넣고 있었다. 둘 다 심사 가이드 위반이다. 가이드 3요건을 여기 한
곳에서 만족시키고, 모든 파이프라인/퍼블리셔가 이 모듈만 쓰게 한다.

    (1) 각 게시물의 제목 또는 첫 부분에 본문과 구별되게 표기
    (2) 글자 크기를 본문보다 크게 하거나 눈에 띄는 색으로 변경
    (3) '수수료를 지급받을 수 있음' 같은 조건부/불확정 표현 없이 명확하게

가이드: https://partners.coupang.com/#announcements/93

사용 예:

    from common import affiliate_notice as notice

    content = notice.prepend_html(content, notice.COUPANG)   # HTML 본문
    body    = notice.prepend_text(body, notice.ALIEXPRESS)   # SNS 평문
    html    = notice.ensure_html(html)                       # 퍼블리셔 안전망

`AFFILIATE_NOTICE_IN_TITLE=true` 를 .env 에 넣으면 제목에도 "[광고]" 접두어가
붙는다(가이드 (1)의 '제목' 옵션). 기본은 off — 본문 최상단 배너만으로 요건이
충족되고, 제목 접두어는 검색 CTR 을 떨어뜨리기 때문. 재심사가 또 반려되면
이 플래그를 켜서 즉시 강화할 수 있다.
"""
from __future__ import annotations

import os

# ─── 제휴처 식별자 ───────────────────────────────────────────────────────────
COUPANG    = "coupang"
ALIEXPRESS = "aliexpress"
NEWSPICK   = "newspick"

LABEL = "광고"

# 조건부 표현("~받을 수 있습니다") 금지 — 쿠팡 공식 예시문 그대로 확정형.
_SENTENCES = {
    COUPANG:    "이 게시물은 쿠팡 파트너스 활동의 일환으로, "
                "이에 따른 일정액의 수수료를 제공받습니다.",
    ALIEXPRESS: "이 게시물은 알리익스프레스 파트너스 활동의 일환으로, "
                "이에 따른 일정액의 수수료를 제공받습니다.",
    NEWSPICK:   "이 게시물은 뉴스픽 파트너스 활동의 일환으로, "
                "이에 따른 일정액의 수수료를 제공받습니다.",
}

# 삽입 여부 판정용 마커. HTML 주석은 에디터(TinyMCE/SE)가 스트립할 수 있어
# 눈에 보이는 "[광고]" 라벨도 함께 감지한다.
_HTML_MARKER = "<!--affiliate-notice-->"

# 본문 HTML 에서 제휴처를 역추적할 때 쓰는 도메인 조각 (퍼블리셔 안전망용).
_DOMAIN_HINTS = (
    (COUPANG,    ("coupang.com", "coupa.ng", "link.coupang")),
    (ALIEXPRESS, ("aliexpress.com", "s.click.ali", "aliexpress.us")),
    (NEWSPICK,   ("newspic.kr", "newspick")),
)

_ACCENT = "#e4000f"


def _normalize(sources) -> list[str]:
    """소스 인자 정규화 — 중복 제거 + 미지의 값 무시, 빈 값이면 쿠팡 기본."""
    out: list[str] = []
    for s in sources:
        if not s:
            continue
        key = str(s).strip().lower()
        if key in _SENTENCES and key not in out:
            out.append(key)
    return out or [COUPANG]


def notice_sentences(*sources: str) -> list[str]:
    """제휴처별 고지 문장 리스트."""
    return [_SENTENCES[s] for s in _normalize(sources)]


def notice_text(*sources: str, label: bool = True) -> str:
    """평문 한 줄 고지 — SNS(Threads/Pinterest/X)·댓글용.

    label=True 면 "[광고] " 접두어가 붙는다(가이드의 '광고' 명시 예시).
    """
    body = " ".join(notice_sentences(*sources))
    return f"[{LABEL}] {body}" if label else body


def notice_html(*sources: str) -> str:
    """본문 최상단 배너 HTML.

    본문(14px 회색) 대비 16px·볼드·빨강 + 테두리 박스로 '본문과 구별' 요건을
    시각적으로 만족시킨다.
    """
    lines = "".join(
        f'<p style="margin:0 0 6px;font-size:16px;font-weight:700;'
        f'line-height:1.6;color:{_ACCENT};">[{LABEL}] {s}</p>'
        for s in notice_sentences(*sources)
    )
    return (
        f'{_HTML_MARKER}'
        f'<div style="max-width:680px;margin:0 auto 20px;padding:13px 16px;'
        f'border:2px solid {_ACCENT};border-radius:8px;background:#fff5f5;'
        f'text-align:center;">{lines}</div>'
    )


def has_notice(text: str) -> bool:
    """이미 고지가 들어있는지 — 중복 삽입 방지(멱등성 보장)."""
    if not text:
        return False
    return _HTML_MARKER in text or f"[{LABEL}]" in text


def prepend_html(content: str, *sources: str) -> str:
    """HTML 본문 최상단에 고지 배너 삽입. 이미 있으면 그대로 반환.

    `<!-- wp:html -->` 로 시작하는 Gutenberg 블록은 블록 *안쪽* 최상단에
    넣는다 — 블록 밖에 붙이면 WordPress 가 별도 classic 블록으로 쪼갠다.
    """
    banner = notice_html(*sources)
    if not content:
        return banner
    if has_notice(content):
        return content

    marker = "<!-- wp:html -->"
    stripped = content.lstrip()
    if stripped.startswith(marker):
        idx = content.index(marker) + len(marker)
        return content[:idx] + banner + content[idx:]
    return banner + content


def prepend_text(text: str, *sources: str) -> str:
    """평문 본문 첫 줄에 고지 삽입. 이미 있으면 그대로 반환."""
    line = notice_text(*sources)
    if not text:
        return line
    if has_notice(text):
        return text
    return f"{line}\n\n{text}"


def title_with_label(title: str) -> str:
    """AFFILIATE_NOTICE_IN_TITLE=true 일 때만 제목에 "[광고] " 접두.

    기본 off — 본문 최상단 배너로 요건이 충족되고 제목 접두어는 CTR 을
    떨어뜨린다. 재심사가 또 반려되면 .env 한 줄로 강화하는 용도.
    """
    if not title:
        return title
    if os.getenv("AFFILIATE_NOTICE_IN_TITLE", "false").strip().lower() \
            not in ("1", "true", "yes"):
        return title
    if title.lstrip().startswith(f"[{LABEL}]"):
        return title
    return f"[{LABEL}] {title}"


def detect_sources(content: str) -> list[str]:
    """본문에 박힌 링크 도메인으로 제휴처 역추적 (퍼블리셔 안전망용).

    단축 URL(bit.ly 등)은 감지할 수 없다 — 안전망은 어디까지나 보조이고,
    1차 방어선은 각 파이프라인의 명시적 삽입이다.
    """
    if not content:
        return []
    low = content.lower()
    return [src for src, hints in _DOMAIN_HINTS if any(h in low for h in hints)]


def ensure_html(content: str, *fallback_sources: str) -> str:
    """퍼블리셔 진입점 안전망 — 제휴 링크가 있는데 고지가 없으면 삽입.

    파이프라인이 고지 삽입을 빠뜨려도 발행 직전에 마지막으로 채워 넣는다.
    제휴 링크가 없는 순수 정보성 글은 건드리지 않는다.
    """
    if not content or has_notice(content):
        return content
    found = detect_sources(content) or [s for s in fallback_sources if s]
    if not found:
        return content
    return prepend_html(content, *found)
