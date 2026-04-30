"""
생활날씨 정보 수집 모듈
- 네이버 검색 날씨 크롤링 (API 키 불필요)
- 현재 날씨, 체감온도, 습도, 풍속
- 미세먼지, 초미세먼지, 자외선 지수
- 일출/일몰 시간
- 시간대별 예보
- 주요 도시 및 자유 지역 검색 지원
"""
import re
from datetime import datetime
from typing import Optional

import requests
from bs4 import BeautifulSoup

from common.logger import log

# ─── 설정 ────────────────────────────────────────────────────────────────────

NAVER_SEARCH_URL = "https://search.naver.com/search.naver"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ko-KR,ko;q=0.9",
}

REQUEST_TIMEOUT = 15

# 주요 도시 목록
CITIES = {
    "서울": "서울",
    "부산": "부산",
    "대구": "대구",
    "인천": "인천",
    "광주": "광주",
    "대전": "대전",
    "울산": "울산",
    "세종": "세종",
    "수원": "수원",
    "제주": "제주",
    "춘천": "춘천",
    "강릉": "강릉",
    "청주": "청주",
    "전주": "전주",
    "포항": "포항",
    "창원": "창원",
}


# ─── 파서 ────────────────────────────────────────────────────────────────────

def _parse_temperature(text: str) -> Optional[float]:
    """온도 문자열에서 숫자 추출. '현재 온도20.5°' -> 20.5"""
    match = re.search(r"(-?\d+\.?\d*)\s*°", text)
    return float(match.group(1)) if match else None


def _parse_weather_page(soup: BeautifulSoup) -> dict:
    """네이버 검색 날씨 페이지에서 정보 추출."""
    result = {
        "temperature": None,
        "weather": "",
        "feels_like": None,
        "humidity": "",
        "wind": "",
        "dust": "",
        "fine_dust": "",
        "uv": "",
        "sunset": "",
        "sunrise": "",
        "hourly": [],
        "crawled_at": datetime.now().isoformat(),
    }

    # 현재 기온
    temp_el = soup.select_one(".temperature_text")
    if temp_el:
        result["temperature"] = _parse_temperature(temp_el.get_text())

    # 날씨 상태 (맑음, 흐림 등)
    weather_el = soup.select_one(".weather_main")
    if weather_el:
        result["weather"] = weather_el.get_text(strip=True)

    # 체감온도, 습도, 풍속
    for item in soup.select(".summary_list .sort"):
        dt = item.select_one("dt")
        dd = item.select_one("dd")
        if not (dt and dd):
            continue
        label = dt.get_text(strip=True)
        value = dd.get_text(strip=True)

        if "체감" in label:
            result["feels_like"] = _parse_temperature(value)
        elif "습도" in label:
            result["humidity"] = value
        elif "풍" in label:
            result["wind"] = f"{label} {value}"

    # 미세먼지, 초미세먼지, 자외선, 일출/일몰
    for li in soup.select(".today_chart_list > li"):
        title_el = li.select_one(".title")
        txt_el = li.select_one(".txt")
        if not (title_el and txt_el):
            continue
        title = title_el.get_text(strip=True)
        value = txt_el.get_text(strip=True)

        if title == "미세먼지":
            result["dust"] = value
        elif title == "초미세먼지":
            result["fine_dust"] = value
        elif title == "자외선":
            result["uv"] = value
        elif title == "일몰":
            result["sunset"] = value
        elif title == "일출":
            result["sunrise"] = value

    # 시간대별 예보 (간략)
    hourly_section = soup.select_one("._cnc1")
    if hourly_section:
        # 시간, 날씨, 온도 패턴 추출
        text = hourly_section.get_text()
        hours = re.findall(r"(\d{1,2})시([가-힣]+)(-?\d+)°", text)
        for h, w, t in hours[:8]:
            result["hourly"].append({
                "hour": f"{h}시",
                "weather": w,
                "temp": int(t),
            })

    return result


# ─── 메인 클래스 ─────────────────────────────────────────────────────────────

class WeatherSource:
    """네이버 검색 기반 생활날씨 수집 클래스.

    API 키 없이 HTTP 요청만으로 동작합니다.

    Usage:
        source = WeatherSource()
        weather = source.fetch("서울")
        multi = source.fetch_cities(["서울", "부산", "제주"])
    """

    def fetch(self, location: str = "서울") -> dict:
        """특정 지역의 현재 날씨 정보를 수집.

        Args:
            location: 지역명 (예: "서울", "강남구", "해운대")

        Returns:
            {location, temperature, weather, feels_like, humidity, wind,
             dust, fine_dust, uv, sunset, sunrise, hourly, crawled_at}
        """
        query = f"{location} 날씨"
        log(f"날씨 수집: {location}", "step")

        try:
            resp = requests.get(
                NAVER_SEARCH_URL,
                params={"query": query},
                headers=HEADERS,
                timeout=REQUEST_TIMEOUT,
            )
            resp.raise_for_status()
        except Exception as e:
            log(f"날씨 요청 실패 ({location}): {e}", "error")
            return {"location": location, "error": str(e)}

        soup = BeautifulSoup(resp.text, "lxml")
        data = _parse_weather_page(soup)
        data["location"] = location

        if data["temperature"] is not None:
            log(
                f"{location}: {data['temperature']}° {data['weather']} | "
                f"미세먼지:{data['dust']} | 자외선:{data['uv']}",
                "ok",
            )
        else:
            log(f"{location}: 날씨 정보 파싱 실패", "warn")

        return data

    def fetch_cities(self, cities: Optional[list[str]] = None) -> list[dict]:
        """여러 도시의 날씨를 한번에 수집.

        Args:
            cities: 도시 목록. None이면 주요 도시 전체.

        Returns:
            list of weather dicts
        """
        targets = cities or list(CITIES.keys())
        log(f"다중 도시 날씨 수집: {len(targets)}개 도시", "step")

        results = []
        for city in targets:
            data = self.fetch(city)
            results.append(data)

        log(f"날씨 수집 완료: {len(results)}개 도시", "ok")
        return results

    def format_summary(self, data: dict) -> str:
        """날씨 데이터를 사람이 읽기 좋은 텍스트로 변환.

        Args:
            data: fetch()의 반환값

        Returns:
            포맷된 날씨 요약 문자열
        """
        if "error" in data:
            return f"[{data['location']}] 정보 수집 실패"

        lines = [
            f"📍 {data['location']} 현재 날씨",
            f"🌡️ {data['temperature']}° (체감 {data['feels_like']}°) — {data['weather']}",
            f"💧 습도 {data['humidity']} | 🌬️ {data['wind']}",
        ]

        env_parts = []
        if data["dust"]:
            env_parts.append(f"미세먼지 {data['dust']}")
        if data["fine_dust"]:
            env_parts.append(f"초미세먼지 {data['fine_dust']}")
        if data["uv"]:
            env_parts.append(f"자외선 {data['uv']}")
        if env_parts:
            lines.append(f"😷 {' | '.join(env_parts)}")

        sun_parts = []
        if data["sunrise"]:
            sun_parts.append(f"🌅 일출 {data['sunrise']}")
        if data["sunset"]:
            sun_parts.append(f"🌇 일몰 {data['sunset']}")
        if sun_parts:
            lines.append(" | ".join(sun_parts))

        if data["hourly"]:
            hourly_str = " → ".join(
                f"{h['hour']} {h['temp']}°{h['weather']}"
                for h in data["hourly"][:6]
            )
            lines.append(f"⏰ {hourly_str}")

        return "\n".join(lines)

    def format_blog_content(self, data: dict) -> str:
        """블로그 발행용 HTML 콘텐츠 생성.

        Args:
            data: fetch()의 반환값

        Returns:
            HTML 형식의 날씨 콘텐츠
        """
        if "error" in data:
            return f"<p>{data['location']} 날씨 정보를 가져올 수 없습니다.</p>"

        today = datetime.now().strftime("%Y년 %m월 %d일")

        html_parts = [
            f"<h2>{data['location']} 오늘의 날씨 ({today})</h2>",
            "<table style='border-collapse:collapse; width:100%; max-width:500px;'>",
            "<tbody>",
            f"<tr><td style='padding:8px; border:1px solid #ddd;'><strong>현재 기온</strong></td>"
            f"<td style='padding:8px; border:1px solid #ddd;'>{data['temperature']}°C</td></tr>",
            f"<tr><td style='padding:8px; border:1px solid #ddd;'><strong>체감 온도</strong></td>"
            f"<td style='padding:8px; border:1px solid #ddd;'>{data['feels_like']}°C</td></tr>",
            f"<tr><td style='padding:8px; border:1px solid #ddd;'><strong>날씨</strong></td>"
            f"<td style='padding:8px; border:1px solid #ddd;'>{data['weather']}</td></tr>",
            f"<tr><td style='padding:8px; border:1px solid #ddd;'><strong>습도</strong></td>"
            f"<td style='padding:8px; border:1px solid #ddd;'>{data['humidity']}</td></tr>",
            f"<tr><td style='padding:8px; border:1px solid #ddd;'><strong>바람</strong></td>"
            f"<td style='padding:8px; border:1px solid #ddd;'>{data['wind']}</td></tr>",
            f"<tr><td style='padding:8px; border:1px solid #ddd;'><strong>미세먼지</strong></td>"
            f"<td style='padding:8px; border:1px solid #ddd;'>{data['dust']}</td></tr>",
            f"<tr><td style='padding:8px; border:1px solid #ddd;'><strong>초미세먼지</strong></td>"
            f"<td style='padding:8px; border:1px solid #ddd;'>{data['fine_dust']}</td></tr>",
            f"<tr><td style='padding:8px; border:1px solid #ddd;'><strong>자외선</strong></td>"
            f"<td style='padding:8px; border:1px solid #ddd;'>{data['uv']}</td></tr>",
        ]

        if data["sunset"]:
            html_parts.append(
                f"<tr><td style='padding:8px; border:1px solid #ddd;'><strong>일몰</strong></td>"
                f"<td style='padding:8px; border:1px solid #ddd;'>{data['sunset']}</td></tr>"
            )

        html_parts.extend(["</tbody>", "</table>"])

        # 시간대별 예보
        if data["hourly"]:
            html_parts.append("<h3>시간대별 예보</h3>")
            html_parts.append(
                "<table style='border-collapse:collapse; width:100%; max-width:500px;'>"
            )
            html_parts.append(
                "<thead><tr>"
                "<th style='padding:6px; border:1px solid #ddd;'>시간</th>"
                "<th style='padding:6px; border:1px solid #ddd;'>날씨</th>"
                "<th style='padding:6px; border:1px solid #ddd;'>기온</th>"
                "</tr></thead><tbody>"
            )
            for h in data["hourly"][:8]:
                html_parts.append(
                    f"<tr>"
                    f"<td style='padding:6px; border:1px solid #ddd; text-align:center;'>{h['hour']}</td>"
                    f"<td style='padding:6px; border:1px solid #ddd; text-align:center;'>{h['weather']}</td>"
                    f"<td style='padding:6px; border:1px solid #ddd; text-align:center;'>{h['temp']}°</td>"
                    f"</tr>"
                )
            html_parts.extend(["</tbody>", "</table>"])

        return "\n".join(html_parts)
