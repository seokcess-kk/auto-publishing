"""
Threads OAuth 자동 캡처 헬퍼 — URL 붙여넣기 불필요

기존 common.threads_token oauth 는 사용자가 redirect URL 을 터미널에
직접 붙여넣어야 한다. 이 스크립트는 localhost:5000 에 HTTPS 서버를 띄워
redirect 콜백을 자동 캡처한 후 단기→장기 토큰 교환까지 처리한다.

사용법:
    python tools/threads_oauth_poll.py

동작:
    1. 임시 self-signed 인증서 생성
    2. HTTPS 서버 localhost:5000 listen 시작
    3. 브라우저 자동 오픈 → Threads 로그인 페이지
    4. 로그인 + 권한 동의
    5. 브라우저가 https://localhost:5000?code=... 로 redirect
       → '안전하지 않음' 경고 표시되면 '고급 → 안전하지 않음으로 이동' 클릭
    6. 서버가 code 캡처 → 단기 토큰 → 장기 토큰 교환 → .env 저장
    7. 자동 종료

전제조건:
    - .env 의 THREADS_APP_ID, THREADS_APP_SECRET, THREADS_REDIRECT_URI 채워져 있어야 함
    - REDIRECT_URI 는 Meta 앱 설정과 동일해야 함 (기본: https://localhost:5000)
"""
from __future__ import annotations

import os
import ssl
import sys
import tempfile
import threading
import time
import webbrowser
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _BASE_DIR)

from dotenv import load_dotenv
load_dotenv(os.path.join(_BASE_DIR, ".env"))


# ─── 상태 보관용 글로벌 ──────────────────────────────────────────────────
_captured_code: str | None = None
_capture_event = threading.Event()


class _OAuthHandler(BaseHTTPRequestHandler):
    """redirect 콜백을 받아 code 를 추출, 응답 페이지 출력 후 종료."""

    def do_GET(self):  # noqa: N802
        global _captured_code
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)
        code = qs.get("code", [None])[0]
        error = qs.get("error", [None])[0]
        error_desc = qs.get("error_description", [None])[0]

        body_html: str
        if code:
            _captured_code = code
            body_html = (
                "<!doctype html><html><head><meta charset='utf-8'>"
                "<title>OAuth OK</title></head><body style='font-family:system-ui;"
                "padding:40px;text-align:center;'>"
                "<h1 style='color:#03C75A'>인증 코드 캡처 완료</h1>"
                "<p>이 창은 닫으셔도 됩니다. 터미널로 돌아가세요.</p>"
                "</body></html>"
            )
            _capture_event.set()
        elif error:
            body_html = (
                f"<!doctype html><html><head><meta charset='utf-8'>"
                f"<title>OAuth Error</title></head><body style='font-family:system-ui;"
                f"padding:40px;'>"
                f"<h1 style='color:#e4000f'>인증 오류</h1>"
                f"<p><b>error:</b> {error}</p>"
                f"<p><b>description:</b> {error_desc or '-'}</p>"
                f"</body></html>"
            )
            _capture_event.set()  # 그래도 종료
        else:
            body_html = "<p>code 또는 error 파라미터가 없습니다.</p>"

        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(body_html.encode("utf-8"))

    def log_message(self, *args, **kwargs):  # 콘솔 로그 억제
        pass


def _make_self_signed_cert() -> tuple[str, str]:
    """임시 self-signed 인증서/키 생성. (cert_path, key_path) 반환."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = issuer = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, "localhost"),
    ])
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.now(timezone.utc))
        .not_valid_after(datetime.now(timezone.utc) + timedelta(days=1))
        .add_extension(
            x509.SubjectAlternativeName([x509.DNSName("localhost")]),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )

    tmpdir = tempfile.mkdtemp(prefix="threads_oauth_")
    cert_path = os.path.join(tmpdir, "cert.pem")
    key_path = os.path.join(tmpdir, "key.pem")
    with open(cert_path, "wb") as f:
        f.write(cert.public_bytes(serialization.Encoding.PEM))
    with open(key_path, "wb") as f:
        f.write(key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        ))
    return cert_path, key_path


def main() -> int:
    app_id       = os.getenv("THREADS_APP_ID", "")
    redirect_uri = os.getenv("THREADS_REDIRECT_URI", "")
    if not app_id or not redirect_uri:
        print("[ERROR] THREADS_APP_ID / THREADS_REDIRECT_URI 미설정 (.env 확인)")
        return 1

    parsed = urlparse(redirect_uri)
    if parsed.hostname not in ("localhost", "127.0.0.1") or parsed.port != 5000:
        print(f"[ERROR] redirect_uri 가 https://localhost:5000 (또는 http) 가 아님: {redirect_uri}")
        print("        Meta 앱 설정과 .env 둘 다 동일하게 맞춰주세요.")
        return 1

    use_ssl = parsed.scheme == "https"

    print("[1] OAuth 인증 URL 생성 + 브라우저 자동 오픈")
    auth_params = {
        "client_id":     app_id,
        "redirect_uri":  redirect_uri,
        # threads_manage_replies 가 있어야 reply chain (스레드 답글) 발행 가능
        "scope":         "threads_basic,threads_content_publish,threads_manage_replies",
        "response_type": "code",
    }
    auth_url = "https://threads.net/oauth/authorize?" + urlencode(auth_params)
    print(f"      {auth_url[:120]}...")
    try:
        webbrowser.open(auth_url)
    except Exception as e:
        print(f"      브라우저 자동 오픈 실패 (위 URL 직접 접속): {e}")

    print(f"[2] 로컬 {parsed.scheme.upper()} 서버 listen :5000")
    if use_ssl:
        cert_path, key_path = _make_self_signed_cert()
        print(f"      self-signed cert 생성: {cert_path}")
        print("      ⚠️  브라우저에 '안전하지 않음' 경고가 뜨면 '고급 → localhost(안전하지 않음)으로 이동' 클릭")

    server = HTTPServer(("0.0.0.0", 5000), _OAuthHandler)
    if use_ssl:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(cert_path, key_path)
        server.socket = ctx.wrap_socket(server.socket, server_side=True)

    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    print("[3] 콜백 대기 중 (최대 10분)...")
    captured = _capture_event.wait(timeout=600)
    server.shutdown()

    if not captured or not _captured_code:
        print("[ERROR] 시간 초과 또는 code 미수신")
        return 1

    print(f"[OK] code 캡처 완료: {_captured_code[:25]}...")
    print("[4] code → 단기 토큰 → 장기 토큰 교환")

    # common.threads_token 의 헬퍼 재사용
    from common.threads_token import get_short_lived_token, exchange_short_to_long

    short = get_short_lived_token(_captured_code)
    if not short:
        print("[ERROR] 단기 토큰 발급 실패")
        return 1

    long_token = exchange_short_to_long(short, save=True)
    if not long_token:
        print("[ERROR] 장기 토큰 교환 실패")
        return 1

    # USER_ID 도 함께 저장 (단기 토큰 발급 시 응답에 포함되지만 별도 호출 필요)
    try:
        import requests
        resp = requests.get("https://graph.threads.net/v1.0/me",
                             params={"fields": "id,username", "access_token": long_token},
                             timeout=10)
        if resp.ok:
            data = resp.json()
            uid = data.get("id", "")
            uname = data.get("username", "")
            if uid:
                from dotenv import set_key
                env_path = os.path.join(_BASE_DIR, ".env")
                set_key(env_path, "THREADS_USER_ID", uid)
                print(f"[OK] THREADS_USER_ID={uid} (username={uname}) 저장")
    except Exception as e:
        print(f"[WARN] USER_ID 저장 실패 (수동 입력 필요): {e}")

    print("[완료] .env 의 THREADS_ACCESS_TOKEN / THREADS_USER_ID 자동 저장됨")
    return 0


if __name__ == "__main__":
    sys.exit(main())
