"""
쿠팡 상품 수집 모듈

크롤링 전략 (우선순위):
  1. 로컬 크롬 모바일 모드 (CDP 연결) — 실제 브라우저 사용, WAF 우회
  2. 쿠팡 파트너스 API (HMAC, 옵션)  — API키 있을 때만 동작

파트너스 링크 생성:
  - AF코드 + CHANNELID 기반 makeDirectPartnersLink (API 불필요)
  - 크롤링으로 가격, 할인율, 도착시간, 평점, 리뷰수, 이미지 등 수집

참조:
  00.Old_Source/wordpress/old/
  wordpress(api)_categories(api)_naverlab_itemscoute_coopang(landingUrl)_ver7.py
"""
import os
import re
import sys
import time
import random
import subprocess
import tempfile
import hashlib
import hmac as _hmac
from urllib import parse
from urllib.parse import urlparse
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup

from common.logger import log


# ─── 환경변수 ────────────────────────────────────────────────────────────────

ACCESS_KEY = os.getenv("COUPANG_ACCESS_KEY", "")
SECRET_KEY = os.getenv("COUPANG_SECRET_KEY", "")
AF_CODE    = os.getenv("COUPANG_AF_CODE", "")
CHANNEL_ID = os.getenv("COUPANG_CHANNEL_ID", "")
FAKE_LINK  = os.getenv("COUPANG_FAKE_LINK", "")

def _resolve_chrome_path() -> str:
    env = os.getenv("CHROME_PATH", "").strip()
    if env and os.path.exists(env):
        return env
    if sys.platform == "darwin":
        return "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    if sys.platform == "win32":
        candidates = [
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
            os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
        ]
        for p in candidates:
            if os.path.exists(p):
                return p
    return "google-chrome"


CHROME_PATH = _resolve_chrome_path()
CDP_PORT    = 9222

# 모바일 에뮬레이션 설정 (Galaxy S21 기준)
# ※ 2026-05 기준 모바일 UA(Galaxy)를 Windows Chrome에 강제하면 Akamai 가
#    sec-ch-ua-platform 불일치를 잡아 'Access Denied' 를 반환한다 (HTML 301B).
#    그래서 _start_chrome_mobile() 은 실제로 모바일 UA 를 강제하지 않고,
#    Chrome 의 데스크톱 기본 UA + 자연스러운 sec-ch-ua 헤더로 접근한다.
#    상품 카드 셀렉터(ProductUnit_productUnit) 는 데스크톱/모바일이 동일하다.
MOBILE_UA = (
    "Mozilla/5.0 (Linux; Android 13; SM-G991B) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Mobile Safari/537.36"
)
MOBILE_WIDTH  = 390
MOBILE_HEIGHT = 844


# ─── 파트너스 직접링크 ────────────────────────────────────────────────────────

def _get_page_key(url: str) -> str:
    m = re.search(r"/products/(\d+)", url)
    return m.group(1) if m else "0"

def _get_product_type(url: str) -> str:
    return "AFFSDP" if "/vp/" in url else "AFFTDP"

def _get_query_val(key: str, url: str):
    return parse.parse_qs(urlparse(url).query).get(key, [None])[0]

def make_partners_link(product_url: str, channel_id: str = "") -> str:
    """AF코드 + CHANNELID 기반 파트너스 링크 (API 불필요).

    channel_id 가 비어 있으면 환경변수(COUPANG_CHANNEL_ID) 기본값 사용.
    파이프라인별 채널(쿠팡 파트너스 채널 아이디 관리와 대응)을 주입하려면
    CoupangSource(channel_id=...) 또는 직접 인자 전달.
    """
    ptype     = _get_product_type(product_url)
    page_key  = _get_page_key(product_url)
    item_id   = _get_query_val("itemId", product_url)
    vendor_id = _get_query_val("vendorItemId", product_url)
    cid       = channel_id or CHANNEL_ID
    return (
        f"https://link.coupang.com/re/{ptype}"
        f"?lptag={AF_CODE}&subid={cid}"
        f"&pageKey={page_key}&traceid=V0-153"
        f"&itemId={item_id}&vendorItemId={vendor_id}"
    )


# ─── 로컬 크롬 모바일 모드 (CDP) ─────────────────────────────────────────────

def _start_chrome_mobile(user_data_dir: str = "") -> subprocess.Popen:
    """로컬 크롬을 원격 디버깅 모드로 실행 (Akamai 우회 위해 데스크톱 UA 사용)."""
    if not user_data_dir:
        user_data_dir = os.path.join(tempfile.gettempdir(), "coupang_chrome")
    os.makedirs(user_data_dir, exist_ok=True)
    cmd = [
        CHROME_PATH,
        f"--remote-debugging-port={CDP_PORT}",
        f"--user-data-dir={user_data_dir}",
        # headless / 모바일 UA / 모바일 viewport 강제는 Akamai 가 차단한다.
        # 실제 데스크톱 Chrome 처럼 떠야 sec-ch-ua / sec-ch-ua-platform 일치.
        "--no-sandbox",
        "--disable-blink-features=AutomationControlled",
        "--disable-infobars",
        "--disable-extensions",
    ]
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(2)  # 크롬 기동 대기
    log(f"크롬 데스크톱 모드 실행 (PID {proc.pid})", "info")
    return proc


def _crawl_with_local_chrome(keyword: str, count: int = 10,
                             channel_id: str = "") -> list:
    """쿠팡 검색 크롤링.

    COUPANG_BRIGHTDATA_WSS 가 설정돼 있으면 Bright Data Scraping Browser
    (Akamai 우회 인프라) 에 CDP 로 연결하고, 없으면 로컬 크롬을 띄워 연결한다.
    Akamai 가 로컬 크롬 검색을 'Access Denied(403)' 로 막을 때 Bright Data 경로로
    우회한다. 추후 파트너스 API 승인 시 COUPANG_USE_API_FIRST=true 로 API 우선.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        log("playwright 미설치: pip install playwright", "warn")
        return []

    bd_wss = os.getenv("COUPANG_BRIGHTDATA_WSS", "").strip()
    proc = None
    products = []

    try:
        if not bd_wss:
            proc = _start_chrome_mobile()
        with sync_playwright() as p:
            if bd_wss:
                log("쿠팡: Bright Data Scraping Browser 연결 (Akamai 우회)", "info")
                browser = p.chromium.connect_over_cdp(bd_wss)
            else:
                # 로컬 크롬에 CDP 로 연결
                browser = p.chromium.connect_over_cdp(f"http://127.0.0.1:{CDP_PORT}")
            context = browser.contexts[0] if browser.contexts else browser.new_context()
            page    = context.new_page()

            # 데스크톱 모드로 동작 — Chrome 기본 viewport / UA / sec-ch-ua 그대로 사용.
            # (모바일 viewport 강제는 Akamai 차단 트리거)
            # Bright Data Browser API 는 헤더를 직접 관리하므로 override 가 금지된다
            # ('Overriding Accept-Language headers forbidden'). 로컬 크롬에서만 설정.
            if not bd_wss:
                page.set_extra_http_headers({
                    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8",
                })

            # 쿠팡 메인 먼저 방문 (쿠키/세션 획득)
            log("쿠팡 메인 방문...", "info")
            page.goto("https://www.coupang.com", timeout=20000, wait_until="domcontentloaded")
            time.sleep(random.uniform(1.5, 2.5))

            # 검색 페이지 이동
            encoded_kw = parse.quote(keyword)
            log(f"쿠팡 검색 페이지 이동: {keyword}", "info")
            resp = page.goto(
                f"https://www.coupang.com/np/search?component=&q={encoded_kw}&channel=user",
                timeout=20000,
                wait_until="domcontentloaded",
            )
            log(f"응답 코드: {resp.status if resp else 'N/A'}", "info")

            # React 하이드레이션이 끝날 때까지 상품 카드 셀렉터를 명시적으로 기다린다.
            # (이전엔 scroll 을 즉시 호출해 hydration 중 context 가 파괴됐음)
            try:
                page.wait_for_selector(
                    'li[class*="ProductUnit_productUnit"]',
                    timeout=15000,
                )
            except Exception as e:
                log(f"상품 카드 대기 timeout (계속 진행): {e}", "info")

            html = page.content()
            page.close()

        products = _parse_coupang_html(html, keyword, count, channel_id=channel_id)

    except Exception as e:
        log(f"크롬 CDP 크롤링 오류: {e}", "error")
    finally:
        if proc is not None:
            proc.terminate()
            time.sleep(0.5)

    return products


def _parse_coupang_html(html: str, keyword: str, count: int,
                        channel_id: str = "") -> list:
    """쿠팡 검색 결과 HTML 파싱.

    모바일 크롬 렌더링 기준 셀렉터 (2024~):
      li[class*=ProductUnit_productUnit]
      → 이름: [class*=ProductUnit_productName]
      → 가격: [class*=PriceArea_priceArea]
      → 이미지: figure[class*=ProductUnit_productImage] img
      → 링크: a.impression-logged (href)
    """
    soup = BeautifulSoup(html, "html.parser")

    # 신규 모바일 셀렉터
    items = soup.select('li[class*="ProductUnit_productUnit"]')

    # 구버전 PC 셀렉터 폴백
    if not items:
        items = soup.select("li.search-product")

    if not items:
        log(f"HTML {len(html)}B / 상품 셀렉터 없음", "warn")
        return []

    log(f"파싱 대상: {len(items)}개 상품", "info")

    products = []
    for item in items[:count]:
        # 링크 & URL
        link_el = item.select_one("a[href]")
        href    = link_el.get("href", "") if link_el else ""
        if href and href.startswith("/"):
            href = "https://www.coupang.com" + href

        # 상품명
        name_el = (item.select_one('[class*="ProductUnit_productNameV2"]')
                   or item.select_one('[class*="ProductUnit_productName"]')
                   or item.select_one("div.name"))
        name = name_el.get_text(strip=True) if name_el else "No data"

        # 가격 영역 전체 텍스트에서 파싱
        price_area = item.select_one('[class*="PriceArea_priceArea"]')
        price_text = price_area.get_text(" ", strip=True) if price_area else ""

        # 할인율 (예: "56%")
        discount_m = re.search(r'(\d+)%', price_text)
        discount_rate = discount_m.group(0) if discount_m else ""

        # 실제 가격 (숫자,숫자원 패턴 중 마지막)
        prices_found = re.findall(r'([\d,]+)원', price_text)
        # 가장 낮은 숫자 = 실제 판매가
        if prices_found:
            price = min(prices_found, key=lambda x: int(x.replace(",", ""))) + "원"
        else:
            price = ""

        # 이미지
        img_el    = item.select_one('figure[class*="ProductUnit_productImage"] img') or item.select_one("img")
        image_url = ""
        if img_el:
            raw = img_el.get("src") or img_el.get("data-src", "")
            image_url = f"https:{raw}" if raw.startswith("//") else raw

        # 평점 & 리뷰 (예: "(1,513)" → "1,513")
        rating_el = item.select_one('[class*="ProductRating_productRating"]')
        review_count = ""
        if rating_el:
            raw_rating = rating_el.get_text(strip=True)          # "(1,513)"
            review_count = re.sub(r"[()]", "", raw_rating)       # "1,513"
        rating = ""  # 별점 숫자는 CSS fill로 표현되어 텍스트 추출 불가

        # 배송 도착 정보
        arrive_el = (item.select_one('[class*="arrival"]')
                     or item.select_one('[class*="delivery"]'))
        arrival_time = arrive_el.get_text(strip=True) if arrive_el else ""

        # 파트너스 링크 (파이프라인별 channel_id 오버라이드 지원)
        aff_url = make_partners_link(href, channel_id=channel_id) if href else FAKE_LINK
        if not href or "pageKey=0" in aff_url:
            aff_url = FAKE_LINK

        products.append({
            "name":          name,
            "price":         price,
            "discount_rate": discount_rate,
            "arrival_time":  arrival_time,
            "rating":        rating,
            "review_count":  review_count,
            "image":         image_url,
            "url":           href,
            "affiliate_url": aff_url,
        })

    log(f"쿠팡 파싱 완료: {len(products)}개", "ok")
    return products


# ─── 파트너스 API (옵션) ──────────────────────────────────────────────────────

def _api_search(keyword: str, count: int = 10,
                channel_id: str = "") -> list:
    """쿠팡 파트너스 API 검색 (ACCESS_KEY/SECRET_KEY 필요)."""
    if not ACCESS_KEY or not SECRET_KEY:
        return []

    cid   = channel_id or CHANNEL_ID
    path  = "/v2/providers/affiliate_open_api/apis/openapi/v1/products/search"
    query = f"keyword={parse.quote(keyword)}&limit={count}&subId={cid}"
    dt    = datetime.now(timezone.utc).strftime("%y%m%dT%H%M%SZ")
    msg   = dt + "GET" + path + query
    sig   = _hmac.new(SECRET_KEY.encode(), msg.encode(), hashlib.sha256).hexdigest()
    auth  = f"CEA algorithm=HmacSHA256, access-key={ACCESS_KEY}, signed-date={dt}, signature={sig}"

    try:
        res = requests.get(
            f"https://api-gateway.coupang.com{path}?{query}",
            headers={"Authorization": auth, "Content-Type": "application/json;charset=UTF-8"},
            timeout=15,
        )
        if not res.ok:
            log(f"파트너스 API 실패: {res.status_code}", "warn")
            return []
        items = res.json().get("data", {}).get("productData", [])
        products = []
        for item in items[:count]:
            products.append({
                "name":          item.get("productName", ""),
                "price":         f"{item.get('productPrice', '')}원",
                "discount_rate": "",
                "arrival_time":  "",
                "rating":        str(item.get("productRating", "")),
                "review_count":  str(item.get("productReviewCount", "0")),
                "image":         item.get("productImage", ""),
                "url":           item.get("productUrl", ""),
                "affiliate_url": item.get("shortenUrl") or make_partners_link(item.get("productUrl", ""), channel_id=channel_id),
            })
        log(f"파트너스 API 결과: {len(products)}개", "ok")
        return products
    except Exception as e:
        log(f"파트너스 API 오류: {e}", "warn")
        return []


# ─── CoupangSource 클래스 ─────────────────────────────────────────────────────

class CoupangSource:
    """쿠팡 상품 소스.

    우선순위:
      1. 로컬 크롬 모바일 모드 CDP 크롤링 (실제 브라우저, WAF 우회)
      2. 파트너스 API (ACCESS_KEY/SECRET_KEY 있을 때, 옵션)
    """

    def __init__(self, access_key: str = "", secret_key: str = "",
                 use_api_first: "bool | None" = None,
                 channel_id: str = ""):
        """
        Args:
            access_key:    쿠팡 파트너스 API 키 (옵션)
            secret_key:    쿠팡 파트너스 시크릿 키 (옵션)
            use_api_first: True 면 API 먼저 시도, False(기본) 면 크롬 크롤링 먼저
            channel_id:    쿠팡 파트너스 채널 ID (파이프라인별 오버라이드).
                           비어 있으면 COUPANG_CHANNEL_ID 환경변수 사용.
                           본인이 쿠팡 파트너스 대시보드에서 생성한 채널 ID 를 입력.
        """
        global ACCESS_KEY, SECRET_KEY
        if access_key:
            ACCESS_KEY = access_key
        if secret_key:
            SECRET_KEY = secret_key
        # 미지정 시 env 로 결정 — 파트너스 API 승인 후 COUPANG_USE_API_FIRST=true
        # 한 줄로 크롤링(Bright Data) → API 우선 전환 가능 (코드 수정 불필요).
        if use_api_first is None:
            use_api_first = os.getenv("COUPANG_USE_API_FIRST", "false").lower() == "true"
        self.use_api_first = use_api_first
        self.channel_id    = channel_id or CHANNEL_ID

    def search(self, keyword: str, count: int = 10) -> list:
        """상품 검색. 크롬 크롤링 → API 순으로 시도."""
        log(f"쿠팡 검색: {keyword} (channel={self.channel_id})", "step")

        if self.use_api_first:
            products = _api_search(keyword, count, channel_id=self.channel_id)
            if products:
                return products

        # 기본: 로컬 크롬 모바일 모드 크롤링
        products = _crawl_with_local_chrome(keyword, count, channel_id=self.channel_id)
        if products:
            return products

        # 폴백: 파트너스 API
        if not self.use_api_first:
            log("크롤링 실패, 파트너스 API 폴백 시도", "warn")
            products = _api_search(keyword, count, channel_id=self.channel_id)

        return products

    def search_with_links(self, keyword: str, count: int = 10) -> list:
        return self.search(keyword, count)

    def get_affiliate_link(self, product_url: str) -> str:
        return make_partners_link(product_url, channel_id=self.channel_id)
