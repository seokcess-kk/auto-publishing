"""
지식/교육 정보 수집 모듈 (API 키 불필요)
- 오늘의 명언: ZenQuotes API (무료, 키 불필요)
- 오늘의 영어 단어: Wordnik API (무료 공개키)
- 오늘의 역사: 위키백과 크롤링
- IT/개발 뉴스: GeekNews, 요즘IT, Hacker News
- GitHub 트렌딩: 일간 인기 저장소
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

# ─── 설정 ────────────────────────────────────────────────────────────────────

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ko-KR,ko;q=0.9",
}

REQUEST_TIMEOUT = 15


# ─── 오늘의 명언 ─────────────────────────────────────────────────────────────

class QuoteCrawler:
    """ZenQuotes API로 명언 수집 (무료, 키 불필요)."""

    TODAY_URL = "https://zenquotes.io/api/today"
    RANDOM_URL = "https://zenquotes.io/api/random"

    def fetch_today(self) -> dict:
        """오늘의 명언 1개."""
        log("오늘의 명언 수집", "step")
        try:
            resp = requests.get(self.TODAY_URL, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            data = resp.json()
            if data:
                quote = data[0]
                result = {
                    "quote": quote.get("q", ""),
                    "author": quote.get("a", ""),
                    "crawled_at": datetime.now().isoformat(),
                }
                log(f"명언: \"{result['quote'][:40]}...\" — {result['author']}", "ok")
                return result
        except Exception as e:
            log(f"명언 수집 실패: {e}", "error")
        return {"quote": "", "author": "", "error": "수집 실패"}

    def fetch_random(self, count: int = 5) -> list[dict]:
        """랜덤 명언 여러개 수집."""
        log(f"랜덤 명언 {count}개 수집", "step")
        items = []
        try:
            for _ in range(count):
                resp = requests.get(self.RANDOM_URL, timeout=REQUEST_TIMEOUT)
                if resp.status_code == 200:
                    data = resp.json()
                    if data:
                        items.append({
                            "quote": data[0].get("q", ""),
                            "author": data[0].get("a", ""),
                        })
        except Exception as e:
            log(f"랜덤 명언 수집 실패: {e}", "error")

        log(f"명언 {len(items)}개 수집", "ok")
        return items


# ─── 오늘의 영어 단어 ─────────────────────────────────────��──────────────────

class WordOfDayCrawler:
    """Wordnik API로 오늘의 영어 단어 수집 (공개키 사용)."""

    API_URL = "https://api.wordnik.com/v4/words.json/wordOfTheDay"
    # Wordnik 공개 데모키
    API_KEY = "c23b746d074135dc9500c0a61300a3cb7647e53ec2b9b658e"

    def fetch(self) -> dict:
        """오늘의 영어 단어."""
        log("오늘의 영어 단어 수집", "step")
        try:
            resp = requests.get(
                self.API_URL,
                params={"api_key": self.API_KEY},
                timeout=REQUEST_TIMEOUT,
            )
            resp.raise_for_status()
            data = resp.json()

            definitions = []
            for d in data.get("definitions", []):
                definitions.append({
                    "text": d.get("text", ""),
                    "part": d.get("partOfSpeech", ""),
                })

            examples = []
            for e in data.get("examples", []):
                examples.append(e.get("text", ""))

            result = {
                "word": data.get("word", ""),
                "definitions": definitions,
                "examples": examples[:2],
                "note": data.get("note", ""),
                "crawled_at": datetime.now().isoformat(),
            }
            log(f"오늘의 단어: {result['word']}", "ok")
            return result
        except Exception as e:
            log(f"영어 단어 수집 실패: {e}", "error")
            return {"word": "", "error": str(e)}


# ─── 오늘의 역사 ─────────────────────────────────────────────────────────────

class TodayInHistoryCrawler:
    """위키백과에서 오늘의 역사적 사건 크롤링."""

    BASE_URL = "https://ko.wikipedia.org/wiki"

    def fetch(self, count: int = 10) -> list[dict]:
        """오늘 날짜의 역사적 사건 목록."""
        now = datetime.now()
        url = f"{self.BASE_URL}/{now.month}월_{now.day}일"
        log(f"오늘의 역사 수집: {now.month}월 {now.day}일", "step")

        try:
            resp = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
        except Exception as e:
            log(f"위키백과 요청 실패: {e}", "error")
            return []

        soup = BeautifulSoup(resp.text, "lxml")
        events = []

        for li in soup.select(".mw-parser-output ul li"):
            text = li.get_text(strip=True)
            # "YYYY년 — 사건" 형태만 필터링
            if len(text) > 10 and re.match(r"\d{2,4}년", text):
                # 연도와 사건 분리
                match = re.match(r"(\d{2,4}년)\s*[-–—:]\s*(.+)", text)
                if match:
                    year = match.group(1)
                    event = match.group(2)
                else:
                    year = text[:text.index("년") + 1]
                    event = text[text.index("년") + 1:].lstrip(" -–—:,")

                events.append({
                    "year": year,
                    "event": event[:100],
                    "crawled_at": datetime.now().isoformat(),
                })

                if len(events) >= count:
                    break

        log(f"오늘의 역사: {len(events)}건 수집", "ok")
        return events


# ─── IT/개발 뉴스 ────────────────────────────────────────────────────────────

class ITNewsCrawler:
    """IT/개발 뉴스 통합 수집 (RSS + 크롤링)."""

    def fetch_geeknews(self, count: int = 10) -> list[dict]:
        """GeekNews 크롤링."""
        log("GeekNews 수집", "step")
        try:
            resp = requests.get("https://news.hada.io/", headers=HEADERS, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
        except Exception as e:
            log(f"GeekNews 요청 실패: {e}", "error")
            return []

        soup = BeautifulSoup(resp.text, "lxml")
        items = []
        for row in soup.select(".topic_row")[:count]:
            title_el = row.select_one(".topictitle a")
            if not title_el:
                continue

            href = title_el.get("href", "")
            if href and not href.startswith("http"):
                href = "https://news.hada.io" + href

            items.append({
                "title": title_el.get_text(strip=True),
                "url": href,
                "source": "GeekNews",
                "crawled_at": datetime.now().isoformat(),
            })

        log(f"GeekNews: {len(items)}건 수집", "ok")
        return items

    def fetch_hackernews(self, count: int = 10) -> list[dict]:
        """Hacker News 공개 API (키 불필요)."""
        log("Hacker News 수집", "step")
        try:
            resp = requests.get(
                "https://hacker-news.firebaseio.com/v0/topstories.json",
                timeout=REQUEST_TIMEOUT,
            )
            resp.raise_for_status()
            story_ids = resp.json()[:count]
        except Exception as e:
            log(f"Hacker News 요청 실패: {e}", "error")
            return []

        items = []
        for sid in story_ids:
            try:
                r = requests.get(
                    f"https://hacker-news.firebaseio.com/v0/item/{sid}.json",
                    timeout=5,
                )
                story = r.json()
                items.append({
                    "title": story.get("title", ""),
                    "url": story.get("url", f"https://news.ycombinator.com/item?id={sid}"),
                    "score": story.get("score", 0),
                    "source": "Hacker News",
                    "crawled_at": datetime.now().isoformat(),
                })
            except Exception:
                continue

        log(f"Hacker News: {len(items)}건 수집", "ok")
        return items

    def fetch_yozm(self, count: int = 10) -> list[dict]:
        """요즘IT RSS 수집."""
        if not HAS_FEEDPARSER:
            log("feedparser 미설치", "error")
            return []

        log("요즘IT 수집", "step")
        try:
            feed = feedparser.parse("https://yozm.wishket.com/magazine/feed/")
        except Exception as e:
            log(f"요즘IT 수집 실패: {e}", "error")
            return []

        items = []
        for entry in feed.entries[:count]:
            items.append({
                "title": entry.get("title", ""),
                "url": entry.get("link", ""),
                "summary": entry.get("summary", "")[:100],
                "source": "요즘IT",
                "crawled_at": datetime.now().isoformat(),
            })

        log(f"요즘IT: {len(items)}건 수집", "ok")
        return items

    def fetch_all(self, count: int = 10) -> list[dict]:
        """전체 IT 뉴스 통합 수집."""
        all_news = []
        all_news.extend(self.fetch_geeknews(count))
        all_news.extend(self.fetch_hackernews(count))
        all_news.extend(self.fetch_yozm(count))
        return all_news


# ─── GitHub 트렌딩 ───────────────────────────────────────────────────────────

class GitHubTrendingCrawler:
    """GitHub 일간 트렌��� 저장소 크롤링 (키 불필요)."""

    URL = "https://github.com/trending"

    def fetch(self, count: int = 10, language: str = "") -> list[dict]:
        """일간 트렌딩 저장소 수집.

        Args:
            count: 수집 건수
            language: 프로그래밍 언어 필터 (예: "python", "javascript")
        """
        url = self.URL
        if language:
            url += f"/{language}"

        log(f"GitHub 트렌딩 수집{f' ({language})' if language else ''}", "step")

        try:
            resp = requests.get(
                url,
                params={"since": "daily"},
                headers=HEADERS,
                timeout=REQUEST_TIMEOUT,
            )
            resp.raise_for_status()
        except Exception as e:
            log(f"GitHub 트렌딩 요청 실패: {e}", "error")
            return []

        soup = BeautifulSoup(resp.text, "lxml")
        items = []

        for i, article in enumerate(soup.select("article.Box-row")[:count], 1):
            name_el = article.select_one("h2 a")
            desc_el = article.select_one("p")
            stars_el = article.select_one("a.Link--muted:first-of-type")
            lang_el = article.select_one("[itemprop=programmingLanguage]")

            if not name_el:
                continue

            repo_name = name_el.get_text(strip=True).replace("\n", "").replace(" ", "")
            href = name_el.get("href", "")
            if href and not href.startswith("http"):
                href = "https://github.com" + href

            # 오늘의 스타 수
            today_stars = ""
            star_today_el = article.select_one(".float-sm-right")
            if star_today_el:
                today_stars = star_today_el.get_text(strip=True)

            items.append({
                "rank": i,
                "name": repo_name,
                "url": href,
                "description": desc_el.get_text(strip=True) if desc_el else "",
                "language": lang_el.get_text(strip=True) if lang_el else "",
                "stars": stars_el.get_text(strip=True) if stars_el else "",
                "today_stars": today_stars,
                "crawled_at": datetime.now().isoformat(),
            })

        log(f"GitHub 트렌딩: {len(items)}건 수집", "ok")
        return items


# ─── 통합 소스 ───────────────────────────────────────────────────────────────

class KnowledgeSource:
    """지식/교육 정보 통합 수집 클래스.

    API 키 없이 HTTP 요청만으로 동작��니다.

    Usage:
        source = KnowledgeSource()
        data = source.fetch_all()
        print(source.format_summary(data))
    """

    def __init__(self):
        self.quote = QuoteCrawler()
        self.word = WordOfDayCrawler()
        self.history = TodayInHistoryCrawler()
        self.it_news = ITNewsCrawler()
        self.github = GitHubTrendingCrawler()

    def fetch_all(self, count: int = 5) -> dict:
        """전체 지식/교육 데이터 일괄 수집."""
        log("지식/교육 통합 수집 시작", "step")

        result = {
            "quote": self.quote.fetch_today(),
            "word": self.word.fetch(),
            "history": self.history.fetch(count=count),
            "geeknews": self.it_news.fetch_geeknews(count=count),
            "hackernews": self.it_news.fetch_hackernews(count=count),
            "yozm": self.it_news.fetch_yozm(count=count),
            "github": self.github.fetch(count=count),
            "crawled_at": datetime.now().isoformat(),
        }

        log("지식/교육 통합 수집 완료", "ok")
        return result

    def format_summary(self, data: dict) -> str:
        """지식/교육 데이터를 읽기 좋은 텍스트로 변환."""
        lines = [f"📚 지식/교육 ({datetime.now().strftime('%Y-%m-%d %H:%M')})"]

        # 오늘의 명언
        q = data.get("quote", {})
        if q.get("quote"):
            lines.append(f"\n💬 오늘의 명언")
            lines.append(f"  \"{q['quote']}\"")
            lines.append(f"  — {q['author']}")

        # 오늘의 영어 단어
        w = data.get("word", {})
        if w.get("word"):
            lines.append(f"\n📝 오늘의 영어 단어: {w['word']}")
            for d in w.get("definitions", [])[:2]:
                lines.append(f"  [{d['part']}] {d['text'][:60]}")

        # 오늘의 역사
        if data.get("history"):
            lines.append(f"\n📅 오늘의 역사 ({datetime.now().month}월 {datetime.now().day}일)")
            for item in data["history"][:5]:
                lines.append(f"  {item['year']} {item['event'][:50]}")

        # IT 뉴스
        if data.get("geeknews"):
            lines.append("\n🖥️ GeekNews")
            for item in data["geeknews"][:5]:
                lines.append(f"  • {item['title'][:55]}")

        if data.get("hackernews"):
            lines.append("\n🔶 Hacker News")
            for item in data["hackernews"][:5]:
                lines.append(f"  • {item['title'][:55]} ({item['score']}pt)")

        if data.get("yozm"):
            lines.append("\n📰 요즘IT")
            for item in data["yozm"][:5]:
                lines.append(f"  • {item['title'][:55]}")

        # GitHub 트렌딩
        if data.get("github"):
            lines.append("\n🐙 GitHub 트렌딩")
            for item in data["github"][:5]:
                lang = f" [{item['language']}]" if item["language"] else ""
                lines.append(f"  {item['rank']}. {item['name']}{lang}")
                if item["description"]:
                    lines.append(f"     {item['description'][:50]}")

        return "\n".join(lines)

    def format_blog_content(self, data: dict) -> str:
        """블로그 발행용 HTML 콘텐츠 생성."""
        today = datetime.now().strftime("%Y년 %m월 %d일")
        parts = [f"<h2>오늘의 지식/교육 ({today})</h2>"]

        # 오늘의 명언
        q = data.get("quote", {})
        if q.get("quote"):
            parts.append(
                f"<blockquote style='border-left:4px solid #3498db; padding:10px 15px; "
                f"margin:20px 0; background:#f8f9fa;'>"
                f"<p style='font-style:italic; margin:0;'>\"{q['quote']}\"</p>"
                f"<footer style='margin-top:8px; color:#666;'>— {q['author']}</footer>"
                f"</blockquote>"
            )

        # 오늘의 영어 단어
        w = data.get("word", {})
        if w.get("word"):
            parts.append(f"<h3>오늘의 영어 단어: <strong>{w['word']}</strong></h3>")
            parts.append("<ul>")
            for d in w.get("definitions", [])[:3]:
                parts.append(f"<li><em>[{d['part']}]</em> {d['text']}</li>")
            parts.append("</ul>")

        # 오늘의 역사
        if data.get("history"):
            now = datetime.now()
            parts.append(f"<h3>오늘의 역사 ({now.month}월 {now.day}일)</h3><ul>")
            for item in data["history"][:7]:
                parts.append(
                    f"<li><strong>{item['year']}</strong> — {item['event']}</li>"
                )
            parts.append("</ul>")

        # IT 뉴스
        all_news = []
        for key in ["geeknews", "hackernews", "yozm"]:
            all_news.extend(data.get(key, [])[:3])
        if all_news:
            parts.append("<h3>IT/개발 뉴스</h3><ul>")
            for item in all_news[:10]:
                source = f" <small>({item['source']})</small>" if item.get("source") else ""
                parts.append(
                    f"<li><a href='{item['url']}'>{item['title']}</a>{source}</li>"
                )
            parts.append("</ul>")

        return "\n".join(parts)
