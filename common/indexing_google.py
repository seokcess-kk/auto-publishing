"""
Google Indexing API 제출자

서비스 계정(Service Account) OAuth2 인증으로 Google Indexing API에 URL을 제출한다.
하루 200개 한도. 429 응답 시 즉시 중단.

SA 키 선택 우선순위 (사이트별):
    1) GOOGLE_INDEXING_KEY_<DOMAIN_SLUG>  — 도메인별 명시 키
       예: example.tistory.com → GOOGLE_INDEXING_KEY_EXAMPLE_TISTORY_COM
    2) GOOGLE_INDEXING_KEY_DEFAULT        — 폴백 (단일 SA 권장)
    3) GOOGLE_INDEXING_SA_JSON            — 단일 키 환경변수 (레거시 호환)

권한 없는 사이트 처리:
    - 403 응답 수신 시 DEFAULT 키로 재시도 (1회)
    - DEFAULT 키로도 403이면 "no_permission" 반환 (건너뜀)
    - "no_permission" URL은 publish_queue에서 색인 대상으로 재큐잉되지 않음

권장 운영:
    Search Console 에서 SA 이메일을 모든 운영 사이트의 소유자로 추가하면
    DEFAULT 키 1개로 전체 사이트 색인을 관리할 수 있다.
"""
import json
import os
import time

from common.logger import log


SCOPES = ["https://www.googleapis.com/auth/indexing"]
ENDPOINT = "https://indexing.googleapis.com/v3/urlNotifications:publish"
DAILY_LIMIT = 200
REQUEST_INTERVAL = 5  # 요청 사이 대기(초)

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SITE_KEY_PREFIX = "GOOGLE_INDEXING_KEY_"
_DEFAULT_KEY_ENV = "GOOGLE_INDEXING_KEY_DEFAULT"
_LEGACY_KEY_ENV  = "GOOGLE_INDEXING_SA_JSON"


def _domain_to_slug(domain: str) -> str:
    """도메인 전체를 환경변수 슬러그로 변환.

    예:
        example.tistory.com    → EXAMPLE_TISTORY_COM
        my-site.github.io      → MY_SITE_GITHUB_IO
        myblog.mycafe24.com    → MYBLOG_MYCAFE24_COM
    """
    return domain.upper().replace("-", "_").replace(".", "_")


def _resolve_path(env_value: str) -> str:
    """절대경로이면 그대로, 상대경로이면 프로젝트 루트 기준으로 변환."""
    if not env_value:
        return ""
    return env_value if os.path.isabs(env_value) else os.path.join(_BASE_DIR, env_value)


def _get_sa_json_path(domain: str = "", allow_default: bool = True) -> str:
    """도메인에 맞는 SA JSON 키 파일 경로 반환. 없으면 빈 문자열."""
    if domain:
        slug = _domain_to_slug(domain)
        env_name = f"{_SITE_KEY_PREFIX}{slug}"
        path = _resolve_path(os.getenv(env_name, ""))
        if path and os.path.exists(path):
            return path

    if not allow_default:
        return ""

    for env_name in (_DEFAULT_KEY_ENV, _LEGACY_KEY_ENV):
        path = _resolve_path(os.getenv(env_name, ""))
        if path and os.path.exists(path):
            return path

    return ""


def _build_http(sa_json_path: str):
    """oauth2client 기반 인증된 httplib2.Http 반환."""
    try:
        from oauth2client.service_account import ServiceAccountCredentials
        import httplib2
    except ImportError as e:
        raise ImportError(
            f"oauth2client 또는 httplib2 패키지가 없습니다: {e}\n"
            "pip install oauth2client httplib2"
        ) from e

    credentials = ServiceAccountCredentials.from_json_keyfile_name(sa_json_path, scopes=SCOPES)
    return credentials.authorize(httplib2.Http())


def _get_default_http():
    """DEFAULT 키로 http 클라이언트 반환. 없으면 None."""
    path = _get_sa_json_path(domain="", allow_default=True)
    if not path:
        return None
    try:
        return _build_http(path)
    except Exception as e:
        log(f"[Google 색인] DEFAULT SA 로드 실패: {e}", "error")
        return None


def submit_urls(urls: list) -> dict:
    """URL 목록을 Google Indexing API에 제출.

    Args:
        urls: 제출할 URL 목록 (최대 DAILY_LIMIT개 권장)

    Returns:
        {url: "ok" | "limit" | "no_permission" | "error"} 딕셔너리

        "no_permission": SA 키가 없거나 403 → Search Console에서 SA 권한 추가 필요
        "limit"        : 일일 200개 한도 초과
        "error"        : API 오류 또는 예외
    """
    if not urls:
        return {}

    results: dict = {}

    # 도메인별 http 클라이언트 캐시 (사이트별 SA 키 재사용)
    _http_cache: dict = {}
    _default_http = None  # 403 폴백용 lazy init

    # DEFAULT SA 경로 (403 폴백용 비교)
    _default_sa_path = _get_sa_json_path(domain="", allow_default=True)

    for idx, url in enumerate(urls[:DAILY_LIMIT], start=1):
        from urllib.parse import urlparse
        parsed = urlparse(url)
        # 절대 URL 가드 — http(s) scheme + netloc 이 있어야 함. 없으면
        # Google Indexing API 가 400 'not in standard URL format' 반환.
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            log(f"[Google 색인] {idx}. 절대 URL 아님, 건너뜀: {url}", "warn")
            results[url] = "error"
            continue
        domain = parsed.netloc

        # ── http 클라이언트 확보 ──────────────────────────────────────────────
        if domain not in _http_cache:
            sa_path = _get_sa_json_path(domain=domain, allow_default=True)
            if not sa_path:
                log(f"[Google 색인] SA 키 없음: {domain} → 건너뜀", "warn")
                results[url] = "no_permission"
                continue
            try:
                _http_cache[domain] = (sa_path, _build_http(sa_path))
            except Exception as e:
                log(f"[Google 색인] SA 로드 실패 ({domain}): {e}", "error")
                results[url] = "error"
                continue

        sa_path_used, http = _http_cache[domain]
        body = json.dumps({"url": url, "type": "URL_UPDATED"})

        # ── API 호출 ──────────────────────────────────────────────────────────
        try:
            response, resp_body = http.request(ENDPOINT, method="POST", body=body)
            status = response.get("status", "")

            if status == "200":
                log(f"[Google 색인] {idx}. OK: {url}", "ok")
                results[url] = "ok"

            elif status == "429":
                log(f"[Google 색인] 일일 한도(200개) 초과 — 중단", "warn")
                results[url] = "limit"
                for remaining_url in list(urls)[idx:DAILY_LIMIT]:
                    results[remaining_url] = "limit"
                break

            elif status in ("403", "401"):
                # 권한 없음 → DEFAULT SA로 1회 재시도
                if sa_path_used != _default_sa_path and _default_sa_path:
                    log(f"[Google 색인] {idx}. 403 → DEFAULT SA 재시도: {url}", "info")
                    if _default_http is None:
                        _default_http = _get_default_http()
                    if _default_http:
                        try:
                            r2, b2 = _default_http.request(ENDPOINT, method="POST", body=body)
                            if r2.get("status") == "200":
                                log(f"[Google 색인] {idx}. DEFAULT SA OK: {url}", "ok")
                                results[url] = "ok"
                            else:
                                log(f"[Google 색인] {idx}. DEFAULT SA도 실패 [{r2.get('status')}]: {url}", "warn")
                                results[url] = "no_permission"
                        except Exception as e2:
                            log(f"[Google 색인] {idx}. DEFAULT SA 재시도 예외: {e2}", "error")
                            results[url] = "no_permission"
                    else:
                        results[url] = "no_permission"
                else:
                    # 이미 DEFAULT SA를 썼는데도 403
                    msg = json.loads(resp_body.decode()).get("error", {}).get("message", "") if resp_body else ""
                    log(f"[Google 색인] {idx}. 권한 없음 [{status}] {url}: {msg}", "warn")
                    log(f"  → Search Console에서 SA 이메일을 소유자로 추가하세요", "info")
                    results[url] = "no_permission"

            else:
                result_json = json.loads(resp_body.decode()) if resp_body else {}
                msg = result_json.get("error", {}).get("message", "")
                log(f"[Google 색인] {idx}. 실패 [{status}] {url}: {msg}", "warn")
                results[url] = "error"

        except Exception as e:
            log(f"[Google 색인] {idx}. 예외: {url} — {e}", "error")
            results[url] = "error"

        if idx < min(len(urls), DAILY_LIMIT):
            time.sleep(REQUEST_INTERVAL)

    ok_count = sum(1 for s in results.values() if s == "ok")
    no_perm  = sum(1 for s in results.values() if s == "no_permission")
    log(
        f"[Google 색인] 완료: {ok_count}/{len(urls)}건 성공"
        + (f", {no_perm}건 권한 없음 (Search Console SA 추가 필요)" if no_perm else ""),
        "step",
    )
    return results
