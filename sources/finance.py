"""
경제/금융 정보 수집 모듈 (API 키 불필요)
- 환율: 네이버 금융 시장지표 크롤링 (USD, JPY, EUR, CNY 등)
- 가상화폐: 업비트 공개 API (BTC, ETH, XRP 등)
- 금/은/유가: 네이버 금융 원자재 시세
- 주식 지수: 네이버 금융 KOSPI/KOSDAQ
- 개별 종목: 네이버 금융 종목 시세
"""
import re
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
}

REQUEST_TIMEOUT = 15

# 업비트 주요 코인
DEFAULT_COINS = ["KRW-BTC", "KRW-ETH", "KRW-XRP", "KRW-SOL", "KRW-DOGE"]

# 주요 종목 코드
POPULAR_STOCKS = {
    "삼성전자": "005930",
    "SK하이닉스": "000660",
    "LG에너지솔루션": "373220",
    "삼성바이오로직스": "207940",
    "현대차": "005380",
    "카카오": "035720",
    "네이버": "035420",
}


# ─── 환율 ────────────────────────────────────────────────────────────────────

class ExchangeRateCrawler:
    """네이버 금융 시장지표에서 환율/원자재 시세 크롤링."""

    URL = "https://finance.naver.com/marketindex/"

    def fetch(self) -> dict:
        """환율, 원자재(금/유가) 시세를 한번에 수집.

        Returns:
            {exchange: [...], commodities: [...], crawled_at: str}
        """
        log("네이버 금융 시장지표 수집", "step")

        try:
            resp = requests.get(self.URL, headers=HEADERS, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
        except Exception as e:
            log(f"네이버 금융 요청 실패: {e}", "error")
            return {"exchange": [], "commodities": [], "error": str(e)}

        soup = BeautifulSoup(resp.text, "lxml")

        exchange = []
        commodities = []

        for li in soup.select("li"):
            h3 = li.select_one("h3")
            val = li.select_one(".value")
            if not (h3 and val):
                continue

            name = h3.get_text(strip=True)
            price = val.get_text(strip=True)

            change_el = li.select_one(".change")
            change = change_el.get_text(strip=True) if change_el else ""

            # 방향 (상승/하락)
            blind_el = li.select_one(".blind")
            direction = ""
            if blind_el:
                blind_text = blind_el.get_text(strip=True)
                # blind에는 이름이 반복되므로 제거
                if "상승" in blind_text or "하락" in blind_text or "보합" in blind_text:
                    for keyword in ["상승", "하락", "보합"]:
                        if keyword in blind_text:
                            direction = keyword
                            break

            item = {
                "name": name,
                "price": price,
                "change": change,
                "direction": direction,
            }

            # 분류
            if any(k in name for k in ["USD", "JPY", "EUR", "CNY", "달러", "유로", "파운드", "인덱스"]):
                exchange.append(item)
            elif any(k in name for k in ["금", "WTI", "휘발유"]):
                commodities.append(item)

        log(f"환율 {len(exchange)}건, 원자재 {len(commodities)}건 수집", "ok")
        return {
            "exchange": exchange,
            "commodities": commodities,
            "crawled_at": datetime.now().isoformat(),
        }


# ─── 가상화폐 ────────────────────────────────────────────────────────────────

class CryptoCrawler:
    """업비트 공개 API로 가상화폐 시세 조회 (API 키 불필요)."""

    TICKER_URL = "https://api.upbit.com/v1/ticker"

    def fetch(self, markets: Optional[list[str]] = None) -> list[dict]:
        """가상화폐 현재가 조회.

        Args:
            markets: 마켓 코드 목록 (예: ["KRW-BTC", "KRW-ETH"])

        Returns:
            list of {market, name, price, change_rate, change_price,
                     high, low, volume, crawled_at}
        """
        targets = markets or DEFAULT_COINS
        log(f"가상화폐 시세 조회: {len(targets)}개 코인", "step")

        try:
            resp = requests.get(
                self.TICKER_URL,
                params={"markets": ",".join(targets)},
                timeout=REQUEST_TIMEOUT,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            log(f"업비트 API 요청 실패: {e}", "error")
            return []

        # 코인 한글명 매핑
        coin_names = {
            "KRW-BTC": "비트코인", "KRW-ETH": "이더리움",
            "KRW-XRP": "리플", "KRW-SOL": "솔라나",
            "KRW-DOGE": "도지코인", "KRW-ADA": "에이다",
            "KRW-AVAX": "아발란체", "KRW-DOT": "폴카닷",
            "KRW-MATIC": "폴리곤", "KRW-LINK": "체인링크",
            "KRW-SHIB": "시바이누", "KRW-TRX": "트론",
        }

        items = []
        for t in data:
            market = t["market"]
            items.append({
                "market": market,
                "name": coin_names.get(market, market.split("-")[-1]),
                "price": t["trade_price"],
                "change_rate": round(t["signed_change_rate"] * 100, 2),
                "change_price": t["signed_change_price"],
                "high": t["high_price"],
                "low": t["low_price"],
                "volume": round(t["acc_trade_volume_24h"], 2),
                "crawled_at": datetime.now().isoformat(),
            })

        log(f"가상화폐 {len(items)}개 코인 시세 수집 완료", "ok")
        return items


# ─── 주식 ────────────────────────────────────────────────────────────────────

class StockCrawler:
    """네이버 금융에서 주식 지수 및 개별 종목 시세 크롤링."""

    SISE_URL = "https://finance.naver.com/sise/"
    ITEM_URL = "https://finance.naver.com/item/main.naver"

    def fetch_index(self) -> dict:
        """KOSPI/KOSDAQ 지수 조회.

        Returns:
            {kospi: str, kosdaq: str, crawled_at: str}
        """
        log("주식 지수 조회", "step")

        try:
            resp = requests.get(self.SISE_URL, headers=HEADERS, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
        except Exception as e:
            log(f"지수 조회 실패: {e}", "error")
            return {"kospi": "", "kosdaq": "", "error": str(e)}

        soup = BeautifulSoup(resp.text, "lxml")

        kospi = ""
        kospi_el = soup.select_one("#KOSPI_now")
        if kospi_el:
            kospi = kospi_el.get_text(strip=True)

        kosdaq = ""
        kosdaq_el = soup.select_one("#KOSDAQ_now")
        if kosdaq_el:
            kosdaq = kosdaq_el.get_text(strip=True)

        log(f"KOSPI: {kospi} | KOSDAQ: {kosdaq}", "ok")
        return {
            "kospi": kospi,
            "kosdaq": kosdaq,
            "crawled_at": datetime.now().isoformat(),
        }

    def fetch_stock(self, code: str, name: str = "") -> dict:
        """개별 종목 현재가 조회.

        Args:
            code: 종목 코드 (예: "005930")
            name: 종목명 (로깅용)

        Returns:
            {code, name, price, crawled_at}
        """
        try:
            resp = requests.get(
                self.ITEM_URL,
                params={"code": code},
                headers=HEADERS,
                timeout=REQUEST_TIMEOUT,
            )
            resp.raise_for_status()
        except Exception as e:
            log(f"종목 조회 실패 ({code}): {e}", "error")
            return {"code": code, "name": name, "price": "", "error": str(e)}

        soup = BeautifulSoup(resp.text, "lxml")
        price_el = soup.select_one(".no_today .blind")
        price = price_el.get_text(strip=True) if price_el else ""

        if not name:
            name_el = soup.select_one(".wrap_company h2 a")
            name = name_el.get_text(strip=True) if name_el else code

        return {
            "code": code,
            "name": name,
            "price": price,
            "crawled_at": datetime.now().isoformat(),
        }

    def fetch_popular(self) -> list[dict]:
        """주요 인기 종목 시세 일괄 조회."""
        log(f"주요 종목 {len(POPULAR_STOCKS)}개 시세 조회", "step")
        results = []
        for name, code in POPULAR_STOCKS.items():
            data = self.fetch_stock(code, name)
            results.append(data)
        log(f"종목 시세 {len(results)}건 수집 완료", "ok")
        return results


# ─── 통합 소스 ───────────────────────────────────────────────────────────────

class FinanceSource:
    """경제/금융 정보 통합 수집 클래스.

    모든 데이터를 API 키 없이 HTTP 요청만으로 수집합니다.

    Usage:
        source = FinanceSource()
        data = source.fetch_all()
        print(source.format_summary(data))
    """

    def __init__(self):
        self.exchange = ExchangeRateCrawler()
        self.crypto = CryptoCrawler()
        self.stock = StockCrawler()

    def fetch_all(self) -> dict:
        """전체 금융 데이터 일괄 수집.

        Returns:
            {market: {exchange, commodities}, crypto: [...],
             index: {kospi, kosdaq}, stocks: [...], crawled_at: str}
        """
        log("금융 데이터 통합 수집 시작", "step")

        market = self.exchange.fetch()
        crypto = self.crypto.fetch()
        index = self.stock.fetch_index()

        result = {
            "market": market,
            "crypto": crypto,
            "index": index,
            "crawled_at": datetime.now().isoformat(),
        }

        log("금융 데이터 통합 수집 완료", "ok")
        return result

    def format_summary(self, data: dict) -> str:
        """금융 데이터를 사람이 읽기 좋은 텍스트로 변환."""
        lines = [f"💰 경제/금융 시황 ({datetime.now().strftime('%Y-%m-%d %H:%M')})"]
        lines.append("")

        # 환율
        if data.get("market", {}).get("exchange"):
            lines.append("📊 환율")
            for item in data["market"]["exchange"]:
                lines.append(f"  {item['name']:15s} {item['price']:>12s} ({item['change']})")

        # 원자재
        if data.get("market", {}).get("commodities"):
            lines.append("\n🛢️ 원자재/금")
            for item in data["market"]["commodities"]:
                lines.append(f"  {item['name']:15s} {item['price']:>12s} ({item['change']})")

        # 주식 지수
        if data.get("index"):
            idx = data["index"]
            lines.append(f"\n📈 주식 지수")
            if idx.get("kospi"):
                lines.append(f"  KOSPI:  {idx['kospi']}")
            if idx.get("kosdaq"):
                lines.append(f"  KOSDAQ: {idx['kosdaq']}")

        # 가상화폐
        if data.get("crypto"):
            lines.append("\n🪙 가상화폐")
            for coin in data["crypto"]:
                lines.append(
                    f"  {coin['name']:6s} {coin['price']:>15,.0f}원  "
                    f"({coin['change_rate']:+.2f}%)"
                )

        return "\n".join(lines)

    def format_blog_content(self, data: dict) -> str:
        """블로그 발행용 HTML 콘텐츠 생성."""
        today = datetime.now().strftime("%Y년 %m월 %d일 %H:%M")

        parts = [f"<h2>오늘의 경제/금융 시황 ({today})</h2>"]

        # 환율 테이블
        if data.get("market", {}).get("exchange"):
            parts.append("<h3>환율</h3>")
            parts.append(
                "<table style='border-collapse:collapse; width:100%; max-width:500px;'>"
                "<thead><tr>"
                "<th style='padding:6px; border:1px solid #ddd;'>통화</th>"
                "<th style='padding:6px; border:1px solid #ddd;'>시세</th>"
                "<th style='padding:6px; border:1px solid #ddd;'>변동</th>"
                "</tr></thead><tbody>"
            )
            for item in data["market"]["exchange"]:
                parts.append(
                    f"<tr>"
                    f"<td style='padding:6px; border:1px solid #ddd;'>{item['name']}</td>"
                    f"<td style='padding:6px; border:1px solid #ddd; text-align:right;'>{item['price']}</td>"
                    f"<td style='padding:6px; border:1px solid #ddd; text-align:right;'>{item['change']}</td>"
                    f"</tr>"
                )
            parts.append("</tbody></table>")

        # 원자재 테이블
        if data.get("market", {}).get("commodities"):
            parts.append("<h3>원자재/금</h3>")
            parts.append(
                "<table style='border-collapse:collapse; width:100%; max-width:500px;'>"
                "<thead><tr>"
                "<th style='padding:6px; border:1px solid #ddd;'>품목</th>"
                "<th style='padding:6px; border:1px solid #ddd;'>시세</th>"
                "<th style='padding:6px; border:1px solid #ddd;'>변동</th>"
                "</tr></thead><tbody>"
            )
            for item in data["market"]["commodities"]:
                parts.append(
                    f"<tr>"
                    f"<td style='padding:6px; border:1px solid #ddd;'>{item['name']}</td>"
                    f"<td style='padding:6px; border:1px solid #ddd; text-align:right;'>{item['price']}</td>"
                    f"<td style='padding:6px; border:1px solid #ddd; text-align:right;'>{item['change']}</td>"
                    f"</tr>"
                )
            parts.append("</tbody></table>")

        # 가상화폐 테이블
        if data.get("crypto"):
            parts.append("<h3>가상화폐</h3>")
            parts.append(
                "<table style='border-collapse:collapse; width:100%; max-width:500px;'>"
                "<thead><tr>"
                "<th style='padding:6px; border:1px solid #ddd;'>코인</th>"
                "<th style='padding:6px; border:1px solid #ddd;'>현재가</th>"
                "<th style='padding:6px; border:1px solid #ddd;'>변동률</th>"
                "</tr></thead><tbody>"
            )
            for coin in data["crypto"]:
                color = "#e74c3c" if coin["change_rate"] < 0 else "#2ecc71"
                parts.append(
                    f"<tr>"
                    f"<td style='padding:6px; border:1px solid #ddd;'>{coin['name']}</td>"
                    f"<td style='padding:6px; border:1px solid #ddd; text-align:right;'>"
                    f"{coin['price']:,.0f}원</td>"
                    f"<td style='padding:6px; border:1px solid #ddd; text-align:right; color:{color};'>"
                    f"{coin['change_rate']:+.2f}%</td>"
                    f"</tr>"
                )
            parts.append("</tbody></table>")

        return "\n".join(parts)
