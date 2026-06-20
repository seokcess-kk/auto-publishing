"""
뉴스픽→단일 Publisher 파이프라인 공통 골격.

구조: login → load_session → fetch_with_links → loop post → notify
각 파이프라인(WP/티스토리)은 NewspickConfig 만 정의하여 run(cfg) 호출.

※ newspick_to_sns 는 멀티 Publisher 라서 이 kernel 을 쓰지 않음.
"""
import os
import random
import re
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Callable

from common.logger import log
from common.notifier import notify_pipeline_result
from common.product_card import (
    fetch_recommend_product,
    keywords_for_cate_code,
    render_product_card,
)
from sources.newspick import NewspickSource

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


# ─── P2 화제성 선별 ──────────────────────────────────────────────────────────
# 뉴스픽 API 의 isHot/impRank/star 는 이 피드에서 비어있어 못 쓴다. 실제 쓸 수
# 있는 신호는 피드 순서(뉴스픽 자체 랭킹) + pubDate(신선도) + 제목 그 자체.
# 따라서 후보 풀에서 ①중복/저관심/부적합 제거 후 ②제목 후킹 점수로 정렬해 고른다.

# 행정 공지·시험 안내·부고·사건사고 — 블로그 가치 낮거나 민감(상품 카드도 부적절).
_LOW_INTEREST_RE = re.compile(
    r"(가답안|정답\s*공개|채점|시험\s*일정|합격자\s*발표|모집\s*공고|채용\s*공고|"
    r"부고|별세|숙환|숨진|숨졌|사망|숨\s*거둬|투신|자살|피살|흉기|부음)"
)

# 호기심/후킹 신호어 — 제목에 있으면 화제성 가점.
_HOOK_WORDS = (
    "충격", "논란", "발칵", "깜짝", "소름", "반전", "경악", "결국", "왜", "이유",
    "정체", "알고보니", "사실", "공개", "포착", "헉", "무슨일", "깜놀", "화제",
)


def _is_low_interest(title: str) -> bool:
    return bool(_LOW_INTEREST_RE.search(title or ""))


def _score_article(a: dict, order_idx: int, today: str) -> float:
    """화제성 점수 — 높을수록 우선 발행."""
    title = a.get("title", "") or ""
    score = max(0.0, 20.0 - order_idx)          # 뉴스픽 피드 랭킹(앞쪽=상위) 신뢰
    if (a.get("pubDate", "") or "").startswith(today):
        score += 4.0                            # 오늘자 신선도
    if any(q in title for q in ('"', "'", "…", "?", "“", "”")):
        score += 4.0                            # 인용/말줄임/물음표 후킹
    if re.search(r"\d", title):
        score += 2.0                            # 구체적 숫자
    if any(w in title for w in _HOOK_WORDS):
        score += 4.0                            # 호기심 단어
    if keywords_for_cate_code(a.get("cate_code", "")):
        score += 2.0                            # 상품 매칭 가능(수익화) 소폭 가점
    return score


def _select_topical(pool: list, count: int, history: set) -> list:
    """후보 풀에서 중복·저관심·부적합 제거 후 화제성 상위 count 선택.

    기존엔 top 글만 취해 그게 이미 발행된 중복이면 0건 발행되는 사각지대가 있었다.
    풀 전체에서 신규·화제성 높은 글을 고르므로 그 문제가 사라진다.
    """
    today = datetime.now().strftime("%Y.%m.%d")   # pubDate 포맷 "2026.06.20."
    cand = []
    for idx, a in enumerate(pool):
        title = a.get("title", "") or ""
        if not title or title in history:
            continue                              # 빈 제목 / 이미 발행
        if a.get("isVideo"):
            continue                              # 영상 → 텍스트 블로그 부적합
        if _is_low_interest(title):
            continue                              # 행정공지/부고/사건
        cand.append((idx, a))
    cand.sort(key=lambda x: _score_article(x[1], x[0], today), reverse=True)
    selected = [a for _, a in cand[:count]]
    if selected:
        log(f"[뉴스픽] 화제성 선별: 후보 {len(pool)} → 신규 {len(cand)} → 채택 "
            f"{len(selected)} (top: {selected[0].get('title', '')[:34]})", "info")
    return selected


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

    # 1) 뉴스픽 세션 + 기사 수집 (sync_playwright 일회성 사용 후 해제)
    if not newspick.ensure_session():
        # 복구 안내(throttled 텔레그램)는 ensure_session 내부에서 정확한 명령으로
        # 발송된다(python tools/newspick_manual_login.py). 여기선 ledger/요약용 로그만.
        log("뉴스픽 세션 없음 — 수동 로그인 필요 (python tools/newspick_manual_login.py)", "error")
        notify_pipeline_result(cfg.name, 0, count, details="뉴스픽 세션 없음")
        return

    # P2 화제성 선별 — 후보 풀(count*8, 최소 15)에서 중복·저관심·부적합 제거 후
    # 화제성 점수 상위 count 선택. (기존엔 top 글만 취해 그게 이미 발행한 중복이면
    # 0건 발행되는 사각지대가 있었다. 이제 풀에서 신규·화제성 높은 글을 고른다.)
    published_history = _load_history()
    pool = newspick.fetch(category, count=max(count * 8, 15))
    if not pool:
        log("수집된 아티클 없음", "warn")
        notify_pipeline_result(cfg.name, 0, count, details="수집 실패")
        return
    articles = _select_topical(pool, count, published_history)
    if not articles:
        log("선별된 신규 아티클 없음 (모두 중복/저관심/부적합)", "warn")
        notify_pipeline_result(cfg.name, 0, count,
                               details="신규 기사 없음", reason="empty")
        return
    # 선택된 글에만 단축 링크 생성 — 풀 전체에 링크 만드는 낭비 회피
    for a in articles:
        a["short_url"] = newspick.shorten_link(a, category)

    # 1.5) 쿠팡 추천 상품 미리 수집 (sync_playwright 충돌 방지를 위해 publisher 로그인 전)
    #   ⚠️ 글마다 cate_code(CAxxyy)로 '본문 맥락에 맞는' 상품 키워드를 골라 검색한다.
    #   하드뉴스(시사/증시)는 자연스러운 상품이 없으므로 keywords_for_cate_code 가
    #   None 을 주고 → 카드 자체를 생략한다. (한동훈 대선 글에 캠핑 랜턴 붙던 문제 해결)
    products: list = []
    if cfg.coupang_card:
        channel_id = (
            os.getenv(cfg.coupang_channel_env, "")
            or os.getenv("COUPANG_CHANNEL_ID", "")
        )
        for article in articles:
            code = article.get("cate_code", "")
            kws = keywords_for_cate_code(code)
            if not kws:
                log(f"[뉴스픽] 카테고리 {code or '?'} 상품 부적합 — 카드 생략", "info")
                products.append(None)
                continue
            try:
                products.append(fetch_recommend_product(kws, channel_id=channel_id))
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

    # published_history 는 위(선별 단계)에서 이미 로드됨 — 발행 성공 시 갱신만 한다.
    published = 0
    skipped_dup = 0
    last_url = ""
    try:
        for article, product in zip(articles, products):
            raw_title = article["title"]

            # 중복 판정은 원문 제목 기준 (재작성 제목은 매번 달라짐)
            if raw_title in published_history:
                log(f"[뉴스픽] 중복 기사 건너뜀: {raw_title}", "info")
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

            # 발행 제목 — 원문 헤드라인은 중복 클러스터에 묻히므로 AI 후킹 재작성
            display_title = raw_title
            if use_ai_summary:
                try:
                    from common.ai_intro import generate_newspick_title
                    display_title = (generate_newspick_title(raw_title, category)
                                     or raw_title)
                except Exception as e:
                    log(f"[뉴스픽] 제목 재작성 예외: {e}", "warn")

            link_url = article.get("short_url") or article.get("url", "")

            # 본문 — thin content 탈출용 AI 본문(<h2>+<p>) 생성, 실패 시 단순 구조
            ai_body = ""
            if use_ai_summary:
                try:
                    from common.ai_intro import generate_newspick_article
                    ai_body = generate_newspick_article(raw_title, category)
                except Exception as e:
                    log(f"[뉴스픽] 본문 생성 예외: {e}", "warn")

            parts = [f"<p>{intro}</p>"]
            if ai_body:
                parts.append(ai_body)
            # 원문 전체 기사 클릭 CTA (조회수→뉴스픽 클릭 수익 유지)
            if link_url:
                parts.append(
                    f'<p style="margin:18px 0;padding:14px 16px;background:#f6f8ff;'
                    f'border-left:4px solid #3b5bdb;border-radius:6px;font-weight:600;">'
                    f'👉 <a href="{link_url}" target="_blank" rel="nofollow">'
                    f'{raw_title} — 전체 기사 보기</a></p>'
                )
            parts.append(f"<p>{outro}</p>")
            content = "\n".join(parts)

            # 본문 하단에 쿠팡 추천 상품 카드 + 파트너스 고지 (난독화 모드 default)
            if product:
                content += render_product_card(product)

            # 정적 2 + AI 관련 3 + 네이버 연관검색어(트렌드) 최대 3 = 검색 태그 강화
            from common.ai_intro import generate_related_tags
            ai_tags = generate_related_tags(
                raw_title, context=f"{category} 카테고리", n=3,
                exclude=[category, "뉴스픽"],
            )
            tags = [category, "뉴스픽"] + ai_tags
            # 실시간 연관검색어 태그 — 죽어있던 tag_generator 활용 (실패 무해)
            try:
                from common import tag_generator
                seed = ai_tags[0] if ai_tags else category
                related = tag_generator.tags_to_plain(
                    tag_generator.filter_forbidden(
                        tag_generator.from_naver_related(seed, limit=3)
                    )
                )
                for t in related:
                    if t and t not in tags and len(tags) < 8:
                        tags.append(t)
            except Exception as e:
                log(f"[뉴스픽] 연관검색어 태그 예외: {e}", "warn")

            result = publisher.post(
                title=display_title,
                content=content,
                tags=tags,
                image_url=article.get("image", ""),
                category=post_category,
            )
            if result.success:
                published += 1
                log(f"[{published}/{count}] 발행 완료: {result.url}", "ok")
                published_history.add(raw_title)
                _save_history(published_history)
                if result.url:
                    last_url = result.url
                    from common.publish_queue import add_url as _add_url
                    _plat = "tistory" if "tistory" in result.url else "wordpress"
                    _add_url(result.url, platform=_plat, title=display_title)

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
