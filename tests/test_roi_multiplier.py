"""ROI 가중치 multiplier 테스트.

수익 학습 루프의 핵심: 클릭/매출 키워드에 가산, 0클릭 키워드에 페널티.
"""
from sources.itemscout_keywords import _roi_multiplier, _trend_weight


def test_no_record_returns_neutral():
    """ROI db 에 키워드 없음 → 1.0 (중립)."""
    assert _roi_multiplier("신규키워드", {}) == 1.0


def test_empty_keyword_returns_neutral():
    assert _roi_multiplier("", {"무선이어폰": {"commission": 5000}}) == 1.0


def test_commission_gives_bonus():
    """수수료 발생 → 1.5 이상 보너스."""
    db = {"kw": {"commission": 2000, "clicks": 30, "publishes": 5}}
    m = _roi_multiplier("kw", db)
    assert m > 1.5
    assert m <= 3.0  # 상한


def test_commission_capped_at_3():
    """수수료 매우 큼 → 상한 3.0."""
    db = {"kw": {"commission": 50_000, "clicks": 100, "publishes": 10}}
    assert _roi_multiplier("kw", db) == 3.0


def test_clicks_only_small_bonus():
    """수수료 0, 클릭만 있으면 1.2."""
    db = {"kw": {"commission": 0, "clicks": 15, "publishes": 2}}
    assert _roi_multiplier("kw", db) == 1.2


def test_zero_click_after_3_publishes_penalty():
    """발행 3회 이상, 클릭 0 → 0.5 페널티."""
    db = {"kw": {"commission": 0, "clicks": 0, "publishes": 3}}
    assert _roi_multiplier("kw", db) == 0.5
    db = {"kw": {"commission": 0, "clicks": 0, "publishes": 10}}
    assert _roi_multiplier("kw", db) == 0.5


def test_zero_click_under_3_publishes_neutral():
    """발행 2회 이하 + 클릭 0 → 페널티 없음 (탐색 중)."""
    db = {"kw": {"commission": 0, "clicks": 0, "publishes": 2}}
    assert _roi_multiplier("kw", db) == 1.0


# ── _trend_weight 결합 검증 ────────────────────────────────────────────

def test_trend_weight_up_no_roi():
    item = {"keyword": "x", "rank_change": "up"}
    # ROI 없음 → base 3.0 × 1.0 = 3.0
    assert _trend_weight(item, {}) == 3.0


def test_trend_weight_combines_with_roi():
    item = {"keyword": "히트상품", "rank_change": "up"}
    db = {"히트상품": {"commission": 5000, "clicks": 100, "publishes": 5}}
    # base 3.0 × multiplier ≥ 1.5 → 4.5 이상
    assert _trend_weight(item, db) >= 4.5


def test_trend_weight_penalty_compounded():
    item = {"keyword": "꽝", "rank_change": "down"}
    db = {"꽝": {"commission": 0, "clicks": 0, "publishes": 5}}
    # base 0.7 × 0.5 = 0.35
    assert abs(_trend_weight(item, db) - 0.35) < 0.001


def test_trend_weight_env_off(monkeypatch):
    """KEYWORD_ROI_WEIGHT=false → ROI 무시, base 만."""
    monkeypatch.setenv("KEYWORD_ROI_WEIGHT", "false")
    item = {"keyword": "히트", "rank_change": "up"}
    db = {"히트": {"commission": 9000, "clicks": 200, "publishes": 5}}
    # ROI 무시 → base 3.0
    assert _trend_weight(item, db) == 3.0
