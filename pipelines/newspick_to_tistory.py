"""
파이프라인: 뉴스픽 → 티스토리

공통 커널(pipelines._kernel.newspick)을 사용한 얇은 래퍼.

실행:
    python -m pipelines.newspick_to_tistory
"""
import json
import os
import time
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

from common.logger import log
from common.tistory_blogs import resolve_blog_name
from pipelines._kernel.newspick import NewspickConfig
from pipelines._kernel.newspick import run as _kernel_run
from publishers.tistory import TistoryPublisher

# 캡차 회피용 cooldown — 마지막 발행 시도 시각 기록
_COOLDOWN_PATH = Path(__file__).resolve().parent.parent / "data" / "newspick_tistory_cooldown.json"


def _load_cooldown() -> dict:
    if not _COOLDOWN_PATH.exists():
        return {}
    try:
        return json.loads(_COOLDOWN_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_cooldown(d: dict) -> None:
    _COOLDOWN_PATH.parent.mkdir(parents=True, exist_ok=True)
    _COOLDOWN_PATH.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")


SCHEDULE = {
    "env":  "SCHEDULE_NEWSPICK_TISTORY",
    "func": "run",
    "args_from_env": ("NEWSPICK_CATEGORY:추천", "POST_COUNT:1:int"),
}


def _tistory_factory():
    blog_name = resolve_blog_name("newspick")
    return TistoryPublisher(blog_name)


_CFG = NewspickConfig(
    name="뉴스픽→티스토리",
    publisher_factory=_tistory_factory,
    post_category_env="TISTORY_CATEGORY",
    sleep_range=(10, 20),
)


def run(category: str = "추천", count: int = 1,
        use_ai_summary: bool = True,
        blog_name: str = "") -> None:
    """뉴스픽 수집 → 티스토리 발행.

    blog_name 은 kept-for-backward-compat — 지정 시 해당 블로그로 override.

    B1 — 캡차 회피용 cooldown:
      NEWSPICK_TISTORY_COOLDOWN_DAYS (기본 0=비활성, 캡차 트리거된 블로그는 3 권장)
      마지막 발행 시도 후 N일 이내 호출 시 skip.
    """
    # B1 — 대상 블로그 cooldown 검사
    target_blog = blog_name or resolve_blog_name("newspick")
    try:
        cooldown_days = int(os.getenv("NEWSPICK_TISTORY_COOLDOWN_DAYS", "0"))
    except ValueError:
        cooldown_days = 0
    if cooldown_days > 0:
        cd = _load_cooldown()
        last_ts = cd.get(target_blog, 0)
        elapsed = time.time() - last_ts
        if elapsed < cooldown_days * 86400:
            remaining = (cooldown_days * 86400 - elapsed) / 3600
            log(f"[뉴스픽→티스토리:{target_blog}] cooldown 중 — 잔여 {remaining:.1f}h, skip", "warn")
            return
        # cooldown 통과 — 시각 갱신 (이번 시도가 성공이든 실패든 cooldown 시작)
        cd[target_blog] = int(time.time())
        _save_cooldown(cd)

    if blog_name:
        # blog_name 명시 시 factory 를 덮어쓴 임시 Config 사용
        cfg = NewspickConfig(
            name=_CFG.name,
            publisher_factory=lambda: TistoryPublisher(blog_name),
            post_category_env=_CFG.post_category_env,
            sleep_range=_CFG.sleep_range,
        )
        _kernel_run(cfg, category=category, count=count, use_ai_summary=use_ai_summary)
    else:
        _kernel_run(_CFG, category=category, count=count, use_ai_summary=use_ai_summary)


if __name__ == "__main__":
    run(
        category=os.getenv("NEWSPICK_CATEGORY", "추천"),
        count=int(os.getenv("POST_COUNT", "1")),
    )
