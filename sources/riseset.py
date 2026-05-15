"""
한국천문연구원 출몰시각 정보

공공데이터포털(data.go.kr) API
End Point: https://apis.data.go.kr/B090041/openapi/service/RiseSetInfoService
데이터포맷: XML
"""
import os
from typing import Optional
from datetime import datetime
from urllib.parse import unquote
import xml.etree.ElementTree as ET

import requests

from common.logger import log


RISESET_URL = "https://apis.data.go.kr/B090041/openapi/service/RiseSetInfoService"


class RiseSetSource:
    """일출/일몰 시각 정보 수집."""

    def __init__(self, service_key: Optional[str] = None):
        # data.go.kr 발급 페이지의 "Encoded" 키를 그대로 .env 에 붙여 넣으면
        # requests 가 params 인코딩 시 `%` 를 한 번 더 인코딩해 401 이 난다.
        # unquote 로 한 번 디코딩해서 두 형태 모두 흡수.
        raw = service_key or os.getenv("DATA_GO_KR_KEY", "")
        self.service_key = unquote(raw)

    def get_riseset_info(self, location: str = "서울",
                         loc_x: str = "126.9783882",
                         loc_y: str = "37.5666103",
                         date: Optional[str] = None) -> dict:
        """특정 위치의 일출/일몰 시각 조회.

        Args:
            location: 지역명 (표시용)
            loc_x: 경도
            loc_y: 위도
            date: 조회 날짜 (YYYYMMDD). None이면 오늘.

        Returns:
            일출/일몰 정보 dict
        """
        if date is None:
            date = datetime.now().strftime("%Y%m%d")

        url = f"{RISESET_URL}/getLCRiseSetInfo"
        params = {
            "serviceKey": self.service_key,
            "locdate": date,
            "longitude": loc_x,
            "latitude": loc_y,
        }
        log(f"출몰시각 조회: {location} ({date})", "step")
        try:
            resp = requests.get(url, params=params, timeout=10,
                                headers={"User-Agent": "Mozilla/5.0"})
            resp.raise_for_status()

            root = ET.fromstring(resp.content)
            item = root.find(".//item")
            if item is None:
                log("출몰시각 데이터 없음", "warn")
                return {}

            info = {
                "location": location,
                "date": date,
                "sunrise": _time_fmt(_text(item, "sunrise")),
                "sunset": _time_fmt(_text(item, "sunset")),
                "moonrise": _time_fmt(_text(item, "moonrise")),
                "moonset": _time_fmt(_text(item, "moonset")),
                "civil_twilight_begin": _time_fmt(_text(item, "civile")),
                "civil_twilight_end": _time_fmt(_text(item, "civils")),
            }
            log(f"출몰시각 수집 완료: 일출 {info['sunrise']} / 일몰 {info['sunset']}", "ok")
            return info
        except Exception as e:
            log(f"출몰시각 수집 실패: {e}", "error")
            return {}

    def get_multi_location(self, locations: list[dict],
                           date: Optional[str] = None) -> list[dict]:
        """여러 지역의 출몰시각 일괄 조회.

        Args:
            locations: [{"name": "서울", "lon": "126.97", "lat": "37.56"}, ...]
            date: 조회 날짜 (YYYYMMDD)

        Returns:
            지역별 출몰시각 dict 목록
        """
        results = []
        for loc in locations:
            info = self.get_riseset_info(
                location=loc["name"],
                loc_x=loc["lon"],
                loc_y=loc["lat"],
                date=date,
            )
            if info:
                results.append(info)
        return results

    def format_post_content(self, info_list: list[dict]) -> str:
        """출몰시각 정보를 블로그 포스트 HTML로 변환."""
        if not info_list:
            return "<p>출몰시각 정보가 없습니다.</p>"

        date_str = info_list[0].get("date", "")
        if len(date_str) == 8:
            date_str = f"{date_str[:4]}년 {date_str[4:6]}월 {date_str[6:]}일"

        rows = "\n".join(
            f"<tr>"
            f"<td>{i['location']}</td>"
            f"<td>{i['sunrise']}</td>"
            f"<td>{i['sunset']}</td>"
            f"<td>{i['moonrise']}</td>"
            f"<td>{i['moonset']}</td>"
            f"</tr>"
            for i in info_list
        )
        html = (
            f"<h2>{date_str} 일출/일몰 시각</h2>\n"
            "<table border='1'>\n"
            "<tr><th>지역</th><th>일출</th><th>일몰</th><th>월출</th><th>월몰</th></tr>\n"
            f"{rows}\n"
            "</table>\n"
            "<p><small>출처: 한국천문연구원 (data.go.kr)</small></p>"
        )
        return html


def _text(element, tag: str) -> str:
    el = element.find(tag)
    return el.text.strip() if el is not None and el.text else ""


def _time_fmt(raw: str) -> str:
    """'0623' -> '06:23' 형식으로 변환."""
    if len(raw) == 4 and raw.isdigit():
        return f"{raw[:2]}:{raw[2:]}"
    return raw
