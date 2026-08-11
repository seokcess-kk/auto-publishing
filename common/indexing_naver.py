"""
Naver Search Advisor 색인 제출자 (Playwright 기반)

네이버 서치어드바이저에 URL을 제출한다.
하루 50개 한도. FAIL_MAX_DOCUMENT_COUNT 응답 시 즉시 중단.

Playwright persistent context를 사용해 로그인 세션을 유지한다.
세션 디렉토리: .sessions/naver_searchadvisor_profile/

환경변수:
    NAVER_SEARCHADVISOR_USERNAME   네이버 아이디
    NAVER_SEARCHADVISOR_PASSWORD   네이버 비밀번호

참조:
    00.Old_Source/indexingAPI/네이버색인 스케줄링/
    indexing_schedule_manager_naver(selenium)_자동화_ver8.py
"""
import json
import os
import time
from pathlib import Path
from urllib.parse import urlparse

from common.logger import log


CRAWL_ENDPOINT = "https://searchadvisor.naver.com/api-console/request/crawl"
DAILY_LIMIT = 50
REQUEST_INTERVAL = 10  # 구 코드 INDEXING_WAIT_TIME

_SESSIONS_DIR = Path(__file__).parent.parent / ".sessions" / "naver_searchadvisor_profile"
_HEADLESS = os.getenv("NAVER_INDEXING_HEADLESS", "true").lower() == "true"


def _extract_csrf_and_encid(page) -> tuple:
    """페이지 <script> 태그에서 csrfToken과 enc_id를 추출."""
    try:
        script_content = page.evaluate(
            """() => {
                const scripts = document.querySelectorAll('script');
                for (const s of scripts) {
                    if (s.textContent.includes('csrfToken')) return s.textContent;
                }
                return '';
            }"""
        )
        csrf = script_content.split('csrfToken:"')[-1].split('",')[0] if 'csrfToken:"' in script_content else ""
        enc_id = script_content.split('enc_id:"')[-1].split('",')[0] if 'enc_id:"' in script_content else ""
        return csrf, enc_id
    except Exception as e:
        log(f"[Naver 색인] csrf/enc_id 추출 실패: {e}", "warn")
        return "", ""


def _site_base(url: str) -> str:
    """URL에서 사이트 기본 주소(scheme+host) 추출."""
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}"


def _relative_path(url: str, site: str) -> str:
    """전체 URL에서 사이트 부분을 제거한 상대 경로 반환."""
    return url.replace(site + "/", "").replace(site, "")


def _do_login(page) -> bool:
    """네이버 로그인 수행. 성공 여부 반환."""
    username = os.getenv("NAVER_SEARCHADVISOR_USERNAME", "")
    password = os.getenv("NAVER_SEARCHADVISOR_PASSWORD", "")

    if not username or not password:
        log("[Naver 색인] NAVER_SEARCHADVISOR_USERNAME/PASSWORD 미설정", "error")
        return False

    try:
        page.goto("https://nid.naver.com/nidlogin.login", wait_until="domcontentloaded", timeout=30000)
        time.sleep(2)

        page.fill("#id", username)
        time.sleep(0.5)
        page.fill("#pw", password)
        time.sleep(0.5)

        # 로그인 버튼 — 네이버가 마크업을 바꿨다(2026-08 확인: `.btn_login` 이
        # 사라지고 `#loginBtn_column` / `#loginBtn_row` 반응형 2벌 +
        # class="btn_done"). 레이아웃에 따라 한쪽만 보이므로 순서대로 시도하고,
        # 전부 못 찾으면 비밀번호 필드에서 Enter 로 폼을 제출한다.
        # (셀렉터 하나만 박아두면 다음 개편 때 또 같은 방식으로 조용히 죽는다)
        clicked = ""
        for sel in ("#loginBtn_column", "#loginBtn_row",
                    "button.btn_done:has-text('로그인')", ".btn_login"):
            try:
                el = page.query_selector(sel)
                if el and el.is_visible():
                    el.click()
                    clicked = sel
                    break
            except Exception:
                continue
        if not clicked:
            log("[Naver 색인] 로그인 버튼 미발견 — Enter 로 폼 제출 시도", "warn")
            page.press("#pw", "Enter")
        else:
            log(f"[Naver 색인] 로그인 버튼 클릭: {clicked}", "info")
        time.sleep(3)

        current = page.url
        if "nid.naver.com" in current and "login" in current:
            log("[Naver 색인] 로그인 실패 (캡차 또는 2단계 인증 필요)", "error")
            return False

        log("[Naver 색인] 로그인 성공", "ok")
        return True
    except Exception as e:
        log(f"[Naver 색인] 로그인 예외: {e}", "error")
        return False


def submit_urls(urls: list) -> dict:
    """URL 목록을 Naver Search Advisor에 제출.

    Args:
        urls: 제출할 URL 목록 (최대 DAILY_LIMIT개 권장)

    Returns:
        {url: "ok" | "limit" | "error"} 딕셔너리
    """
    if not urls:
        return {}

    results = {}

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        log("[Naver 색인] playwright 패키지가 없습니다: pip install playwright", "error")
        return {url: "error" for url in urls}

    _SESSIONS_DIR.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=str(_SESSIONS_DIR),
            headless=_HEADLESS,
            args=["--no-sandbox", "--disable-blink-features=AutomationControlled"],
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
        )

        page = context.new_page()

        # 로그인 상태 확인 — 랜딩 페이지("/")는 **비로그인이어도 리다이렉트하지
        # 않는다**. 로그인 링크가 있는 소개 페이지가 그대로 렌더된다. 이 때문에
        # 아래 분기가 한 번도 참이 되지 않아 _do_login 이 호출되지 않았고,
        # 네이버 색인이 64건 연속 조용히 실패했다(2026-08-11 발견).
        # 로그인이 필요한 콘솔 경로로 판정해야 한다.
        _CONSOLE = "https://searchadvisor.naver.com/console/board"
        page.goto(_CONSOLE, wait_until="domcontentloaded", timeout=30000)
        time.sleep(2)

        if "nid.naver.com" in page.url or "login" in page.url.lower():
            log("[Naver 색인] 세션 만료 — 자동 로그인 시도", "info")
            if not _do_login(page):
                log("[Naver 색인] 자동 로그인 실패 — 네이버 색인 전량 미제출. "
                    "python tools/naver_searchadvisor_login.py 로 수동 로그인 필요",
                    "error")
                context.close()
                return {url: "error" for url in urls}
            page.goto(_CONSOLE, wait_until="domcontentloaded", timeout=30000)
            time.sleep(2)
            if "nid.naver.com" in page.url or "login" in page.url.lower():
                log("[Naver 색인] 로그인 후에도 콘솔 접근 불가 — 수동 로그인 필요",
                    "error")
                context.close()
                return {url: "error" for url in urls}

        # URL별 색인 제출
        for idx, url in enumerate(urls[:DAILY_LIMIT], start=1):
            site = _site_base(url)
            document = _relative_path(url, site)
            crawl_page_url = f"https://searchadvisor.naver.com/console/site/request/crawl?site={site}"

            try:
                page.goto(crawl_page_url, wait_until="domcontentloaded", timeout=30000)
                time.sleep(2)

                csrf, enc_id = _extract_csrf_and_encid(page)
                if not csrf or not enc_id:
                    # 첫 URL 부터 실패하면 개별 URL 문제가 아니라 세션 문제다.
                    # warn 으로만 남기면 스케줄러가 stderr 의 [ERROR] 를 못 봐
                    # 전량 실패한 실행을 success 로 기록한다(소프트 실패 사각지대).
                    if idx == 1:
                        log("[Naver 색인] csrf/enc_id 추출 불가 — 로그인 세션 없음. "
                            "네이버 색인 전량 미제출, 수동 로그인 필요", "error")
                        for u in urls[:DAILY_LIMIT]:
                            results.setdefault(u, "error")
                        break
                    log(f"[Naver 색인] {idx}. csrf/enc_id 없음: {url}", "warn")
                    results[url] = "error"
                    continue

                payload = {
                    "user_enc_id": enc_id,
                    "site": site,
                    "document": document,
                    "_csrf": csrf,
                }
                headers = {
                    "authority": "searchadvisor.naver.com",
                    "accept": "application/json, text/plain, */*",
                    "accept-language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
                    "content-type": "application/json;charset=UTF-8",
                    "origin": "https://searchadvisor.naver.com",
                    "referer": crawl_page_url,
                }

                # fetch API로 직접 POST
                response_text = page.evaluate(
                    """([endpoint, hdrs, body]) => {
                        return fetch(endpoint, {
                            method: 'POST',
                            headers: hdrs,
                            body: JSON.stringify(body),
                            credentials: 'include',
                        }).then(r => r.text());
                    }""",
                    [CRAWL_ENDPOINT, headers, payload],
                )

                response = json.loads(response_text)
                message = response.get("message", "")

                if message == "SUCCESS":
                    log(f"[Naver 색인] {idx}. OK: {url}", "ok")
                    results[url] = "ok"
                elif message == "FAIL_MAX_DOCUMENT_COUNT":
                    log(f"[Naver 색인] 일일 한도(50개) 초과 — 중단", "warn")
                    results[url] = "limit"
                    for remaining in urls[idx:]:
                        results[remaining] = "limit"
                    break
                else:
                    log(f"[Naver 색인] {idx}. 실패 [{message}]: {url}", "warn")
                    results[url] = "error"

            except Exception as e:
                log(f"[Naver 색인] {idx}. 예외: {url} — {e}", "error")
                results[url] = "error"

            if idx < len(urls):
                time.sleep(REQUEST_INTERVAL)

        context.close()

    ok_count = sum(1 for s in results.values() if s == "ok")
    log(f"[Naver 색인] 완료: {ok_count}/{len(urls)}건 성공", "step")
    return results
