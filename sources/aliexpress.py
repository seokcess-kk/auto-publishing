"""
알리익스프레스 상품 수집 & 제휴링크 생성 모듈 (Playwright 기반)

알리는 requests 로 검색 시 captcha 페이지로 리다이렉트되므로 실제 브라우저가 필요.
로그인 시 저장한 storage_state.json 을 재사용해 Playwright 컨텍스트를 세운다.

전략:
  1. 상품 검색: ko.aliexpress.com/w/wholesale-{keyword}.html → JS 실행 후
     window._dida_config_._init_data_ JSON 파싱
  2. 제휴 단축링크: portals.aliexpress.com/tools/linkGenerate/generatePromotionLink.htm
     (동일 컨텍스트 쿠키 사용)

환경변수:
  ALIEXPRESS_TRACKING_ID   알리 파트너스 tracking id (기본 "wordpress")
  ALIEXPRESS_HEADLESS      "true" 면 headless 검색 (기본 true)
"""
import json
import os
import pickle
import re
import time
from urllib import parse

from common.logger import log


TRACKING_ID = os.getenv("ALIEXPRESS_TRACKING_ID", "wordpress")

_BASE_DIR     = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DATA_DIR     = os.path.join(_BASE_DIR, "data")
COOKIE_PATH   = os.path.join(_DATA_DIR, "aliexpress_cookies.pkl")
STORAGE_PATH  = os.path.join(_DATA_DIR, "aliexpress_storage.json")

FIXED_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"


# ─── JSON 추출 헬퍼 ─────────────────────────────────────────────────────────

def _extract_init_data(html: str) -> dict | None:
    """window._dida_config_._init_data_ = { ... } 에서 JSON 객체 균형 괄호 추출."""
    m = re.search(r"window\._dida_config_\._init_data_\s*=\s*", html)
    if not m:
        return None
    start = m.end()
    if start >= len(html) or html[start] != "{":
        return None

    depth = 0
    in_str = False
    escape = False
    end_idx = -1
    for i in range(start, len(html)):
        ch = html[i]
        if escape:
            escape = False
            continue
        if ch == "\\":
            escape = True
            continue
        if ch == '"':
            in_str = not in_str
            continue
        if in_str:
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                end_idx = i + 1
                break
    if end_idx < 0:
        return None
    try:
        return json.loads(html[start:end_idx])
    except json.JSONDecodeError:
        return None


def _parse_items(obj: dict, count: int) -> list:
    """_init_data_ JSON → 상품 리스트."""
    try:
        items = obj["data"]["data"]["root"]["fields"]["mods"]["itemList"]["content"]
    except (KeyError, TypeError):
        return []

    products = []
    for item in items[:count]:
        try:
            picture = f"https:{item['image']['imgUrl']}"
        except Exception:
            picture = ""
        try:
            title = item["title"]["seoTitle"]
        except Exception:
            title = ""
        try:
            sales_num = item["trade"]["realTradeCount"]
        except Exception:
            sales_num = ""
        try:
            grade = item["evaluation"]["starRating"]
        except Exception:
            grade = ""
        try:
            price = item["prices"]["salePrice"]["formattedPrice"]
        except Exception:
            price = ""
        try:
            product_id = item["productId"]
            general_link = f"https://ko.aliexpress.com/item/{product_id}.html"
        except Exception:
            general_link = ""

        if not title or not general_link:
            continue
        products.append({
            "name":          title,
            "image":         picture,
            "sales_num":     str(sales_num),
            "rating":        str(grade),
            "price":         price,
            "url":           general_link,
            "affiliate_url": "",
        })
    return products


# ─── 키워드/상품 매칭 검증 ──────────────────────────────────────────────────

def is_keyword_mismatch(keyword: str, products: list, min_match: int = 1) -> bool:
    """상품명에 키워드 substring 매칭이 min_match 미만이면 mismatch (True).

    예: 키워드 '에버랜드' 검색 결과 5개 상품 모두 '코스프레 의상', '마네킹 헤드'
    등으로 키워드가 0개 등장 → mismatch=True. 호출 측이 발행 스킵 또는
    풀에서 제외 결정에 사용.
    """
    kw = (keyword or "").strip().lower()
    if not kw or not products:
        return False
    hits = sum(1 for p in products
                if kw in (p.get("name", "") or "").lower())
    return hits < min_match


# ─── AliexpressSource ──────────────────────────────────────────────────────

class AliexpressSource:
    """알리익스프레스 상품 소스 (Playwright 기반).

    Usage:
        source = AliexpressSource(tracking_id="wordpress")
        products = source.search("무선이어폰", count=10)
        source.close()   # 또는 with 블록

    파이프라인이 여러 키워드를 연속 검색하므로 브라우저 컨텍스트는 재사용한다.
    """

    def __init__(self, tracking_id: str = "",
                 link_interval: float = 0.5,
                 headless: bool | None = None):
        self.tracking_id   = tracking_id or TRACKING_ID
        self.link_interval = link_interval
        # headless 기본값: 환경변수 또는 True (검색 시점에는 headless OK)
        if headless is None:
            env = os.getenv("ALIEXPRESS_HEADLESS", "true").lower()
            headless = env == "true"
        self.headless = headless

        self._pw = None
        self._browser = None
        self._context = None
        self._page = None
        self._session_reset_done = False  # 세션 초기화 1회만

    # Context manager 지원
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def _ensure_browser(self) -> bool:
        """Playwright 브라우저/컨텍스트/페이지 보장."""
        if self._page is not None:
            return True

        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            log("playwright 미설치: pip install playwright && playwright install chromium", "error")
            return False

        # storage_state.json 우선, 없으면 cookies.pkl 에서 재구성
        storage = None
        if os.path.exists(STORAGE_PATH) and os.path.getsize(STORAGE_PATH) > 0:
            storage = STORAGE_PATH
        elif os.path.exists(COOKIE_PATH) and os.path.getsize(COOKIE_PATH) > 0:
            log("storage_state.json 없음 — cookies.pkl 재로그인 필요", "warn")
            if not self._relogin():
                return False
            storage = STORAGE_PATH if os.path.exists(STORAGE_PATH) else None
        else:
            log("알리 세션 파일 없음 — 로그인 시작", "step")
            if not self._relogin():
                return False
            storage = STORAGE_PATH if os.path.exists(STORAGE_PATH) else None

        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(
            headless=self.headless,
            args=["--disable-blink-features=AutomationControlled"],
        )
        self._context = self._browser.new_context(
            user_agent=FIXED_UA,
            locale="ko-KR",
            storage_state=storage,
        )

        # playwright-stealth 로 봇 탐지 우회 (navigator.webdriver 등 패치)
        try:
            from playwright_stealth import Stealth
            Stealth().apply_stealth_sync(self._context)
            log("playwright-stealth 적용 완료", "info")
        except ImportError:
            log("playwright-stealth 미설치 — 탐지 위험 증가", "warn")
        except Exception as e:
            log(f"stealth 적용 실패: {e}", "warn")

        self._page = self._context.new_page()
        return True

    def _relogin(self) -> bool:
        """자동 재로그인 비활성화 (2026-05-29).

        제휴(Partners) 계정은 Google 계정(assagaori00@gmail.com)인데,
        common.aliexpress_login 의 자동 로그인은 Kakao SSO 라 제휴 미가입
        naver 구매자 계정(ae712475)으로 들어가 유효 세션을 잘못된 계정으로
        덮어쓴다. 따라서 자동 재로그인 대신 수동 Google 로그인을 안내만 한다.
        세션 갱신: python tools/aliexpress_manual_login.py → 'Continue with Google'.
        """
        log("알리 자동 재로그인 비활성화 — 수동 Google 로그인 필요", "warn")
        try:
            from common.notifier import notify_login_required
            notify_login_required(
                "알리익스프레스 (제휴=Google 계정)",
                "python tools/aliexpress_manual_login.py → 'Continue with Google' 로 로그인",
            )
        except Exception:
            pass
        return False

    def _is_captcha_page(self) -> bool:
        """현재 페이지가 captcha / punish 페이지인지 감지."""
        try:
            title = (self._page.title() or "").lower()
            url = (self._page.url or "").lower()
            if "captcha" in title or "punish" in url or "_____tmd_____" in url:
                return True
            # nc_1_n1z 같은 슬라이더 captcha 요소 감지
            has_nc = self._page.evaluate(
                "() => !!document.querySelector('[id^=\"nc_\"], .nc-container, #baxia-dialog')"
            )
            return bool(has_nc)
        except Exception:
            return False

    def _wait_for_captcha_solve(self, wait_sec: int = 180) -> bool:
        """사용자가 수동으로 captcha 해결할 때까지 대기. 해결 시 True.

        슬라이더 해결 후 알리가 자동으로 원래 URL로 리다이렉트하므로
        _dida_config_ 가 로드될 때까지 같은 페이지에서 기다린다.
        재접속은 하지 않음 (즉시 재접속 시 HTTP 에러 발생).
        """
        log(f"⚠️  Captcha 감지 — {wait_sec}초 내에 브라우저에서 슬라이더를 왼쪽→오른쪽으로 밀어주세요", "warn")
        log(f"   현재 URL: {self._page.url}", "info")
        deadline = time.time() + wait_sec
        while time.time() < deadline:
            time.sleep(3)
            if self._is_captcha_page():
                continue
            # punish URL 벗어남 — 검색 페이지 로드 대기
            log("Captcha 해결 확인 — 검색 페이지 로드 대기 중", "ok")
            try:
                self._page.wait_for_function(
                    "() => window._dida_config_ && window._dida_config_._init_data_",
                    timeout=20000,
                )
                log("검색 페이지 로드 완료", "ok")
            except Exception:
                log("검색 데이터 로드 미확인 — HTML 파싱으로 시도", "warn")
            # 세션 상태 저장 (다음 실행 시 재사용)
            try:
                self._context.storage_state(path=STORAGE_PATH)
                log("storage_state 갱신 저장", "ok")
            except Exception:
                pass
            return True
        log("Captcha 대기 시간 초과", "error")
        return False

    def _warmup_session(self) -> None:
        """홈 → 카테고리 순으로 자연스러운 세션 생성 (HTTP 에러 우회)."""
        for warmup_url in ("https://www.aliexpress.com/", "https://ko.aliexpress.com/"):
            try:
                self._page.goto(warmup_url, timeout=20000, wait_until="domcontentloaded")
                time.sleep(2)
                if self._is_captcha_page():
                    log(f"Warmup captcha 감지 ({warmup_url}) — 수동 해결 필요", "warn")
                    if not self.headless:
                        self._wait_for_captcha_solve()
                    return
                log(f"Warmup OK: {warmup_url}", "info")
                return
            except Exception as e:
                log(f"Warmup 실패 ({warmup_url}): {e}", "warn")
                continue

    def _reset_session(self, wipe_storage: bool = True) -> bool:
        """세션 초기화 요청 — 자동 재로그인 비활성화 후로는 storage 를 지우지 않는다.

        과거엔 5xx 지속 시 storage 를 삭제하고 자동 재로그인했지만, 자동 로그인이
        잘못된 계정으로 들어가는 문제(_relogin 주석 참고) 때문에 유효 세션을
        날리지 않고 수동 Google 로그인 안내만 한다. wipe_storage 인자는 호환용으로
        남기되 사용하지 않는다.
        """
        log("세션 초기화 요청 — 자동 재로그인 비활성, 수동 로그인 안내만 수행", "warn")
        self.close()
        self._session_reset_done = True
        return self._relogin()  # notify + False, storage 보존

    def _goto_with_retry(self, url: str, retries: int = 2) -> bool:
        """page.goto() 재시도 래퍼. HTTP 에러 시 warmup → 세션 초기화 순으로 복구."""
        for attempt in range(retries + 1):
            try:
                self._page.goto(url, timeout=30000, wait_until="domcontentloaded")
                return True
            except Exception as e:
                msg = str(e)
                log(f"goto 실패 [{attempt+1}/{retries+1}]: {msg[:120]}", "warn")
                if "ERR_HTTP_RESPONSE_CODE_FAILURE" not in msg or attempt >= retries:
                    return False
                # 1차: warmup 으로 복구 시도
                if attempt == 0:
                    log("Warmup 후 재시도", "info")
                    self._warmup_session()
                    time.sleep(3)
                    continue
                # 2차: 세션 초기화 (1회만)
                if not self._session_reset_done:
                    if not self._reset_session():
                        return False
                    time.sleep(3)
                    continue
                return False
        return False

    def _parse_cards_from_dom(self, count: int) -> list:
        """렌더된 DOM 에서 `.search-item-card-wrapper-gallery` 카드를 파싱.

        알리 UI 업데이트 후 _init_data_ 의 itemList 필드가 비어서 전달되므로
        클라이언트 렌더 결과인 DOM 을 직접 추출한다.
        """
        # 스크롤로 lazy-load 유도
        try:
            self._page.evaluate("window.scrollTo(0, 1500)")
            time.sleep(2)
        except Exception:
            pass

        raw = self._page.evaluate("""
            (maxCount) => {
                const cards = Array.from(document.querySelectorAll('.search-item-card-wrapper-gallery'));
                const out = [];
                for (const card of cards.slice(0, maxCount * 2)) {
                    const link = card.querySelector('a.search-card-item') || card.querySelector('a[href*="/item/"]');
                    const img = card.querySelector('img');
                    const text = (card.innerText || '').trim();
                    if (!link || !text) continue;
                    const hrefRaw = link.getAttribute('href') || '';
                    // href 가 //ko.aliexpress.com/... 또는 /item/... 형태
                    let href = hrefRaw;
                    if (href.startsWith('//')) href = 'https:' + href;
                    else if (href.startsWith('/')) href = 'https://ko.aliexpress.com' + href;
                    // productId 추출
                    const pidMatch = href.match(/\\/item\\/(\\d+)\\.html/);
                    const productId = pidMatch ? pidMatch[1] : '';
                    out.push({
                        productId,
                        href,
                        img: img?.getAttribute('src') || '',
                        text,
                    });
                }
                return out;
            }
        """, count)

        products = []
        for r in raw:
            text = r.get("text", "")
            lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
            if not lines or not r.get("productId"):
                continue

            name = lines[0]
            # 가격: ₩ 로 시작하는 첫 줄
            price = ""
            rating = ""
            sales_num = ""
            for ln in lines[1:]:
                if not price and (ln.startswith("₩") or ln.startswith("$")):
                    price = ln
                    continue
                if not rating and re.match(r"^\d\.\d$", ln):
                    rating = ln
                    continue
                if not sales_num and "판매" in ln:
                    # "5,000+ 판매" → "5,000+"
                    sales_num = ln.replace("판매", "").strip()

            img = r.get("img", "")
            if img.startswith("//"):
                img = "https:" + img

            general_link = f"https://ko.aliexpress.com/item/{r['productId']}.html"

            products.append({
                "name":          name,
                "image":         img,
                "sales_num":     sales_num,
                "rating":        rating,
                "price":         price,
                "url":           general_link,
                "affiliate_url": "",
            })
            if len(products) >= count:
                break

        return products

    def _search_products(self, keyword: str, count: int) -> list:
        """Playwright 로 검색 페이지 열고 DOM 에서 상품 카드 파싱."""
        if not self._ensure_browser():
            return []

        url = f"https://ko.aliexpress.com/w/wholesale-{parse.quote(keyword)}.html"
        try:
            if not self._goto_with_retry(url):
                log(f"검색 페이지 접속 실패: {keyword}", "warn")
                return []

            # captcha 감지 → 수동 해결 대기 (headful 에서만 의미 있음)
            time.sleep(2)
            if self._is_captcha_page():
                if self.headless:
                    log(f"Captcha 차단 (headless): {keyword}", "warn")
                    return []
                if not self._wait_for_captcha_solve():
                    return []

            # 카드 렌더 대기
            try:
                self._page.wait_for_selector(
                    ".search-item-card-wrapper-gallery",
                    timeout=15000,
                )
            except Exception:
                log(f"상품 카드 렌더 미확인: {keyword}", "warn")

            # 파싱 — 알리는 anti-bot 으로 카드 없는 빈 페이지를 간헐적으로 준다
            # (captcha 도 없이). 같은 페이지 재파싱은 소용없으므로, 0개면 홈
            # 워밍업으로 세션을 "사람처럼" 만든 뒤 검색 URL 을 재네비게이션한다.
            products = self._parse_cards_from_dom(count)
            for attempt in range(1, 3):  # 최대 2회 재네비게이션
                if products or self._is_captcha_page():
                    break
                log(f"검색 0개 — 워밍업 후 재시도 {attempt}/2: {keyword}", "info")
                self._warmup_session()       # 홈 방문으로 세션 워밍
                time.sleep(1)
                if not self._goto_with_retry(url):
                    continue
                time.sleep(2)
                if self._is_captcha_page():
                    break
                try:
                    self._page.wait_for_selector(
                        ".search-item-card-wrapper-gallery", timeout=12000)
                except Exception:
                    pass
                products = self._parse_cards_from_dom(count)

            log(f"알리 검색 완료: {len(products)}개 ({keyword})", "ok")
            return products
        except Exception as e:
            log(f"알리 검색 오류 ({keyword}): {e}", "warn")
            return []

    def _shorten_link(self, general_link: str) -> str:
        """알리 파트너스 API 로 단축 제휴링크 생성 (브라우저 컨텍스트 사용)."""
        if not self._ensure_browser():
            return ""
        url = "https://portals.aliexpress.com/tools/linkGenerate/generatePromotionLink.htm"
        params = {"trackId": self.tracking_id, "targetUrl": general_link}
        full = f"{url}?{parse.urlencode(params)}"

        try:
            # APIRequestContext 는 context 의 쿠키를 그대로 사용
            res = self._context.request.get(
                full,
                headers={
                    "accept": "application/json, text/plain, */*",
                    "referer": "https://portals.aliexpress.com/affiportals/web/link_generator.htm",
                    "user-agent": FIXED_UA,
                },
                timeout=15000,
            )
            if not res.ok:
                return ""
            # 세션 만료 시 JSON 대신 HTML 로그인 페이지가 반환됨
            body = res.text()
            if not body.strip().startswith("{"):
                if "login" in body.lower() or "sign" in body.lower():
                    # storage 를 지우지 않는다 — 유효할 수도 있는 세션을 보존하고
                    # (검색은 비로그인으로도 됨) 수동 Google 재로그인만 안내한다.
                    log("알리 링크 생성 — 제휴 세션 만료/미가입 감지, 수동 Google 로그인 필요", "warn")
                    try:
                        from common.notifier import notify_login_required
                        notify_login_required(
                            "알리익스프레스 (제휴=Google 계정)",
                            "python tools/aliexpress_manual_login.py → 'Continue with Google' 로 로그인",
                        )
                    except Exception:
                        pass
                return ""
            data = json.loads(body)
            aff = data.get("data", "")
            if isinstance(aff, str) and aff.startswith("https://s.click.aliexpress.com"):
                return aff
            return ""
        except Exception as e:
            log(f"알리 링크 생성 오류: {e}", "warn")
            return ""

    def search(self, keyword: str, count: int = 10,
               require_affiliate: bool = True,
               min_keyword_match: "int | None" = None) -> list:
        """검색 + 제휴링크 생성.

        min_keyword_match: 검색 결과 N개 중 상품명에 키워드 substring 매칭이
            이만큼 있어야 발행 가치 있음으로 판단. 미달 시 빈 결과 반환 —
            한국 고유명사 등 알리에 적합하지 않은 키워드의 잡상품 발행 차단.
            0 으로 두면 검증 비활성. None 이면 ALIEXPRESS_MIN_KEYWORD_MATCH
            환경변수(기본 0) 사용.
        """
        if min_keyword_match is None:
            try:
                min_keyword_match = int(os.getenv("ALIEXPRESS_MIN_KEYWORD_MATCH", "0"))
            except ValueError:
                min_keyword_match = 0

        log(f"알리 검색 (tracking={self.tracking_id}): {keyword}", "step")

        products = self._search_products(keyword, count=count * 2)  # 여유분
        if not products:
            return []

        # 키워드/상품 매칭 검증 — 알리는 한국 고유명사에 매칭되는 상품이 없어
        # 잡상품을 반환하는 케이스가 흔함. 발행 전에 차단.
        if min_keyword_match > 0 and is_keyword_mismatch(keyword, products,
                                                          min_match=min_keyword_match):
            hits = sum(1 for p in products
                        if (keyword or "").strip().lower()
                        in (p.get("name", "") or "").lower())
            log(f"키워드 '{keyword}' 매칭 부족 ({hits}/{len(products)}) — "
                f"발행 가치 낮음, 빈 결과 반환", "warn")
            return []

        if not require_affiliate:
            return products[:count]

        result = []
        for p in products:
            aff = self._shorten_link(p["url"])
            if aff:
                p["affiliate_url"] = aff
                result.append(p)
                if len(result) >= count:
                    break
            time.sleep(self.link_interval)

        log(f"알리 제휴링크 생성: {len(result)}/{count}", "ok")
        return result

    def close(self):
        """브라우저/Playwright 종료."""
        try:
            if self._context:
                self._context.close()
        except Exception:
            pass
        try:
            if self._browser:
                self._browser.close()
        except Exception:
            pass
        try:
            if self._pw:
                self._pw.stop()
        except Exception:
            pass
        self._pw = self._browser = self._context = self._page = None
