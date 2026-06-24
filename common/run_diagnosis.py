"""
파이프라인 실행 실패의 원인 분류.

run_ledger 의 record 를 받아 흔한 실패 패턴을 매칭하고 사람이 읽기 좋은
원인 라벨을 반환한다. 매칭 실패 시 stderr 마지막 줄을 그대로 노출해 사용자가
원본 메시지로 판단할 수 있게 한다.

매핑 우선순위 (위에서부터 먼저 매칭):
  1. data.go.kr 인증키 401/등록되지 않은 인증키
  2. Kakao/티스토리 세션 만료 (auth/login 리다이렉트, Kakao 로그인 실패)
  3. Naver 봇 탐지/로그인 차단
  4. Playwright timeout / browser launch 실패
  5. Threads/Twitter rate-limit
  6. subprocess timeout
  7. (폴백) stderr 의 마지막 의미 있는 한 줄
"""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class Diagnosis:
    label: str   # 짧은 한국어 라벨 (텔레그램 1줄)
    hint: str    # 사용자 조치 힌트 (선택; 비어 있을 수 있음)


# (정규식, 라벨, 조치 힌트) 의 우선순위 리스트.
# stderr_tail 전체에 대해 search 수행.
_PATTERNS: list[tuple[re.Pattern, str, str]] = [
    (
        re.compile(r"등록되지\s*않은\s*인증키|DATA_GO_KR_KEY\s*env\s*누락", re.I),
        "data.go.kr 인증키 폐기/만료",
        ".env DATA_GO_KR_KEY 재발급 후 교체",
    ),
    (
        re.compile(r"data\.go\.kr.*?(401|Unauthorized)", re.I | re.S),
        "data.go.kr 401 (인증키 거부)",
        "data.go.kr 마이페이지에서 활용 상태 확인 / 재발급",
    ),
    (
        # 뉴스픽 전용 — tistory 패턴보다 먼저 매칭돼야 올바른 복구 명령을 안내한다.
        re.compile(r"뉴스픽\s*세션\s*없음|Kakao\s*SSO\s*로그인\s*실패|newspic", re.I),
        "뉴스픽 Kakao 세션 만료",
        "python tools/newspick_manual_login.py 로 수동 로그인",
    ),
    (
        re.compile(r"/auth/login|Kakao\s*로그인\s*실패|Kakao\s*페이지\s*전환\s*실패", re.I),
        "Kakao(티스토리) 세션 만료",
        "python -m tools.verify_tistory_login <blog>",
    ),
    (
        re.compile(r"수동\s*로그인\s*필요|notify_login_required", re.I),
        "수동 로그인 가드 발동",
        "텔레그램의 instructions 명령 실행",
    ),
    (
        re.compile(r"naver.*?(login|로그인).*?(차단|차단됨|실패|봇)", re.I | re.S),
        "Naver 로그인 차단/봇 탐지",
        "python tools/naver_manual_login.py 로 수동 로그인",
    ),
    (
        re.compile(r"Threads.*?rate.?limit|status.*?429", re.I),
        "Threads/SNS API rate-limit",
        "잠시 대기 후 재시도 (자동 회복)",
    ),
    (
        re.compile(r"subprocess\s*timeout\s*\d+s|TimeoutError", re.I),
        "subprocess 타임아웃",
        "SCHEDULE_SUBPROCESS_TIMEOUT 상향 또는 외부 응답 지연 확인",
    ),
    (
        re.compile(r"playwright.*?(Timeout|Error)|browser.*?(launch|context).*?failed", re.I),
        "Playwright 브라우저 오류",
        "Chromium 재설치 / orphan 프로세스 정리",
    ),
    (
        re.compile(r"ConnectionError|ConnectTimeout|Max retries exceeded|requests\.exceptions", re.I),
        "외부 네트워크 오류",
        "일시적 — 다음 슬롯에 자동 회복 가능",
    ),
    (
        # 알리 수집 0건 — 명품/한국 고유명사 등 알리 부적합 키워드. 진짜 장애가
        # 아니므로 네트워크/Playwright 패턴 뒤에 둬 그것들이 먼저 매칭되게 한다.
        re.compile(r"수집\s*0건|상품/제휴링크\s*수집|상품/링크\s*수집\s*실패|키워드\s*부적합|매칭\s*부족", re.I),
        "알리 키워드 미매칭 (검색 0건)",
        "해당 키워드 풀에서 자동 제외됨 — 조치 불필요 (자가 치유)",
    ),
]


def _last_meaningful_line(text: str) -> str:
    """stderr 의 마지막 의미있는 한 줄 (공백/INFO 제외)."""
    if not text:
        return ""
    for line in reversed(text.splitlines()):
        s = line.strip()
        if not s:
            continue
        # ANSI 색상코드 제거
        s = re.sub(r"\x1b\[[0-9;]*m", "", s)
        if not s:
            continue
        return s[:200]
    return ""


def diagnose(record: dict) -> Diagnosis:
    """run_ledger record 를 받아 Diagnosis 반환."""
    status = record.get("status", "")
    if status == "success":
        return Diagnosis(label="성공", hint="")

    stderr = record.get("stderr_tail") or ""
    error  = record.get("error") or ""
    haystack = stderr + "\n" + error

    if status == "timeout":
        return Diagnosis(
            label="subprocess 타임아웃",
            hint="SCHEDULE_SUBPROCESS_TIMEOUT 상향 또는 외부 응답 지연 확인",
        )

    for pat, label, hint in _PATTERNS:
        if pat.search(haystack):
            return Diagnosis(label=label, hint=hint)

    # 폴백 — stderr 마지막 의미있는 한 줄
    tail = _last_meaningful_line(stderr) or _last_meaningful_line(error)
    if tail:
        return Diagnosis(label=tail, hint="")
    return Diagnosis(label=f"원인 불명 (exit={record.get('exit_code')})", hint="")
