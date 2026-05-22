"""
뉴스픽→단일 Publisher 파이프라인 공통 골격.

구조: login → load_session → fetch_with_links → loop post → notify
각 파이프라인(WP/티스토리)은 NewspickConfig 만 정의하여 run(cfg) 호출.

※ newspick_to_sns 는 멀티 Publisher 라서 이 kernel 을 쓰지 않음.
"""
import os
import random
import time
from dataclasses import dataclass
from typing import Callable

from common.logger import log
from common.notifier import notify_pipeline_result
from common.product_card import (
    GENERIC_DEFAULT_KEYWORDS,
    fetch_recommend_product,
    render_product_card,
)
from sources.newspick import NewspickSource
from sources.gemini_generator import GeminiGenerator

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_HISTORY_PATH = os.path.join(_BASE_DIR, "data", "newspick_published.json")


def _load_history() -> set:
    """이미 발행된 뉴스픽 기사 제목 집합 반환."""
    try:
        import json
        if not os.path.exists(_HISTORY_PATH):
            return set()
        with open(_HISTORY_PATH, "r", encoding="utf-8") as f:
            return set(json.load(f))
    except Exception:
        return set()


def _save_history(titles: set) -> None:
    import json
    os.makedirs(os.path.dirname(_HISTORY_PATH), exist_ok=True)
    tmp = _HISTORY_PATH + ".tmp"
    # 최근 500건만 유지
    items = list(titles)[-500:]
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)
    os.replace(tmp, _HISTORY_PATH)


@dataclass
class NewspickConfig:
    """뉴스픽→Publisher 파이프라인 설정."""
    name: str                                      # 알림용 이름 (예: "뉴스픽→WordPress")
    publisher_factory: Callable[[], object]        # () -> Publisher (login 전)
    post_category_env: str = ""                    # 발행 카테고리 env (선택)
    sleep_range: tuple = (5, 15)                   # 글 간 대기
    coupang_card: bool = True                      # True 면 본문 하단에 쿠팡 추천 상품 카드 삽입
    coupang_channel_env: str = "COUPANG_CHANNEL_ID_NEWSPICK"  # 쿠팡 채널 ID env (없으면 COUPANG_CHANNEL_ID 폴백)


def run(cfg: NewspickConfig, category: str = "추천", count: int = 1,
        use_ai_summary: bool = True) -> None:
    """공통 run() — 뉴스픽 수집 + AI 요약 + Publisher.post() 루프.

    ⚠️ 순서 주의: NewspickSource 와 TistoryPublisher 가 모두 sync_playwright
    를 쓰는데, 같은 스레드에서 두 인스턴스가 동시에 살아있으면
    'Playwright Sync API inside the asyncio loop' 에러로 충돌한다. 따라서
    newspick.ensure_session() (Playwright 켰다 끔) → publisher.login()
    순서로 직렬화해야 한다.
    """
    newspick = NewspickSource(referral_code=os.getenv("NEWSPICK_REFERRAL", ""))
    gemini   = GeminiGenerator() if use_ai_summary else None

    # 1) 뉴스픽 세션 + 기사 수집 (sync_playwright 일회성 사용 후 해제)
    if not newspick.ensure_session():
        log("뉴스픽 세션 없음 — Chrome(Profile 2)에서 partners.newspic.kr 로그인 필요", "error")
        notify_pipeline_result(cfg.name, 0, count, details="뉴스픽 세션 없음")
        return

    # fetch 가 추천+일반 두 소스에서 가져오므로 최대 2*count 반환 → 명시적 절단
    articles = newspick.fetch_with_links(category=category, count=count)[:count]
    if not articles:
        log("수집된 아티클 없음", "warn")
        notify_pipeline_result(cfg.name, 0, count, details="수집 실패")
        return

    # 1.5) 쿠팡 추천 상품 미리 수집 (sync_playwright 충돌 방지를 위해 publisher 로그인 전)
    products: list = []
    if cfg.coupang_card:
        channel_id = (
            os.getenv(cfg.coupang_channel_env, "")
            or os.getenv("COUPANG_CHANNEL_ID", "")
        )
        for _ in articles:
            try:
                products.append(fetch_recommend_product(
                    GENERIC_DEFAULT_KEYWORDS, channel_id=channel_id,
                ))
            except Exception as e:
                log(f"쿠팡 상품 수집 예외: {e}", "warn")
                products.append(None)
    else:
        products = [None] * len(articles)

    # 2) Publisher 로그인 (이 시점에 sync_playwright 새로 켜짐)
    publisher = cfg.publisher_factory()
    if not publisher.login():
        log(f"{cfg.name} Publisher 로그인 실패", "error")
        notify_pipeline_result(cfg.name, 0, count, details="Publisher 로그인 실패")
        _maybe_close(publisher)
        return

    post_category = os.getenv(cfg.post_category_env, "") if cfg.post_category_env else ""

    published_history = _load_history()

    published = 0
    skipped_dup = 0
    last_url = ""
    try:
        for article, product in zip(articles, products):
            title   = article["title"]

            # 이미 발행된 기사 건너뜀 (NinjaFirewall 중복 차단 방지)
            if title in published_history:
                log(f"[뉴스픽] 중복 기사 건너뜀: {title}", "info")
                skipped_dup += 1
                continue

            # B2 — 인사말 + 마무리 다양화 (봇 패턴 탐지 회피)
            intros = [
                "오늘 가장 주목받는 뉴스를 한 줄로 정리해 드립니다.",
                "오늘 화제가 된 이야기를 빠르게 살펴봅니다.",
                "지금 가장 많이 이야기되고 있는 주제입니다.",
                "방금 들어온 따끈한 소식을 전해드립니다.",
                "이슈가 되고 있는 이야기를 간단히 짚어봅니다.",
                "관심을 끌고 있는 뉴스를 정리해 봤습니다.",
                "오늘의 화제 키워드를 함께 알아봅니다.",
                "지금 SNS에서 화제가 된 이슈입니다.",
            ]
            outros = [
                "📌 자세한 내용은 본문 링크에서 확인해 보세요.",
                "📰 더 많은 이야기는 원문에서 만나보실 수 있습니다.",
                "🔍 관심 있으신 분은 원문 기사를 참고하세요.",
                "💬 어떻게 생각하시나요? 댓글로 의견을 남겨주세요.",
                "✨ 오늘 하루도 좋은 정보가 되었길 바랍니다.",
                "👀 추가 소식이 들어오는 대로 업데이트하겠습니다.",
            ]
            intro = random.choice(intros)
            outro = random.choice(outros)

            content = (
                f'<p>{intro}</p>\n'
                f'<p><a href="{article["short_url"]}" target="_blank">{title}</a></p>'
            )
            if gemini and article.get("summary"):
                summary = gemini.summarize(article["summary"])
                content += f"\n<p>{summary}</p>"
            content += f"\n<p>{outro}</p>"

            # 본문 하단에 쿠팡 추천 상품 카드 + 파트너스 고지 (난독화 모드 default)
            if product:
                content += render_product_card(product)

            # AI 관련 태그 3개 + 정적 태그 2개 = 총 5개
            from common.ai_intro import generate_related_tags
            ai_tags = generate_related_tags(
                title, context=f"{category} 카테고리", n=3,
                exclude=[category, "뉴스픽"],
            )
            tags = [category, "뉴스픽"] + ai_tags

            result = publisher.post(
                title=title,
                content=content,
                tags=tags,
                image_url=article.get("image", ""),
                category=post_category,
            )
            if result.success:
                published += 1
                log(f"[{published}/{count}] 발행 완료: {result.url}", "ok")
                published_history.add(title)
                _save_history(published_history)
                if result.url:
                    last_url = result.url
                    from common.publish_queue import add_url as _add_url
                    _plat = "tistory" if "tistory" in result.url else "wordpress"
                    _add_url(result.url, platform=_plat, title=title)

            time.sleep(random.uniform(*cfg.sleep_range))
    finally:
        _maybe_close(publisher)

    # bridge 모드 = 큐 등록만 한 것. 실제 발행 완료 텔레그램 알림은 bridge
    # server 가 /done 받을 때 보낸다 — 파이프라인 단계 알림은 skip (false
    # positive 방지).
    # cfg.name 은 한글("뉴스픽→티스토리") 또는 영문("→tistory") 둘 다 허용
    is_bridge = ("tistory" in cfg.name.lower() or "티스토리" in cfg.name) and \
        os.getenv("TISTORY_PUBLISHER", "web").strip().lower() == "bridge"
    verb = "큐 등록" if is_bridge else "발행"
    log(f"{cfg.name} 완료: {published}/{count}건 {verb}", "step")
    if is_bridge and published > 0:
        log(f"{cfg.name} bridge 모드 — 파이프라인 알림 skip (실제 발행 완료 시 bridge 가 알림)", "info")
        return
    if published == 0 and skipped_dup == len(articles) and skipped_dup > 0:
        notify_pipeline_result(cfg.name, 0, count,
                               details=f"새 기사 없음 ({skipped_dup}건 모두 중복)",
                               reason="empty")
    else:
        notify_pipeline_result(cfg.name, published, count, url=last_url)


def _maybe_close(publisher) -> None:
    """Publisher 에 close 가 있으면 호출 (Playwright context 정리용)."""
    close = getattr(publisher, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            pass
