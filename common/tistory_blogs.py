"""
티스토리 블로그 라우팅 — 파이프라인 역할별 블로그 ID 해석

여러 티스토리 블로그를 운영할 때, 파이프라인 역할별로 각기 다른 블로그에
발행하기 위한 라우팅 모듈.

지원 역할:
    realestate  → 분양정보
    riseset     → 일출일몰
    newspick    → 뉴스픽
    policy      → 정책정보
    reserved    → 예약 슬롯
    backlink    → 백링크 소스
    aliexpress  → 알리익스프레스 상품글
    coupang     → 쿠팡 파트너스 상품글

각 역할은 .env 에서 TISTORY_BLOG_<ROLE> 로 지정한다.
미지정 시 TISTORY_BLOG_NAME 으로 폴백한다 (단일 블로그 운영 시 유용).
"""
import os

from common.logger import log


SUPPORTED_ROLES = (
    "realestate",
    "riseset",
    "newspick",
    "policy",
    "reserved",
    "backlink",
    "aliexpress",
    "coupang",
)


def resolve_blog_name(role: str) -> str:
    """파이프라인 역할에 매핑된 티스토리 블로그 ID 반환.

    조회 우선순위:
        1. TISTORY_BLOG_<ROLE>  (예: TISTORY_BLOG_REALESTATE)
        2. TISTORY_BLOG_NAME  (전역 폴백)
    """
    role_key = role.upper()
    env_name = f"TISTORY_BLOG_{role_key}"

    # python-dotenv 는 따옴표 없는 unquoted 빈 값 뒤의 인라인 `# 코멘트` 를
    # 값의 일부로 파싱한다 (예: `KEY=   # 설명` → `'   # 설명'`).
    # .strip() 만 하면 `# 설명` 이 남아 truthy 가 되고 폴백이 막힌다.
    # 운영 사고 방지를 위해 `#` 으로 시작하면 미설정으로 간주.
    blog = os.getenv(env_name, "").strip()
    if blog.startswith("#"):
        blog = ""
    if blog:
        return blog

    fallback = os.getenv("TISTORY_BLOG_NAME", "").strip()
    if fallback.startswith("#"):
        fallback = ""
    if fallback:
        log(f"[tistory_blogs] role='{role}' 매핑 없음 — TISTORY_BLOG_NAME 폴백", "warn")
        return fallback

    raise ValueError(
        f"티스토리 블로그 ID 를 찾을 수 없습니다. "
        f"환경변수 {env_name} 또는 TISTORY_BLOG_NAME 을 설정하세요."
    )


def list_blogs() -> dict:
    """현재 role → blog_name 매핑을 dict 로 반환 (디버깅용)."""
    return {role: resolve_blog_name(role) for role in SUPPORTED_ROLES}
