"""
문화체육관광부 정책브리핑 정책뉴스 API

공공데이터포털(data.go.kr) API
End Point: https://apis.data.go.kr/1371000/policyNewsService
데이터포맷: XML
"""
import os
from typing import Optional
import xml.etree.ElementTree as ET

import requests

from common.logger import log


POLICY_NEWS_URL = "https://apis.data.go.kr/1371000/policyNewsService"


class PolicyNewsSource:
    """정책브리핑 정책뉴스 수집."""

    def __init__(self, service_key: Optional[str] = None):
        self.service_key = service_key or os.getenv("DATA_GO_KR_KEY", "")

    def get_news_list(self, num_rows: int = 20, page: int = 1) -> list[dict]:
        """정책뉴스 목록 조회.

        Args:
            num_rows: 조회 건수
            page: 페이지 번호

        Returns:
            뉴스 정보 dict 목록
        """
        url = f"{POLICY_NEWS_URL}/policyNewsList"
        params = {
            "serviceKey": self.service_key,
            "numOfRows": num_rows,
            "pageNo": page,
        }
        log(f"정책뉴스 조회: page={page}, rows={num_rows}", "step")
        try:
            resp = requests.get(url, params=params, timeout=15)
            resp.raise_for_status()

            root = ET.fromstring(resp.content)
            items = root.findall(".//item")

            news_list = []
            for item in items:
                news_list.append({
                    "title": _text(item, "title"),
                    "link": _text(item, "link"),
                    "description": _text(item, "description"),
                    "pub_date": _text(item, "pubDate"),
                    "category": _text(item, "category"),
                    "content": _text(item, "content"),
                })
            log(f"정책뉴스 {len(news_list)}건 수집", "ok")
            return news_list
        except Exception as e:
            log(f"정책뉴스 수집 실패: {e}", "error")
            return []

    def format_post_content(self, news_list: list[dict]) -> str:
        """정책뉴스를 블로그 포스트 HTML로 변환."""
        if not news_list:
            return "<p>정책뉴스 정보가 없습니다.</p>"

        articles = []
        for n in news_list:
            article = (
                f"<article>\n"
                f"<h3>{n['title']}</h3>\n"
                f"<p class='meta'>{n['pub_date']} | {n['category']}</p>\n"
                f"<p>{n['description']}</p>\n"
                f"<p><a href='{n['link']}'>원문 보기</a></p>\n"
                f"</article>\n<hr>\n"
            )
            articles.append(article)

        html = (
            "<h2>정책브리핑 정책뉴스</h2>\n"
            + "\n".join(articles)
            + "<p><small>출처: 문화체육관광부 정책브리핑 (data.go.kr)</small></p>"
        )
        return html


def _text(element, tag: str) -> str:
    el = element.find(tag)
    return el.text.strip() if el is not None and el.text else ""
