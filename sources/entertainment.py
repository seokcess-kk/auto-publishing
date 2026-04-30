"""
엔터/트렌드 정보 수집 모듈 (API 키 불필요)
- 구글 트렌드: 일간 급상승 검색어 (RSS)
- 멜론 차트: 음악 TOP 순위
- 네이버 웹툰: 요일별 인기 웹툰 (공개 API)
- 알라딘: 베스트셀러 도서
- 네이버 웹소설/시리즈 등 확장 가능
"""
import re
from datetime import datetime
from typing import Optional

import requests
from bs4 import BeautifulSoup

from common.logger import log

try:
    import feedparser
    HAS_FEEDPARSER = True
except ImportError:
    HAS_FEEDPARSER = False

# ─── 설정 ────────��─────────────────────────────────��─────────────────────────

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ko-KR,ko;q=0.9",
}

REQUEST_TIMEOUT = 15

WEEKDAY_MAP = {
    0: "mon", 1: "tue", 2: "wed", 3: "thu",
    4: "fri", 5: "sat", 6: "sun",
}


# ─── 구글 트렌드 ──────────────────────────────────────��──────────────────────

class GoogleTrendsCrawler:
    """구글 트렌드 일간 급상승 검색어 수집 (RSS, 키 불필요)."""

    RSS_URL = "https://trends.google.co.kr/trending/rss?geo=KR"

    def fetch(self, count: int = 20) -> list[dict]:
        """일간 급상승 검색어를 수집.

        Returns:
            list of {rank, keyword, traffic, url, crawled_at}
        """
        if not HAS_FEEDPARSER:
            log("feedparser 미설치 (pip install feedparser)", "error")
            return []

        log("구글 트렌드 급상승 검색어 수집", "step")

        try:
            feed = feedparser.parse(self.RSS_URL)
        except Exception as e:
            log(f"구글 트렌드 수집 실패: {e}", "error")
            return []

        items = []
        for i, entry in enumerate(feed.entries[:count], 1):
            traffic = entry.get("ht_approx_traffic", "")
            items.append({
                "rank": i,
                "keyword": entry.get("title", ""),
                "traffic": traffic,
                "url": entry.get("link", ""),
                "crawled_at": datetime.now().isoformat(),
            })

        log(f"구글 트렌드: {len(items)}건 수집", "ok")
        return items


# ─── 멜론 차트 ────────────���─────────────────────────��────────────────────────

class MelonChartCrawler:
    """멜론 실시간/일간 차트 크롤링."""

    CHART_URL = "https://www.melon.com/chart/index.htm"

    def fetch(self, count: int = 20) -> list[dict]:
        """멜론 TOP 차트 수집.

        Returns:
            list of {rank, title, artist, crawled_at}
        """
        log(f"멜론 차트 수집 (상위 {count}곡)", "step")

        try:
            resp = requests.get(
                self.CHART_URL,
                headers={**HEADERS, "Referer": "https://www.melon.com"},
                timeout=REQUEST_TIMEOUT,
            )
            resp.raise_for_status()
        except Exception as e:
            log(f"멜론 차트 요청 실��: {e}", "error")
            return []

        soup = BeautifulSoup(resp.text, "lxml")
        rows = soup.select(".lst50, .lst100")

        items = []
        for i, row in enumerate(rows[:count], 1):
            title_el = row.select_one(".ellipsis.rank01 a")
            artist_el = row.select_one(".ellipsis.rank02 a")

            if not title_el:
                continue

            items.append({
                "rank": i,
                "title": title_el.get_text(strip=True),
                "artist": artist_el.get_text(strip=True) if artist_el else "",
                "crawled_at": datetime.now().isoformat(),
            })

        log(f"멜론 차트: {len(items)}�� 수집", "ok")
        return items


# ─── 네이버 웹툰 ────────────────────────────────────────────────────────────

class NaverWebtoonCrawler:
    """네이버 웹툰 인기 순위 수집 (공개 API, 키 불필요)."""

    API_URL = "https://comic.naver.com/api/webtoon/titlelist/weekday"

    def fetch(self, weekday: Optional[str] = None, count: int = 15) -> list[dict]:
        """요일별 인기 웹툰 목록 수집.

        Args:
            weekday: 요일 (mon~sun). None이면 오늘.
            count: 수집 건수

        Returns:
            list of {rank, title, author, url, rating, crawled_at}
        """
        if weekday is None:
            weekday = WEEKDAY_MAP[datetime.now().weekday()]

        log(f"네이버 웹툰 수집: {weekday} 인기순", "step")

        try:
            resp = requests.get(
                self.API_URL,
                params={"order": "star", "week": weekday},
                headers=HEADERS,
                timeout=REQUEST_TIMEOUT,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            log(f"네이버 웹툰 요청 실패: {e}", "error")
            return []

        title_list = data.get("titleList", [])

        items = []
        for i, t in enumerate(title_list[:count], 1):
            title_id = t.get("titleId", "")
            items.append({
                "rank": i,
                "title": t.get("titleName", ""),
                "author": t.get("author", ""),
                "url": f"https://comic.naver.com/webtoon/list?titleId={title_id}" if title_id else "",
                "thumbnail": t.get("thumbnailUrl", ""),
                "crawled_at": datetime.now().isoformat(),
            })

        log(f"네이버 웹툰: {len(items)}건 수집", "ok")
        return items


# ─── 알라딘 베스트셀러 ────────────────��──────────────────────────────────────

class AladinCrawler:
    """알라딘 베스트셀러 크롤링."""

    BESTSELLER_URL = "https://www.aladin.co.kr/shop/common/wbest.aspx"

    CATEGORIES = {
        "종합": "0",
        "소설": "1",
        "경제경영": "170",
        "자기계발": "336",
        "에세이": "55889",
        "인문": "656",
        "IT": "351",
    }

    def fetch(self, category: str = "종합", count: int = 15) -> list[dict]:
        """베스트셀러 목록 수집.

        Args:
            category: 카테고리명 (종합, 소설, 경제경영 등)
            count: 수��� 건수

        Returns:
            list of {rank, title, author, publisher, url, crawled_at}
        """
        cat_id = self.CATEGORIES.get(category, "0")
        log(f"알라딘 베스트셀러 수집: {category}", "step")

        try:
            resp = requests.get(
                self.BESTSELLER_URL,
                params={"BestType": "Bestseller", "BranchType": "1", "CID": cat_id},
                headers=HEADERS,
                timeout=REQUEST_TIMEOUT,
            )
            resp.raise_for_status()
        except Exception as e:
            log(f"알라딘 요��� 실패: {e}", "error")
            return []

        soup = BeautifulSoup(resp.text, "lxml")
        # itemid 속성이 있는 실제 도서만 필터링 (이벤트 배너 제외)
        book_boxes = [
            box for box in soup.select(".ss_book_box")
            if box.get("itemid")
        ]

        items = []
        for i, box in enumerate(book_boxes[:count], 1):
            title_el = box.select_one("a.bo3")
            if not title_el:
                continue

            title = title_el.get_text(strip=True)
            href = title_el.get("href", "")

            # 저자/출판사: "(지은이)" 패턴이 있는 li에서 추출
            author = ""
            publisher = ""
            for li in box.select(".ss_book_list li"):
                li_text = li.get_text(strip=True)
                if "(지은이)" in li_text or "(옮긴이)" in li_text or "(엮은이)" in li_text:
                    # 저자: (지은이) 앞의 a 태그들
                    author_parts = []
                    for a_tag in li.select("a"):
                        a_text = a_tag.get_text(strip=True)
                        # 출판사는 | 뒤에 오므로 구분
                        if "|" in li_text:
                            idx = li_text.index("|")
                            if li_text.index(a_text) < idx:
                                author_parts.append(a_text)
                            else:
                                if not publisher:
                                    publisher = a_text
                        else:
                            author_parts.append(a_text)
                    author = ", ".join(author_parts)
                    break

            items.append({
                "rank": i,
                "title": title,
                "author": author,
                "publisher": publisher,
                "url": href,
                "category": category,
                "crawled_at": datetime.now().isoformat(),
            })

        log(f"알��딘 {category}: {len(items)}건 수집", "ok")
        return items


# ─��─ 통합 소스 ─────────────���─────────────────────────────────────────────────

class EntertainmentSource:
    """엔��/트렌드 정보 통합 수집 클래스.

    API 키 없이 HTTP 요청만으로 동작합니다.

    Usage:
        source = EntertainmentSource()
        data = source.fetch_all()
        print(source.format_summary(data))
    """

    def __init__(self):
        self.trends = GoogleTrendsCrawler()
        self.melon = MelonChartCrawler()
        self.webtoon = NaverWebtoonCrawler()
        self.aladin = AladinCrawler()

    def fetch_all(self, count: int = 10) -> dict:
        """전체 엔터/트렌드 데이터 일괄 수집.

        Returns:
            {trends: [...], melon: [...], webtoon: [...],
             books: [...], crawled_at: str}
        """
        log("엔��/트렌드 통합 수집 시작", "step")

        result = {
            "trends": self.trends.fetch(count=count),
            "melon": self.melon.fetch(count=count),
            "webtoon": self.webtoon.fetch(count=count),
            "books": self.aladin.fetch(count=count),
            "crawled_at": datetime.now().isoformat(),
        }

        log("엔터/트렌드 ���합 수집 완료", "ok")
        return result

    def format_summary(self, data: dict) -> str:
        """엔터/트렌드 데이터를 읽기 좋은 텍스트로 변환."""
        lines = [f"🎬 엔터/트렌드 ({datetime.now().strftime('%Y-%m-%d %H:%M')})"]

        # 구글 트렌드
        if data.get("trends"):
            lines.append("\n🔥 급상승 검색어 (구글 트렌드)")
            for item in data["trends"][:10]:
                traffic = f" ({item['traffic']})" if item["traffic"] else ""
                lines.append(f"  {item['rank']:2d}. {item['keyword']}{traffic}")

        # 멜론 차트
        if data.get("melon"):
            lines.append("\n���� 멜론 차트")
            for item in data["melon"][:10]:
                lines.append(f"  {item['rank']:2d}. {item['title']} — {item['artist']}")

        # 웹툰
        if data.get("webtoon"):
            lines.append("\n📖 네이버 웹툰 인기")
            for item in data["webtoon"][:10]:
                lines.append(f"  {item['rank']:2d}. {item['title']}")

        # 베스트셀러
        if data.get("books"):
            lines.append("\n📚 알라딘 베스트셀러")
            for item in data["books"][:10]:
                author = f" — {item['author']}" if item["author"] else ""
                lines.append(f"  {item['rank']:2d}. {item['title']}{author}")

        return "\n".join(lines)

    def format_blog_content(self, data: dict) -> str:
        """블로그 발행용 HTML 콘텐츠 생성."""
        today = datetime.now().strftime("%Y년 %m월 %d일")
        parts = [f"<h2>오늘의 엔���/트렌드 ({today})</h2>"]

        # 급상승 검색어
        if data.get("trends"):
            parts.append("<h3>급상승 검색어</h3><ol>")
            for item in data["trends"][:10]:
                traffic = f" <small>({item['traffic']})</small>" if item["traffic"] else ""
                parts.append(f"<li><strong>{item['keyword']}</strong>{traffic}</li>")
            parts.append("</ol>")

        # 멜론 차트
        if data.get("melon"):
            parts.append("<h3>멜론 차트 TOP 10</h3>")
            parts.append(
                "<table style='border-collapse:collapse; width:100%; max-width:500px;'>"
                "<thead><tr>"
                "<th style='padding:6px; border:1px solid #ddd;'>순위</th>"
                "<th style='padding:6px; border:1px solid #ddd;'>곡명</th>"
                "<th style='padding:6px; border:1px solid #ddd;'>아티스트</th>"
                "</tr></thead><tbody>"
            )
            for item in data["melon"][:10]:
                parts.append(
                    f"<tr>"
                    f"<td style='padding:6px; border:1px solid #ddd; text-align:center;'>{item['rank']}</td>"
                    f"<td style='padding:6px; border:1px solid #ddd;'>{item['title']}</td>"
                    f"<td style='padding:6px; border:1px solid #ddd;'>{item['artist']}</td>"
                    f"</tr>"
                )
            parts.append("</tbody></table>")

        # 베스트셀러
        if data.get("books"):
            parts.append("<h3>베스트셀러</h3><ol>")
            for item in data["books"][:10]:
                author = f" <small>— {item['author']}</small>" if item["author"] else ""
                parts.append(f"<li>{item['title']}{author}</li>")
            parts.append("</ol>")

        return "\n".join(parts)
