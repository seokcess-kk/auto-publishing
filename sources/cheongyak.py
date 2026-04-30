"""
한국부동산원 청약홈 분양정보 조회 서비스

공공데이터포털(data.go.kr) API
Base URL: api.odcloud.kr/api
데이터포맷: JSON+XML
"""
import os
from typing import Optional

import requests

from common.logger import log


CHEONGYAK_BASE_URL = "https://api.odcloud.kr/api"


class CheongyakSource:
    """청약홈 분양정보 수집."""

    def __init__(self, service_key: Optional[str] = None):
        self.service_key = service_key or os.getenv("DATA_GO_KR_KEY", "")

    def get_apt_announcements(self, page: int = 1, per_page: int = 20) -> list[dict]:
        """APT 분양 공고 목록 조회.

        Args:
            page: 페이지 번호
            per_page: 페이지당 건수

        Returns:
            분양 공고 dict 목록
        """
        url = f"{CHEONGYAK_BASE_URL}/ApplyhomeInfoDetailSvc/v1/getAPTLttotPblancDetail"
        params = {
            "serviceKey": self.service_key,
            "page": page,
            "perPage": per_page,
        }
        log(f"청약홈 분양정보 조회: page={page}", "step")
        try:
            resp = requests.get(url, params=params, timeout=15)
            resp.raise_for_status()
            data = resp.json()

            items = data.get("data", [])
            announcements = []
            for item in items:
                announcements.append({
                    "house_name": item.get("HOUSE_NM", ""),
                    "house_detail": item.get("HOUSE_DTL_SECD_NM", ""),
                    "region": item.get("SUBSCRPT_AREA_CODE_NM", ""),
                    "supply_count": item.get("TOT_SUPLY_HSHLDCO", ""),
                    "rcept_begin": item.get("RCEPT_BGNDE", ""),
                    "rcept_end": item.get("RCEPT_ENDDE", ""),
                    "announce_date": item.get("PBLANC_DE", ""),
                    "contract_begin": item.get("CNTRCT_CNCLS_BGNDE", ""),
                    "contract_end": item.get("CNTRCT_CNCLS_ENDDE", ""),
                    "homepage_url": item.get("HMPG_ADRES", ""),
                    "construction": item.get("BSNS_MBY_NM", ""),
                })
            log(f"청약홈 분양정보 {len(announcements)}건 수집", "ok")
            return announcements
        except Exception as e:
            log(f"청약홈 분양정보 수집 실패: {e}", "error")
            return []

    def format_post_content(self, announcements: list[dict]) -> str:
        """분양정보를 블로그 포스트 HTML로 변환."""
        if not announcements:
            return "<p>분양 공고 정보가 없습니다.</p>"

        rows = "\n".join(
            f"<tr>"
            f"<td>{a['house_name']}</td>"
            f"<td>{a['region']}</td>"
            f"<td>{a['house_detail']}</td>"
            f"<td>{a['supply_count']}세대</td>"
            f"<td>{a['rcept_begin']} ~ {a['rcept_end']}</td>"
            f"<td>{a['announce_date']}</td>"
            f"</tr>"
            for a in announcements
        )
        html = (
            "<h2>청약홈 분양정보</h2>\n"
            "<table border='1'>\n"
            "<tr><th>단지명</th><th>지역</th><th>유형</th><th>공급세대</th>"
            "<th>접수기간</th><th>공고일</th></tr>\n"
            f"{rows}\n"
            "</table>\n"
            "<p><small>출처: 한국부동산원 청약홈 (data.go.kr)</small></p>"
        )
        return html
