"""
백링크 발행 상태 저장소 (중복 방지 + 플랫폼별 발행 이력 추적)

저장 위치: data/backlink_state.json
형식:
{
  "<url>": {
      "added_at": "2026-04-20T10:00:00",
      "title": "...",
      "site": "https://a.com",
      "source": "sitemap|rest",
      "platforms": {
          "twitter": {"posted_at": "...", "post_url": "...", "status": "ok|fail"},
          "threads": {...}
      }
  },
  ...
}

설계 이유 — CSV(ver1) 대신 JSON 을 쓰는 이유:
  - 여러 플랫폼(X/Threads/Tistory) 발행 상태를 독립적으로 추적해야 함
  - 파이프라인 상태와 일관된 위치(data/) 에 저장
"""
import json
import os
from datetime import datetime
from typing import Optional

from common.logger import log


DEFAULT_STATE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(__file__)),
    "data",
    "backlink_state.json",
)


class BacklinkState:
    """백링크 발행 이력 JSON 저장소."""

    def __init__(self, state_path: str = DEFAULT_STATE_PATH):
        self.state_path = state_path
        os.makedirs(os.path.dirname(self.state_path), exist_ok=True)
        self._data = self._load()

    def _load(self) -> dict:
        if not os.path.exists(self.state_path):
            return {}
        try:
            with open(self.state_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            log(f"백링크 상태 로드 실패: {e} — 새로 시작", "warn")
            return {}

    def save(self) -> None:
        tmp = self.state_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self._data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.state_path)

    # ─── URL 풀 관리 ──────────────────────────────────────────────────────

    def add_urls(self, records: list) -> int:
        """신규 URL 기록. 이미 존재하면 건너뜀. 추가된 건수 반환."""
        added = 0
        now = datetime.now().isoformat(timespec="seconds")
        for rec in records:
            url = rec.get("url", "").strip()
            if not url or url in self._data:
                continue
            self._data[url] = {
                "added_at": now,
                "title": rec.get("title", ""),
                "site": rec.get("site", ""),
                "source": rec.get("source", ""),
                "platforms": {},
            }
            added += 1
        if added:
            self.save()
        return added

    def pending_urls(self, platform: str, limit: Optional[int] = None) -> list:
        """해당 플랫폼에 아직 발행되지 않은 URL 목록."""
        pending = []
        for url, info in self._data.items():
            plat_info = info.get("platforms", {}).get(platform, {})
            if plat_info.get("status") == "ok":
                continue
            pending.append({"url": url, **info})
            if limit and len(pending) >= limit:
                break
        return pending

    # ─── 발행 결과 기록 ──────────────────────────────────────────────────

    def mark_posted(self, url: str, platform: str,
                    post_url: str = "", status: str = "ok") -> None:
        """플랫폼 발행 결과 기록."""
        if url not in self._data:
            return
        self._data[url].setdefault("platforms", {})[platform] = {
            "posted_at": datetime.now().isoformat(timespec="seconds"),
            "post_url": post_url,
            "status": status,
        }
        self.save()

    # ─── 통계 ────────────────────────────────────────────────────────────

    def stats(self) -> dict:
        total = len(self._data)
        per_platform = {}
        for info in self._data.values():
            for plat, plat_info in info.get("platforms", {}).items():
                per_platform.setdefault(plat, {"ok": 0, "fail": 0})
                if plat_info.get("status") == "ok":
                    per_platform[plat]["ok"] += 1
                else:
                    per_platform[plat]["fail"] += 1
        return {"total_urls": total, "per_platform": per_platform}
