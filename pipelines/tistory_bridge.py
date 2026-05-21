"""티스토리 브릿지 — Chrome Extension 이 polling 으로 가져갈 HTTP 서버.

구조:
  Python 파이프라인 → tistory_queue.enqueue() → 큐 파일에 적재
  Chrome Extension → GET /next → 큐에서 다음 'pending' 항목 claim
  Extension → editor DOM 자동 작성 + 사용자 캡차 풀이 + 발행 클릭
  Extension → POST /done {id, url} 또는 POST /fail {id, error}

엔드포인트 (모두 JSON):
  GET  /healthz            → {"ok": true}
  GET  /next               → 다음 pending 항목 (extension payload 형태) 또는 204
  POST /done {id, url}     → 발행 성공 기록 + publish_queue.json 갱신
  POST /fail {id, error}   → 실패 기록
  GET  /list?status=pending → 큐 상태 조회 (디버깅)
  POST /reset-stale        → 30분 이상 'claimed' 상태 항목 pending 복원

CORS: chrome-extension://* 만 허용 (확장 ID 가 install 마다 달라지므로 wildcard).

실행:
  python -m pipelines.tistory_bridge
  python -m pipelines.tistory_bridge --port 5757
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from common.logger import log  # noqa: E402
from common.tistory_queue import (  # noqa: E402
    claim_next, list_all, mark_done, mark_failed,
    reset_stale_claimed, to_extension_payload,
    set_captcha_pending, find_item_by_tg_message_id,
    set_captcha_answer, pop_captcha_answer, reset_stale_captcha,
    get as queue_get,
)


_DEFAULT_PORT = 5757


class BridgeHandler(BaseHTTPRequestHandler):
    """HTTP handler — JSON 응답 + extension CORS."""

    def log_message(self, fmt: str, *args) -> None:  # noqa: A003
        # BaseHTTPRequestHandler 의 stderr 출력을 우리 logger 로 통일
        log(f"[bridge] {self.address_string()} - {fmt % args}", "info")

    # ─── CORS / OPTIONS ──────────────────────────────────────────────────────

    def _cors(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(204)
        self._cors()
        self.end_headers()

    # ─── 응답 헬퍼 ───────────────────────────────────────────────────────────

    def _json(self, status: int, payload: dict | list) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def _no_content(self, status: int = 204) -> None:
        self.send_response(status)
        self._cors()
        self.end_headers()

    def _read_json(self) -> dict | None:
        try:
            length = int(self.headers.get("Content-Length", "0") or "0")
            if length <= 0:
                return {}
            raw = self.rfile.read(length)
            return json.loads(raw.decode("utf-8"))
        except Exception as e:
            self._json(400, {"error": f"invalid json: {e}"})
            return None

    # ─── GET ────────────────────────────────────────────────────────────────

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path == "/healthz":
            self._json(200, {"ok": True, "pending": len(list_all("pending"))})
            return
        if path == "/next":
            item = claim_next()
            if item is None:
                self._no_content(204)
                return
            self._json(200, to_extension_payload(item))
            return
        if path == "/list":
            qs = self.path.split("?", 1)[1] if "?" in self.path else ""
            params = dict(p.split("=", 1) for p in qs.split("&") if "=" in p)
            self._json(200, list_all(params.get("status") or None))
            return
        if path.startswith("/captcha/answer/"):
            item_id = path[len("/captcha/answer/"):]
            answer = pop_captcha_answer(item_id)
            if answer is None:
                self._no_content(204)
            else:
                self._json(200, {"id": item_id, "answer": answer})
            return
        if path == "/captcha/state":
            # 진단용 — 현재 pending 캡차 + 받은 답안 dump
            from common.tistory_queue import _CAPTCHA_PENDING, _CAPTCHA_ANSWERS
            self._json(200, {
                "pending": {iid: {"tg_message_id": v["tg_message_id"]} for iid, v in _CAPTCHA_PENDING.items()},
                "answers": dict(_CAPTCHA_ANSWERS),
            })
            return
        self._json(404, {"error": f"not found: {path}"})

    # ─── POST ───────────────────────────────────────────────────────────────

    def do_POST(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path == "/captcha/needed":
            # background.js 가 chrome.tabs.captureVisibleTab 으로 캡처한 이미지
            # 를 텔레그램으로 전송. 본인이 답글로 답안 입력.
            data = self._read_json()
            if data is None:
                return
            item_id = data.get("id", "")
            image_b64 = data.get("image_b64", "")
            if not item_id or not image_b64:
                self._json(400, {"error": "id, image_b64 필수"})
                return
            item = queue_get(item_id) or {}
            title = (item.get("title", "") or "")[:60]
            caption = (
                f"🔐 DKAPTCHA 풀이 필요\n"
                f"📝 {title}\n\n"
                f"👇 이 메시지에 *답글*로 정답 입력"
            )
            msg_id = _telegram_send_photo(image_b64, caption)
            if not msg_id:
                self._json(500, {"ok": False, "error": "Telegram sendPhoto 실패"})
                return
            set_captcha_pending(item_id, msg_id)
            log(f"[bridge] 캡차 텔레그램 전송: item={item_id[:8]} msg_id={msg_id}", "ok")
            self._json(200, {"ok": True, "telegram_message_id": msg_id})
            return
        if path == "/done":
            data = self._read_json()
            if data is None:
                return
            item_id = data.get("id", "")
            url = data.get("url", "")
            post_id = data.get("post_id", "")
            if not item_id or not url:
                self._json(400, {"error": "id, url 필수"})
                return
            ok = mark_done(item_id, url=url, post_id=post_id)
            if ok:
                # publish_queue.json 에도 기록 — backlink/색인 파이프라인이 활용
                self._record_publish_queue(item_id, url)
                log(f"[bridge] done id={item_id[:8]} url={url}", "ok")
                # 실제 발행 완료 telegram 알림 — 파이프라인 단계 알림 대신
                self._notify_publish_done(item_id, url)
            self._json(200 if ok else 404, {"ok": ok})
            return
        if path == "/fail":
            data = self._read_json()
            if data is None:
                return
            item_id = data.get("id", "")
            err = data.get("error", "")
            if not item_id:
                self._json(400, {"error": "id 필수"})
                return
            ok = mark_failed(item_id, error=err)
            log(f"[bridge] fail id={item_id[:8]} err={err[:80]}", "warn")
            self._json(200 if ok else 404, {"ok": ok})
            return
        if path == "/reset-stale":
            n = reset_stale_claimed(stale_minutes=30)
            self._json(200, {"reset": n})
            return
        self._json(404, {"error": f"not found: {path}"})

    # ─── publish_queue 갱신 ──────────────────────────────────────────────────

    def _notify_publish_done(self, item_id: str, url: str) -> None:
        """실제 발행 완료 telegram 알림 — bridge 모드에선 이게 진짜 알림."""
        try:
            from common.tistory_queue import get as _get
            from common.notifier import _send_telegram
            item = _get(item_id) or {}
            title = (item.get("title", "") or "")[:60]
            source = item.get("source", "")
            keyword = item.get("keyword", "")
            blog = item.get("blog_name", "")
            tag = f"{source}→티스토리" if source else "티스토리"
            msg = (
                f"✅ <b>[Tistory 발행 완료]</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"📌 {tag}\n"
                f"📝 {title}\n"
                + (f"🔍 키워드: {keyword}\n" if keyword else "")
                + f"🔗 {url}"
            )
            _send_telegram(msg)
        except Exception as e:
            log(f"[bridge] publish-done telegram 알림 실패 (무시): {e}", "warn")

    def _record_publish_queue(self, item_id: str, url: str) -> None:
        """발행 성공 시 publish_queue.json 에도 push — 색인/백링크 파이프라인 input."""
        try:
            from common.tistory_queue import get as _get
            from common.publish_queue import add_url as _add_url
            item = _get(item_id) or {}
            _add_url(
                url,
                platform="tistory",
                title=item.get("title", ""),
                keyword=item.get("keyword", ""),
                source=item.get("source", ""),
                affiliate_url=item.get("affiliate_url", ""),
            )
        except Exception as e:
            log(f"[bridge] publish_queue 갱신 실패 (무시): {e}", "warn")


def _stale_reset_loop() -> None:
    """30초마다 stale claimed 항목 검사 — extension 이 죽어도 회복되도록."""
    while True:
        try:
            n = reset_stale_claimed(stale_minutes=30)
            if n:
                log(f"[bridge] stale claimed {n}개 → pending 복원", "warn")
            # 캡차도 같이 — 10분 이상 답변 없는 pending 정리
            reset_stale_captcha(stale_minutes=10)
        except Exception:
            pass
        time.sleep(30)


# ─── Telegram 캡차 relay ─────────────────────────────────────────────────────

def _telegram_send_photo(image_b64: str, caption: str) -> Optional[int]:
    """캡차 이미지를 텔레그램으로 발송. message_id 반환 (실패 시 None).

    force_reply 로 사용자가 폰에서 답글 입력 UI 자동 노출.
    """
    import base64
    try:
        import requests
    except ImportError:
        log("[bridge] requests 미설치 — 텔레그램 발송 불가", "error")
        return None
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        log("[bridge] TELEGRAM_BOT_TOKEN/CHAT_ID 미설정 — 캡차 relay 불가", "error")
        return None
    try:
        image_bytes = base64.b64decode(image_b64)
        r = requests.post(
            f"https://api.telegram.org/bot{token}/sendPhoto",
            data={
                "chat_id": chat_id,
                "caption": caption,
                "reply_markup": json.dumps({
                    "force_reply": True,
                    "input_field_placeholder": "DKAPTCHA 답안",
                }),
            },
            files={"photo": ("captcha.png", image_bytes, "image/png")},
            timeout=15,
        )
        if not r.ok:
            log(f"[bridge] Telegram sendPhoto {r.status_code}: {r.text[:200]}", "error")
            return None
        return r.json().get("result", {}).get("message_id")
    except Exception as e:
        log(f"[bridge] Telegram sendPhoto 예외: {e}", "error")
        return None


def _telegram_long_poll_loop() -> None:
    """텔레그램 봇 getUpdates long-poll — 사용자가 캡차 메시지에 답글 달면 수신.

    reply_to_message.message_id 로 어느 캡차 요청의 답변인지 매칭 →
    set_captcha_answer() 로 저장. content.js 가 polling 으로 가져감.
    """
    try:
        import requests
    except ImportError:
        return
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        return
    offset = 0
    log("[bridge] Telegram long-poll 시작", "info")
    while True:
        try:
            r = requests.get(
                f"https://api.telegram.org/bot{token}/getUpdates",
                params={"offset": offset, "timeout": 25},
                timeout=30,
            )
            if not r.ok:
                time.sleep(5)
                continue
            data = r.json()
            for upd in data.get("result", []):
                offset = upd["update_id"] + 1
                msg = upd.get("message", {})
                reply_to = msg.get("reply_to_message")
                if not reply_to:
                    continue
                reply_msg_id = reply_to.get("message_id")
                text = (msg.get("text") or "").strip()
                if not text or not reply_msg_id:
                    continue
                item_id = find_item_by_tg_message_id(reply_msg_id)
                if item_id:
                    set_captcha_answer(item_id, text)
                    log(f"[bridge] 텔레그램 캡차 답안 수신: item={item_id[:8]} "
                        f"answer={text[:20]}", "ok")
        except Exception as e:
            log(f"[bridge] Telegram long-poll 예외 (5초 후 재시도): {e}", "warn")
            time.sleep(5)


def _port_in_use(host: str, port: int) -> bool:
    """포트가 이미 listen 중인지 검사."""
    import socket
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(0.5)
    try:
        sock.connect((host, port))
        return True
    except (ConnectionRefusedError, OSError):
        return False
    finally:
        sock.close()


def start_server_in_thread(host: str = "127.0.0.1", port: int = _DEFAULT_PORT) -> bool:
    """bridge HTTP 서버를 daemon thread 로 띄움.

    scheduler_runner 가 시작 시 호출 — 별도 터미널 없이 통합 운영.

    Returns:
        True: 새로 띄움 (또는 이미 활성)
        False: 띄우지 못함 (port 충돌이 아닌 진짜 에러)
    """
    if _port_in_use(host, port):
        log(f"[bridge] {host}:{port} 이미 사용 중 — 임베드 skip (별도 프로세스 실행 중이거나 다른 서비스)", "info")
        return True  # 외부 bridge 가 살아있으면 그것 그대로 활용

    def _run():
        threading.Thread(target=_stale_reset_loop, daemon=True).start()
        threading.Thread(target=_telegram_long_poll_loop, daemon=True).start()
        server = ThreadingHTTPServer((host, port), BridgeHandler)
        log(f"[bridge] embedded — listening on http://{host}:{port}", "step")
        try:
            server.serve_forever()
        except Exception as e:
            log(f"[bridge] embedded 서버 종료: {e}", "warn")

    t = threading.Thread(target=_run, daemon=True, name="tistory-bridge")
    t.start()
    # 시작 확인 — 0.5초 정도 후 포트가 열렸는지 한 번 더 점검
    time.sleep(0.5)
    if _port_in_use(host, port):
        log(f"[bridge] embedded 시작 완료 (스케줄러 프로세스 내 thread)", "ok")
        return True
    log(f"[bridge] embedded 시작 검증 실패 — 포트가 안 열림", "warn")
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description="Tistory bridge server")
    parser.add_argument("--port", type=int, default=_DEFAULT_PORT)
    parser.add_argument("--host", default="127.0.0.1",
                         help="localhost 만 허용 (기본). 0.0.0.0 은 외부 노출 위험")
    args = parser.parse_args()

    # stale 회복 + 텔레그램 캡차 long-poll 백그라운드 스레드
    threading.Thread(target=_stale_reset_loop, daemon=True).start()
    threading.Thread(target=_telegram_long_poll_loop, daemon=True).start()

    addr = (args.host, args.port)
    server = ThreadingHTTPServer(addr, BridgeHandler)
    log(f"[bridge] listening on http://{args.host}:{args.port}", "step")
    log(f"[bridge] queue: {len(list_all('pending'))} pending / {len(list_all('done'))} done", "info")
    log("[bridge] 사용자 평소 Chrome 에 extension 설치 + 활성 상태여야 동작", "info")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log("[bridge] 종료 요청 받음", "warn")
    return 0


if __name__ == "__main__":
    sys.exit(main())
