"""
알림 모듈 — 텔레그램 + 카카오톡 나와의 채팅 병행 발송.

채널 우선순위:
  1. 텔레그램 (TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID)
  2. 카카오톡 나와의 채팅 (KAKAO_ACCESS_TOKEN, 자동 갱신)

두 채널 모두 설정됐으면 동시 발송. 한쪽만 있어도 정상 동작.
"""
import os
import traceback
from datetime import datetime

import requests

from .logger import log


# ─── 텔레그램 ─────────────────────────────────────────────────────────────────

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "")


def _send_telegram(text: str) -> bool:
    if not BOT_TOKEN or not CHAT_ID:
        return False
    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={
                "chat_id": CHAT_ID,
                "text": text[:4096],
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
            timeout=10,
        )
        return resp.ok
    except Exception:
        return False


# ─── 카카오톡 나와의 채팅 ──────────────────────────────────────────────────────

_KAKAO_MSG_URL = "https://kapi.kakao.com/v2/api/talk/memo/default/send"

# HTML 태그를 카카오 plain text 로 변환하는 최소 처리
def _strip_html(text: str) -> str:
    import re
    text = re.sub(r"<b>(.*?)</b>", r"\1", text)
    text = re.sub(r"<[^>]+>", "", text)
    return text


def _send_kakao(text: str) -> bool:
    """카카오톡 나에게 메시지 전송. access_token 만료 시 자동 갱신 1회 시도."""
    from common.kakao_token import get_access_token, refresh_access_token

    plain = _strip_html(text)

    def _post(token: str) -> requests.Response:
        import json
        template = json.dumps({
            "object_type": "text",
            "text": plain[:200],
            "link": {"web_url": "", "mobile_web_url": ""},
        }, ensure_ascii=False)
        return requests.post(
            _KAKAO_MSG_URL,
            headers={"Authorization": f"Bearer {token}"},
            data={"template_object": template},
            timeout=10,
        )

    token = get_access_token()
    if not token:
        return False

    try:
        resp = _post(token)

        # access_token 만료(401) 시 갱신 후 1회 재시도
        if resp.status_code == 401:
            log("카카오 access_token 만료, 갱신 시도", "warn")
            token = refresh_access_token()
            if not token:
                return False
            resp = _post(token)

        if resp.ok:
            return True
        log(f"카카오 메시지 전송 실패: {resp.status_code} {resp.text[:200]}", "error")
        return False
    except Exception as e:
        log(f"카카오 메시지 전송 오류: {e}", "error")
        return False


# ─── 통합 발송 ────────────────────────────────────────────────────────────────

def _notify(text: str) -> None:
    """텔레그램 단일 발송.

    카카오톡 '나와의 채팅' 발송은 사용자 요청으로 비활성화 (텔레그램만 사용).
    _send_kakao 함수 자체는 남겨두어 필요 시 한 줄 추가로 다시 켤 수 있다.
    """
    tg_ok = _send_telegram(text)
    if tg_ok:
        log("텔레그램 알림 전송", "info")


# ─── 캘린더 기록 ───────────────────────────────────────────────────────────────

def _record_calendar_failure(pipeline: str, detail: str, *, partial: bool = False) -> None:
    """실패/부분실패 시 톡캘린더 '자동발행기록' 에 이벤트 등록. 실패해도 조용히 무시."""
    try:
        from common.kakao_calendar import record_failure
        record_failure(pipeline, detail, partial=partial)
    except Exception as e:
        log(f"캘린더 기록 실패 (무시): {e}", "warn")


# ─── Public API ───────────────────────────────────────────────────────────────

def notify_pipeline_result(pipeline: str, published: int, total: int,
                           details: str = "", *, reason: str = "failure",
                           url: str = "") -> None:
    """파이프라인 실행 결과 알림.

    Args:
        reason: published==0 일 때 의미 분류.
            "failure" (기본) — 외부 의존성/내부 결함으로 발행 못 함, 캘린더 ❌ 등록
            "empty"          — 발행 대상이 없는 정상 종료, 캘린더 등록 안 함
        url:    발행된 글의 URL (성공 시에만). 단축 URL 로 변환해 본문에 포함.
    """
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    if published == total and published > 0:
        emoji = "✅"
    elif published > 0:
        emoji = "⚠️"
    elif reason == "empty":
        emoji = "ℹ️"
    else:
        emoji = "❌"

    text = (
        f"{emoji} <b>[Auto Publishing]</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📌 {pipeline}\n"
        f"📊 {published}/{total}건 발행\n"
    )
    if details:
        text += f"📝 {details}\n"
    if url:
        # 100자 이내 URL 은 이미 충분히 짧아서 단축 불필요 (텔레그램에서 클릭
        # 가능한 자동 링크 처리에도 안정적). 100자 초과 URL 만 단축 시도.
        if len(url) > 100:
            try:
                from common.url_shortener import shorten as _shorten
                short = _shorten(url) or url
            except Exception:
                short = url
        else:
            short = url
        text += f"🔗 {short}\n"
    text += f"🕒 {now}"

    _notify(text)

    # 실패/부분실패 시 톡캘린더 기록 (성공·empty 는 기록 안 함)
    if total > 0 and published < total and reason != "empty":
        partial = published > 0  # published==0 이면 완전실패, 1 이상이면 부분실패
        detail = f"{published}/{total}건 발행"
        if details:
            detail += f" — {details}"
        _record_calendar_failure(pipeline, detail, partial=partial)


def notify_error(pipeline: str, error: Exception) -> None:
    """에러 발생 알림."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    tb = traceback.format_exception_only(type(error), error)
    err_msg = "".join(tb).strip()[:500]

    text = (
        f"🚨 <b>[Auto Publishing 오류]</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📌 {pipeline}\n"
        f"❌ {err_msg}\n"
        f"🕒 {now}"
    )
    _notify(text)

    # 예외 기반 실패 → 완전실패로 캘린더 기록
    _record_calendar_failure(pipeline, err_msg, partial=False)


def notify_login_intervention(platform: str, hint: str, url: str = "") -> None:
    """로그인 중 사용자 개입(캡차/2단계/추가 인증)이 필요할 때 즉시 통지.

    티스토리 카카오 SSO 흐름에서 ID/PW 입력 후 추가 인증 화면이 뜨면
    publisher 가 120초 timeout 까지 대기한다 — 사용자에게 빠르게 알려야
    수동으로 캡차/2단계를 처리할 수 있다.
    """
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    hint_line = (hint or "").strip()[:300]
    url_line = (url or "").strip()
    text = (
        f"⚠️ <b>[Auto Publishing 로그인 개입 필요]</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📌 {platform}\n"
        f"💡 {hint_line or '추가 인증/캡차 화면 감지'}\n"
        + (f"🔗 {url_line[:200]}\n" if url_line else "")
        + f"🕒 {now}"
    )
    _notify(text)


def notify_login_required(platform: str, instructions: str = "",
                          *, throttle_hours: int = 24) -> None:
    """자동 로그인 가드 발동 — 사용자가 직접 1회 로그인해야 storage 발급되는 상황.

    예: 알리 약관 동의 → 카카오 redirect 실패 → 가드 발동.
    같은 platform 으로 throttle_hours 시간 내 재호출 시 스팸 방지로 skip.

    instructions 예: "ALIEXPRESS_HEADLESS=false python -m common.aliexpress_login"
    """
    import json as _json
    from pathlib import Path as _Path

    # throttle 검사
    try:
        alerts_path = _Path(__file__).resolve().parent.parent / "data" / "login_alerts.json"
        alerts: dict = {}
        if alerts_path.exists():
            try:
                alerts = _json.loads(alerts_path.read_text(encoding="utf-8"))
            except Exception:
                alerts = {}
        last_ts = alerts.get(platform, 0)
        import time as _time
        now_ts = int(_time.time())
        if now_ts - last_ts < throttle_hours * 3600:
            log(f"[로그인 알림] '{platform}' throttle 중 — skip", "info")
            return
        alerts[platform] = now_ts
        alerts_path.parent.mkdir(parents=True, exist_ok=True)
        alerts_path.write_text(_json.dumps(alerts, ensure_ascii=False, indent=2),
                               encoding="utf-8")
    except Exception as e:
        log(f"[로그인 알림] throttle 처리 예외 (무시): {e}", "warn")

    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    text = (
        f"🔐 <b>[Auto Publishing 수동 로그인 필요]</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📌 {platform}\n"
        f"📝 자동 로그인이 차단됐습니다 (약관/봇탐지/세션만료).\n"
        + (f"💡 {instructions}\n" if instructions else "")
        + f"🕒 {now}\n"
        + f"━━━━━━━━━━━━━━━━━━━━\n"
        + f"※ {throttle_hours}시간 내 동일 알림은 발송 생략됩니다."
    )
    _notify(text)


def notify_scheduler_start(job_count: int) -> None:
    """스케줄러 시작 알림."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    text = (
        f"🚀 <b>[Auto Publishing 스케줄러]</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📋 {job_count}개 스케줄 등록 완료\n"
        f"⏳ 실행 대기 중...\n"
        f"🕒 {now}"
    )
    _notify(text)
