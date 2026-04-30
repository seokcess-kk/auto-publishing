"""
핫딜 커뮤니티 크롤링 모듈
- 뽐뿌 (ppomppu.co.kr) 국내/해외 핫딜
- 클리앙 (clien.net) 알뜰구매
- 루리웹 (ruliweb.com) 핫딜
- 중복 제거 및 통합 결과 제공
"""
import re
import time
import random
from datetime import datetime
from typing import Optional

import requests
from bs4 import BeautifulSoup

from common.logger import log

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


# ─── 뽐뿌 ───────────────────────────────────────────────────────────────────

class PpomppuCrawler:
    """뽐뿌 핫딜 게시판 크롤러."""

    BOARDS = {
        "국내": "https://www.ppomppu.co.kr/zboard/zboard.php?id=ppomppu",
        "해외": "https://www.ppomppu.co.kr/zboard/zboard.php?id=ppomppu4",
    }

    def fetch(self, board: str = "국내", count: int = 15) -> list[dict]:
        url = self.BOARDS.get(board, self.BOARDS["국내"])
        log(f"뽐뿌 크롤링: {board} 핫딜 (최대 {count}건)", "step")

        try:
            resp = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
            resp.encoding = "euc-kr"
        except Exception as e:
            log(f"뽐뿌 요청 실패: {e}", "error")
            return []

        soup = BeautifulSoup(resp.text, "lxml")
        rows = soup.select("tr.baseList.bbs_new1")
        if not rows:
            rows = soup.select("tr.list0, tr.list1")

        items = []
        for row in rows[:count]:
            try:
                # 제목
                title_tag = row.select_one("a.baseList-title") or row.select_one("a.list_subject")
                if not title_tag:
                    continue
                title = title_tag.get_text(strip=True)
                href = title_tag.get("href", "")
                if href and not href.startswith("http"):
                    href = "https://www.ppomppu.co.kr/zboard/" + href

                # 추천수
                vote_tag = row.select_one("td.baseList-rec") or row.select_one("td.list_vote")
                votes = 0
                if vote_tag:
                    vote_text = vote_tag.get_text(strip=True).split("-")[0].strip()
                    votes = int(vote_text) if vote_text.isdigit() else 0

                # 댓글수
                comment_tag = row.select_one("span.baseList-comment") or row.select_one("span.list_comment2")
                comments = 0
                if comment_tag:
                    c_text = re.sub(r"[^\d]", "", comment_tag.get_text())
                    comments = int(c_text) if c_text else 0

                # 이미지
                img_tag = row.select_one("img.baseList-thumb") or row.select_one("a.list_subject img")
                image = ""
                if img_tag:
                    image = img_tag.get("src", "")
                    if image and not image.startswith("http"):
                        image = "https://www.ppomppu.co.kr" + image

                items.append({
                    "title": title,
                    "url": href,
                    "image": image,
                    "votes": votes,
                    "comments": comments,
                    "source": "뽐뿌",
                    "board": board,
                    "crawled_at": datetime.now().isoformat(),
                })
            except Exception:
                continue

        log(f"뽐뿌 {board}: {len(items)}건 수집", "ok")
        return items


# ─── 클리앙 ──────────────────────────────────────────────────────────────────

class ClienCrawler:
    """클리앙 알뜰구매 게시판 크롤러."""

    URL = "https://www.clien.net/service/board/jirum"

    def fetch(self, count: int = 15) -> list[dict]:
        log(f"클리앙 크롤링: 알뜰구매 (최대 {count}건)", "step")

        try:
            resp = requests.get(self.URL, headers=HEADERS, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
        except Exception as e:
            log(f"클리앙 요청 실패: {e}", "error")
            return []

        soup = BeautifulSoup(resp.text, "lxml")
        rows = soup.select("div.list_item.symph_row")

        items = []
        for row in rows[:count]:
            try:
                title_tag = (
                    row.select_one("span.subject_fixed")
                    or row.select_one("span.list_subject")
                )
                if not title_tag:
                    continue

                link_tag = row.select_one("a.list_subject") or row.select_one("a")
                title = title_tag.get_text(strip=True)
                href = ""
                if link_tag:
                    href = link_tag.get("href", "")
                    if href and not href.startswith("http"):
                        href = "https://www.clien.net" + href

                # 추천수
                vote_tag = (
                    row.select_one("div.list_symph span")
                    or row.select_one("span.symph_count")
                )
                votes = 0
                if vote_tag:
                    v_text = vote_tag.get_text(strip=True)
                    votes = int(v_text) if v_text.isdigit() else 0

                # 댓글수
                comment_tag = row.select_one("span.rSymph05")
                comments = 0
                if comment_tag:
                    c_text = re.sub(r"[^\d]", "", comment_tag.get_text())
                    comments = int(c_text) if c_text else 0

                # 이미지
                img_tag = row.select_one("img.lazy")
                image = ""
                if img_tag:
                    image = img_tag.get("data-src", "") or img_tag.get("src", "")

                items.append({
                    "title": title,
                    "url": href,
                    "image": image,
                    "votes": votes,
                    "comments": comments,
                    "source": "클리앙",
                    "board": "알뜰구매",
                    "crawled_at": datetime.now().isoformat(),
                })
            except Exception:
                continue

        log(f"클리앙 알뜰구매: {len(items)}건 수집", "ok")
        return items


# ─── 루리웹 ──────────────────────────────────────────────────────────────────

class RuliwebCrawler:
    """루리웹 핫딜 게시판 크롤러."""

    URL = "https://bbs.ruliweb.com/market/board/1020"

    def fetch(self, count: int = 15) -> list[dict]:
        log(f"루리웹 크롤링: 핫딜 (최대 {count}건)", "step")

        try:
            resp = requests.get(self.URL, headers=HEADERS, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
        except Exception as e:
            log(f"루리웹 요청 실패: {e}", "error")
            return []

        soup = BeautifulSoup(resp.text, "lxml")
        rows = soup.select("tr.table_body.blocktarget")

        items = []
        for row in rows[:count]:
            try:
                title_tag = row.select_one("a.deco")
                if not title_tag:
                    continue

                title = title_tag.get_text(strip=True)
                href = title_tag.get("href", "")
                if href and not href.startswith("http"):
                    href = "https://bbs.ruliweb.com" + href

                # 추천수
                vote_tag = row.select_one("td.recomd")
                votes = 0
                if vote_tag:
                    v_text = vote_tag.get_text(strip=True)
                    votes = int(v_text) if v_text.isdigit() else 0

                # 댓글수
                comment_tag = row.select_one("a.num_reply span.num")
                comments = 0
                if comment_tag:
                    c_text = re.sub(r"[^\d]", "", comment_tag.get_text())
                    comments = int(c_text) if c_text else 0

                items.append({
                    "title": title,
                    "url": href,
                    "image": "",
                    "votes": votes,
                    "comments": comments,
                    "source": "루리웹",
                    "board": "핫딜",
                    "crawled_at": datetime.now().isoformat(),
                })
            except Exception:
                continue

        log(f"루리웹 핫딜: {len(items)}건 수집", "ok")
        return items


# ─── 통합 핫딜 소스 ──────────────────────────────────────────────────────────

class HotdealSource:
    """여러 핫딜 커뮤니티를 통합 수집하는 메인 클래스.

    Usage:
        source = HotdealSource()
        deals = source.fetch(count=20, sort_by="votes")
    """

    def __init__(self, sites: Optional[list[str]] = None):
        """
        Args:
            sites: 수집할 사이트 목록. None이면 전체.
                   선택지: "뽐뿌", "클리앙", "루리웹"
        """
        self.sites = sites or ["뽐뿌", "클리앙", "루리웹"]
        self._crawlers = {
            "뽐뿌": PpomppuCrawler(),
            "클리앙": ClienCrawler(),
            "루리웹": RuliwebCrawler(),
        }

    def fetch(self, count: int = 20, sort_by: str = "votes",
              ppomppu_board: str = "국내") -> list[dict]:
        """전체 사이트에서 핫딜을 수집하고 정렬하여 반환.

        Args:
            count: 최종 반환할 최대 건수
            sort_by: 정렬 기준 ("votes", "comments", "recent")
            ppomppu_board: 뽐뿌 게시판 선택 ("국내", "해외")

        Returns:
            list of {title, url, image, votes, comments, source, board, crawled_at}
        """
        log(f"핫딜 통합 수집 시작: sites={self.sites}, sort={sort_by}", "step")
        all_deals = []

        for site in self.sites:
            crawler = self._crawlers.get(site)
            if not crawler:
                log(f"알 수 없는 사이트: {site}", "warn")
                continue

            if site == "뽐뿌":
                deals = crawler.fetch(board=ppomppu_board, count=count)
            else:
                deals = crawler.fetch(count=count)

            all_deals.extend(deals)
            time.sleep(random.uniform(0.5, 1.5))

        # 중복 제거 (제목 기준, 괄호 숫자 등 제거 후 비교)
        seen_titles = set()
        unique_deals = []
        for deal in all_deals:
            normalized = re.sub(r"\(\d+\)\s*$", "", deal["title"])
            normalized = re.sub(r"\s+", "", normalized.lower())
            if normalized not in seen_titles:
                seen_titles.add(normalized)
                unique_deals.append(deal)

        # 정렬
        if sort_by == "votes":
            unique_deals.sort(key=lambda x: x["votes"], reverse=True)
        elif sort_by == "comments":
            unique_deals.sort(key=lambda x: x["comments"], reverse=True)

        result = unique_deals[:count]
        log(f"핫딜 통합 수집 완료: 총 {len(all_deals)}건 → 중복 제거 {len(unique_deals)}건 → 상위 {len(result)}건", "ok")
        return result

    def fetch_best(self, count: int = 10, min_votes: int = 5) -> list[dict]:
        """추천수 기준 베스트 핫딜만 필터링.

        Args:
            count: 반환할 최대 건수
            min_votes: 최소 추천수 기준
        """
        deals = self.fetch(count=50, sort_by="votes")
        best = [d for d in deals if d["votes"] >= min_votes]
        result = best[:count]
        log(f"베스트 핫딜: 추천 {min_votes}+ 필터 → {len(result)}건", "ok")
        return result
