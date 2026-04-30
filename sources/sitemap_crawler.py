"""
WordPress/Tistory/Blogger URL 수집 모듈 (백링크 소스)

수집 전략:
  1) Jetpack/Yoast/RankMath sitemap 순회
       GET {WP_URL}/sitemap-{n}.xml           (Jetpack)
       GET {WP_URL}/post-sitemap{n}.xml       (Yoast/RankMath)
       GET {WP_URL}/wp-sitemap.xml            (WordPress Core)
  2) WordPress REST API 최신 글
       GET {WP_URL}/wp-json/wp/v2/posts?per_page=100&page=N

반환 스키마:
  [{"url": str, "title": str, "published": str|None, "source": "sitemap|rest"}]

참조:
  wordpress_twitter_auto_backlink/ch05_semi_final/
  00.Old_Source/backlink/backlink_tistory_wordpress_naver_link_upload_ver8.py
"""
import re
from typing import Iterable, Optional

import requests

from common.logger import log


FIXED_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)
LOC_PATTERN = re.compile(r"<loc>(.*?)</loc>", re.IGNORECASE | re.DOTALL)
TIMEOUT = 15


def _normalize_base(url: str) -> str:
    return url.rstrip("/")


def _http_get(url: str, timeout: int = TIMEOUT) -> Optional[requests.Response]:
    try:
        resp = requests.get(
            url,
            headers={"User-Agent": FIXED_UA, "Accept": "text/xml,application/xml,*/*"},
            timeout=timeout,
        )
        return resp
    except Exception as e:
        log(f"요청 실패: {url} ({e})", "warn")
        return None


# ─── Jetpack style (sitemap-1.xml, sitemap-2.xml, ...) ─────────────────────────

def fetch_jetpack_sitemap(base_url: str, max_pages: int = 100) -> list:
    """Jetpack 플러그인 sitemap 순회. 2xx 응답이 끊길 때까지."""
    base = _normalize_base(base_url)
    urls = []
    for idx in range(1, max_pages + 1):
        resp = _http_get(f"{base}/sitemap-{idx}.xml")
        if not resp or resp.status_code != 200:
            break
        locs = LOC_PATTERN.findall(resp.text)
        if not locs:
            break
        urls.extend(locs)
    return urls


# ─── Yoast/RankMath style (post-sitemap.xml → post-sitemap1.xml ...) ──────────

def fetch_yoast_sitemap(base_url: str, max_pages: int = 50) -> list:
    """Yoast/RankMath 인덱스 sitemap 순회."""
    base = _normalize_base(base_url)
    urls = []

    for name in ("sitemap_index.xml", "sitemap.xml"):
        resp = _http_get(f"{base}/{name}")
        if resp and resp.status_code == 200 and "<loc>" in resp.text:
            for sub in LOC_PATTERN.findall(resp.text):
                sub_resp = _http_get(sub)
                if sub_resp and sub_resp.status_code == 200:
                    urls.extend(LOC_PATTERN.findall(sub_resp.text))
            if urls:
                return urls

    for idx in range(1, max_pages + 1):
        for pattern in (f"post-sitemap{idx}.xml", f"post-sitemap-{idx}.xml"):
            resp = _http_get(f"{base}/{pattern}")
            if resp and resp.status_code == 200:
                urls.extend(LOC_PATTERN.findall(resp.text))
                break
    return urls


# ─── WordPress Core wp-sitemap.xml ────────────────────────────────────────────

def fetch_wp_core_sitemap(base_url: str) -> list:
    base = _normalize_base(base_url)
    urls = []
    resp = _http_get(f"{base}/wp-sitemap.xml")
    if not resp or resp.status_code != 200:
        return urls
    for sub in LOC_PATTERN.findall(resp.text):
        if "post" not in sub:
            continue
        sub_resp = _http_get(sub)
        if sub_resp and sub_resp.status_code == 200:
            urls.extend(LOC_PATTERN.findall(sub_resp.text))
    return urls


# ─── WordPress REST API ───────────────────────────────────────────────────────

def fetch_wp_rest_posts(base_url: str, per_page: int = 100,
                        max_pages: int = 5) -> list:
    """WordPress REST API 최근 글 수집. 공개 엔드포인트이므로 인증 불필요."""
    base = _normalize_base(base_url)
    records = []
    for page in range(1, max_pages + 1):
        url = f"{base}/wp-json/wp/v2/posts?per_page={per_page}&page={page}&_fields=link,title,date"
        resp = _http_get(url)
        if not resp or resp.status_code != 200:
            break
        try:
            items = resp.json()
        except Exception:
            break
        if not isinstance(items, list) or not items:
            break
        for it in items:
            link = it.get("link") or ""
            if not link:
                continue
            title = (it.get("title") or {}).get("rendered", "") if isinstance(it.get("title"), dict) else (it.get("title") or "")
            records.append({
                "url": link,
                "title": title,
                "published": it.get("date"),
                "source": "rest",
            })
    return records


# ─── 통합 수집 진입점 ──────────────────────────────────────────────────────────

def collect_backlink_urls(base_urls: Iterable[str],
                          strategies: Iterable[str] = ("jetpack", "yoast", "wp_core", "rest"),
                          max_sitemap_pages: int = 100,
                          rest_max_pages: int = 3) -> list:
    """여러 블로그에서 백링크 후보 URL 통합 수집.

    Args:
        base_urls:  ["https://a.mycafe24.com", "https://b.com"]
        strategies: 사용할 전략 ("jetpack"|"yoast"|"wp_core"|"rest")
        max_sitemap_pages: sitemap 페이지 최대 탐색 수
        rest_max_pages:    REST API 페이지 최대 탐색 수

    Returns:
        [{"url": str, "title": str, "published": str|None,
          "site": str, "source": str}, ...]  (중복 url 제거됨)
    """
    collected = []
    seen = set()

    for base in base_urls:
        base = _normalize_base(base)
        log(f"URL 수집 대상: {base}", "step")
        site_count = 0

        if "jetpack" in strategies:
            for u in fetch_jetpack_sitemap(base, max_pages=max_sitemap_pages):
                if u and u not in seen and u != base and u != f"{base}/":
                    seen.add(u)
                    collected.append({"url": u, "title": "",
                                      "published": None, "site": base, "source": "sitemap"})
                    site_count += 1

        if "yoast" in strategies:
            for u in fetch_yoast_sitemap(base):
                if u and u not in seen and u != base and u != f"{base}/":
                    seen.add(u)
                    collected.append({"url": u, "title": "",
                                      "published": None, "site": base, "source": "sitemap"})
                    site_count += 1

        if "wp_core" in strategies:
            for u in fetch_wp_core_sitemap(base):
                if u and u not in seen and u != base and u != f"{base}/":
                    seen.add(u)
                    collected.append({"url": u, "title": "",
                                      "published": None, "site": base, "source": "sitemap"})
                    site_count += 1

        if "rest" in strategies:
            for rec in fetch_wp_rest_posts(base, max_pages=rest_max_pages):
                u = rec["url"]
                if u and u not in seen:
                    seen.add(u)
                    rec["site"] = base
                    collected.append(rec)
                    site_count += 1

        log(f"  └ {base}: {site_count}개 URL 수집", "ok")

    log(f"총 {len(collected)}개 URL 수집 완료 ({len(list(base_urls))}개 사이트)", "ok")
    return collected
