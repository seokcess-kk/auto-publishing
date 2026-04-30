"""
인증 헬퍼 모듈
- Naver CDP 로그인 (Chrome 실제 프로필 쿠키 추출) — 1순위
- Naver RSA 로그인 (BVSD 포함) — 폴백
- WordPress Basic Auth 헤더 생성
- Coupang HMAC-SHA256 서명
"""
import base64
import hashlib
import hmac
import json
import os
import subprocess
import time
import uuid
from datetime import datetime, timezone

import requests
from .logger import log


# ─── Naver CDP 로그인 ────────────────────────────────────────────────────────

CHROME_PATH    = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
CDP_PORT_NAVER = 9223   # 쿠팡(9222)과 포트 분리
CHROME_PROFILE = os.getenv("NAVER_CHROME_PROFILE", "Profile 2")  # 네이버 로그인된 프로필
CHROME_USER_DATA = os.path.expanduser("~/Library/Application Support/Google/Chrome")


def naver_login_cdp(session: requests.Session) -> bool:
    """로컬 Chrome 프로필을 CDP로 연결해 네이버 쿠키를 requests.Session에 주입.

    - 이미 로그인된 Chrome 프로필의 쿠키 DB를 복사해 독립 인스턴스로 실행
    - 실행 중인 Chrome과 충돌하지 않도록 프로필을 임시 디렉토리로 복사
    - NAVER_CHROME_PROFILE 환경변수로 프로필 지정 (기본: Profile 2)
    """
    import shutil
    import tempfile

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        log("playwright 미설치: pip install playwright", "warn")
        return False

    src_profile = os.path.join(CHROME_USER_DATA, CHROME_PROFILE)
    if not os.path.exists(src_profile):
        log(f"[Naver CDP] 프로필 디렉토리 없음: {src_profile}", "warn")
        return False

    log(f"[Naver CDP] Chrome '{CHROME_PROFILE}' 프로필로 쿠키 추출", "step")

    # 프로필을 임시 디렉토리에 복사 (실행 중인 Chrome과 충돌 방지)
    tmp_user_data = tempfile.mkdtemp(prefix="naver_chrome_")
    tmp_profile_dir = os.path.join(tmp_user_data, "Default")
    try:
        shutil.copytree(src_profile, tmp_profile_dir,
                        ignore=shutil.ignore_patterns(
                            "SingletonLock", "SingletonCookie", "SingletonSocket",
                            "lockfile", "*.log", "Cache", "Code Cache",
                            "GPUCache", "ShaderCache",
                        ))
    except Exception as e:
        log(f"[Naver CDP] 프로필 복사 실패: {e}", "warn")
        shutil.rmtree(tmp_user_data, ignore_errors=True)
        return False

    cmd = [
        CHROME_PATH,
        f"--remote-debugging-port={CDP_PORT_NAVER}",
        f"--user-data-dir={tmp_user_data}",
        "--profile-directory=Default",
        "--headless=new",
        "--no-sandbox",
        "--disable-blink-features=AutomationControlled",
        "--disable-extensions",
    ]
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(4)  # 프로필 복사본 로드는 2초보다 더 필요

    naver_cookies = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.connect_over_cdp(f"http://127.0.0.1:{CDP_PORT_NAVER}")
            context = browser.contexts[0] if browser.contexts else browser.new_context()
            page = context.new_page()

            # 네이버 메인 → blog 방문하여 쿠키 활성화
            page.goto("https://www.naver.com", wait_until="domcontentloaded", timeout=15000)
            time.sleep(1)
            page.goto("https://blog.naver.com", wait_until="domcontentloaded", timeout=15000)
            time.sleep(1)

            # 네이버 도메인 쿠키 수집
            all_cookies = context.cookies(["https://www.naver.com", "https://blog.naver.com"])
            naver_cookies = [c for c in all_cookies if "naver" in c.get("domain", "")]
            page.close()
            browser.close()
    except Exception as e:
        log(f"[Naver CDP] 브라우저 연결 실패: {e}", "error")
        return False
    finally:
        proc.terminate()
        time.sleep(0.5)
        shutil.rmtree(tmp_user_data, ignore_errors=True)

    if not naver_cookies:
        log("[Naver CDP] 네이버 쿠키 없음 (해당 프로필에 로그인 필요)", "warn")
        return False

    # requests.Session에 쿠키 주입
    for c in naver_cookies:
        session.cookies.set(c["name"], c["value"], domain=c.get("domain", "").lstrip("."))

    cookie_names = [c["name"] for c in naver_cookies]
    has_auth = "NID_AUT" in cookie_names or "NID_SES" in cookie_names
    if has_auth:
        log(f"[Naver CDP] 로그인 쿠키 주입 완료 ({len(naver_cookies)}개)", "ok")
        return True

    log(f"[Naver CDP] 쿠키 주입했지만 NID_AUT/NID_SES 없음: {cookie_names}", "warn")
    return False


# ─── Naver RSA 로그인 ────────────────────────────────────────────────────────

def naver_get_rsa_keys(session: requests.Session) -> dict:
    """Naver 로그인 페이지에서 RSA 공개키 정보를 가져온다."""
    url = "https://nid.naver.com/login/ext/keys.nhn"
    resp = session.get(url)
    resp.raise_for_status()
    tokens = resp.text.split(",")
    return {
        "session_key": tokens[0],
        "key_name":    tokens[1],
        "n_val":       tokens[2],  # 긴 16진수 모듈러스
        "e_val":       tokens[3],  # 공개 지수 (보통 010001)
    }


def naver_encrypt_credentials(session_key: str, key_name: str,
                               e_val: str, n_val: str,
                               username: str, password: str) -> tuple[str, str]:
    """RSA로 아이디/비밀번호를 암호화하여 (encrypted_str, key_name) 반환."""
    try:
        import rsa
    except ImportError:
        raise ImportError("rsa 패키지 필요: pip install rsa")

    n = int(n_val, 16)
    e = int(e_val, 16)
    pub_key = rsa.PublicKey(n, e)

    # 각 필드를 UTF-8 바이트로 변환 후 바이트 길이 기준으로 메시지 조립
    sk_b  = session_key.encode("utf-8")
    uid_b = username.encode("utf-8")
    pw_b  = password.encode("utf-8")
    msg = (
        bytes([len(sk_b)])  + sk_b
        + bytes([len(uid_b)]) + uid_b
        + bytes([len(pw_b)])  + pw_b
    )

    encrypted = rsa.encrypt(msg, pub_key)
    return encrypted.hex(), key_name


def naver_build_bvsd() -> str:
    """Naver BVSD(브라우저 지문) JSON 문자열 생성."""
    bvsd = {
        "uuid": str(uuid.uuid4()),
        "em": {
            "version": "1.0.0",
            "platform": "macOS",
            "app_key": "naverapp",
        },
        "ts": int(time.time() * 1000),
    }
    return json.dumps(bvsd, ensure_ascii=False)


def naver_login(session: requests.Session,
                username: str, password: str) -> bool:
    """Naver 계정으로 로그인. 성공 시 True 반환."""
    log(f"[Naver] 로그인 시도: {username}", "step")
    keys = naver_get_rsa_keys(session)
    enc_pw, key_name = naver_encrypt_credentials(
        keys["session_key"], keys["key_name"],
        keys["e_val"], keys["n_val"],
        username, password,
    )
    bvsd = naver_build_bvsd()

    payload = {
        "svctype":    "0",
        "enctp":      "1",
        "encpw":      enc_pw,
        "encnm":      key_name,
        "sv":         "https://www.naver.com",
        "url":        "https://www.naver.com",
        "id":         username,
        "pw":         "",
        "locale":     "ko_KR",
        "bvsd":       bvsd,
    }
    resp = session.post(
        "https://nid.naver.com/nidlogin.login",
        data=payload,
        headers={"Referer": "https://nid.naver.com/nidlogin.login"},
        allow_redirects=False,
    )

    # 로그인 성공 시 302 리다이렉트 또는 JS location.replace 포함
    if resp.status_code not in (200, 302) or (
        resp.status_code == 200 and "location.replace" not in resp.text
    ):
        log("[Naver] 로그인 실패", "error")
        return False

    # JS 리다이렉트: location.replace("URL") 에서 URL 추출 후 GET
    if resp.status_code == 200 and "location.replace" in resp.text:
        import re
        m = re.search(r'location\.replace\("([^"]+)"', resp.text)
        if m:
            session.get(m.group(1), allow_redirects=True)

    # 302 리다이렉트 수동 추적 (쿠키 수집)
    if resp.status_code == 302:
        location = resp.headers.get("Location", "")
        if location:
            session.get(location, allow_redirects=True)

    # NID_AUT 또는 NID_SES 쿠키 확인
    cookie_keys = [c.name for c in session.cookies]
    if "NID_AUT" in cookie_keys or "NID_SES" in cookie_keys:
        log("[Naver] 로그인 성공", "ok")
        return True

    # 쿠키 없어도 로그인 응답 자체가 성공이면 통과 (일부 계정)
    log("[Naver] 로그인 성공 (쿠키 미확인)", "ok")
    return True


# ─── WordPress Basic Auth ────────────────────────────────────────────────────

def wp_basic_auth_header(username: str, app_password: str) -> dict:
    """WordPress Application Password 기반 Basic Auth 헤더 반환."""
    token = base64.b64encode(f"{username}:{app_password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def wp_jwt_auth_header(jwt_token: str) -> dict:
    """WordPress JWT 토큰 기반 Bearer Auth 헤더 반환."""
    return {"Authorization": f"Bearer {jwt_token}"}


# ─── Coupang HMAC-SHA256 ─────────────────────────────────────────────────────

def coupang_hmac_headers(method: str, path: str,
                          access_key: str, secret_key: str,
                          query: str = "") -> dict:
    """Coupang Partners API HMAC-SHA256 서명 헤더 반환."""
    datetime_str = datetime.now(timezone.utc).strftime("%y%m%dT%H%M%SZ")
    message = datetime_str + method + path + query
    signature = hmac.new(
        secret_key.encode("utf-8"),
        message.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    auth = f"CEA algorithm=HmacSHA256, access-key={access_key}, signed-date={datetime_str}, signature={signature}"
    return {
        "Authorization": auth,
        "Content-Type":  "application/json;charset=UTF-8",
    }
