"""
RSS 피드 파싱 모듈
- 알라딘, 뉴스 등 RSS 피드 수집
"""
from typing import Optional
import requests
from common.logger import log

try:
    import feedparser
    HAS_FEEDPARSER = True
except ImportError:
    HAS_FEEDPARSER = False


class RssFeedSource:
    """RSS 피드 수집기."""

    def __init__(self, feed_url: str, name: str = "rss"):
        if not HAS_FEEDPARSER:
            raise ImportError("feedparser 패키지 필요: pip install feedparser")
        self.feed_url = feed_url
        self.name     = name

    def fetch(self, count: int = 10) -> list[dict]:
        """RSS 피드에서 최신 아이템 목록을 반환.

        Returns:
            list of {"title": str, "url": str, "summary": str, "image": str}
        """
        log(f"RSS 수집: {self.name} ({self.feed_url})", "step")
        try:
            feed = feedparser.parse(self.feed_url)
            items = []
            for entry in feed.entries[:count]:
                image = ""
                if hasattr(entry, "media_content"):
                    image = entry.media_content[0].get("url", "")
                elif hasattr(entry, "enclosures") and entry.enclosures:
                    image = entry.enclosures[0].get("url", "")

                items.append({
                    "title":   entry.get("title", ""),
                    "url":     entry.get("link", ""),
                    "summary": entry.get("summary", ""),
                    "image":   image,
                })
            log(f"RSS 수집 완료: {len(items)}건", "ok")
            return items
        except Exception as e:
            log(f"RSS 수집 실패: {e}", "error")
            return []


# ─── 알라딘 라이트노벨 피드 (자주 사용) ──────────────────────────────────────

ALADIN_LIGHT_NOVEL_RSS = "https://www.aladin.co.kr/rss/new_book.aspx?CID=70403"
ALADIN_BESTSELLER_RSS  = "https://www.aladin.co.kr/rss/bestseller.aspx"

def aladin_new_books(count: int = 10) -> list[dict]:
    """알라딘 라이트노벨 신간 목록."""
    return RssFeedSource(ALADIN_LIGHT_NOVEL_RSS, "알라딘_라이트노벨").fetch(count)
