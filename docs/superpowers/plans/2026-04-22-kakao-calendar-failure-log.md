# Kakao Talk Calendar 발행 실패 기록 — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 파이프라인이 실패 또는 부분 실패로 끝나면 Kakao Talk Calendar의 전용 서브캘린더 "자동발행기록"에 5분짜리 이벤트를 자동 등록한다. 성공 이벤트는 기록하지 않는다.

**Architecture:** `common/kakao_calendar.py` 신규 모듈이 서브캘린더 확보/이벤트 등록을 담당하고, `common/notifier.py`의 기존 `notify_pipeline_result` / `notify_error`에 얇은 훅(`_record_calendar_failure`)을 추가한다. 기존 텔레그램/카톡 발송과 동일한 "예외 격리" 패턴을 따라 캘린더 기록 실패가 파이프라인 결과에 영향을 주지 않는다. 카카오 OAuth 토큰은 기존 `common/kakao_token.py`를 재사용하고, 401 시 자동 갱신 후 1회 재시도한다.

**Tech Stack:** Python 3.x, `requests`, `python-dotenv`, Kakao Talk Calendar REST API (`https://kapi.kakao.com/v2/api/calendar/*`)

**Prerequisite (수동 1회):**
1. https://developers.kakao.com 앱 콘솔 → 동의항목에서 `talk_calendar` ON
2. `python scripts/kakao_auth.py` 재실행 (새 scope 포함 토큰 재발급)
3. `.env`의 `KAKAO_ACCESS_TOKEN` / `KAKAO_REFRESH_TOKEN` 갱신 확인

---

## File Structure

| 파일 | 작업 | 책임 |
|---|---|---|
| `common/kakao_calendar.py` | Create | 서브캘린더 확보 + 이벤트 등록. 단일 퍼블릭 함수 `record_failure()` |
| `common/notifier.py` | Modify | `notify_pipeline_result` / `notify_error`에 캘린더 훅 추가 |
| `scripts/kakao_auth.py` | Modify | OAuth scope에 `talk_calendar` 추가 |
| `data/kakao_calendar.json` | Runtime create | calendar_id 캐시 (이미 gitignore됨) |
| `tools/test_kakao_calendar.py` | Create | 라이브 API 스모크 테스트 스크립트 |
| `README.md` | Modify | `talk_calendar` scope 재발급 절차 안내 |

---

## Task 1: Kakao 콘솔 설정 + scope 업데이트 (수동 + 코드 1줄)

**Files:**
- Modify: `scripts/kakao_auth.py:42`

- [ ] **Step 1: 카카오 개발자 콘솔에서 scope 활성화**

  https://developers.kakao.com 로그인 → 내 애플리케이션 → (Auto Publishing 앱) → 동의항목:
  - `카카오톡 메시지 전송` (이미 ON) 확인
  - `캘린더` (`talk_calendar`) → **필수 동의** 또는 **선택 동의**로 설정

  스크린샷이나 확인 불가 시 다음 Step에서 실패하면 재확인.

- [ ] **Step 2: `scripts/kakao_auth.py`의 scope 문자열 수정**

  현재:
  ```python
  "scope":         "talk_message",
  ```
  변경:
  ```python
  "scope":         "talk_message,talk_calendar",
  ```

- [ ] **Step 3: OAuth 재실행해 토큰 재발급**

  ```bash
  python scripts/kakao_auth.py
  ```
  Expected:
  - 브라우저에서 카카오 로그인 + 동의 화면에 "캘린더" 항목이 표시되어야 함
  - 스크립트 종료 시 `✅ 토큰 발급 완료!` + `.env` 갱신 로그
  - 테스트 메시지 "Auto Publishing 카카오 알림 연결 완료!"가 카카오톡 나와의 채팅에 도착

- [ ] **Step 4: 새 토큰으로 캘린더 API 접근 가능한지 즉석 확인**

  ```bash
  source .env 2>/dev/null || true
  curl -s -H "Authorization: Bearer $KAKAO_ACCESS_TOKEN" \
    "https://kapi.kakao.com/v2/api/calendar/calendars"
  ```
  Expected: `{"calendars":[...], "subscribe_calendars":[...]}` 형태 JSON. 403/401이면 Step 1 재확인.

- [ ] **Step 5: Commit**

  ```bash
  git add scripts/kakao_auth.py
  git commit -m "feat(kakao-auth): add talk_calendar scope for event logging"
  ```

---

## Task 2: `common/kakao_calendar.py` — 서브캘린더 생성 함수 (TDD)

**Files:**
- Create: `common/kakao_calendar.py`
- Create: `tools/test_kakao_calendar.py` (스모크 스크립트, 이 Task에서 초기 셸만)

이 프로젝트는 유닛테스트 인프라가 없고 라이브 API 의존이 강하므로, 각 Task마다 **라이브 스모크 검증 스크립트를 작성→실행→확인** 하는 패턴을 사용한다.

- [ ] **Step 1: 스모크 스크립트 셸 작성 — 실패 상태 확인용**

  Create `tools/test_kakao_calendar.py`:
  ```python
  """
  common/kakao_calendar 라이브 스모크 테스트.

  실행:
      python -m tools.test_kakao_calendar ensure
      python -m tools.test_kakao_calendar record
      python -m tools.test_kakao_calendar all
  """
  import sys
  from datetime import datetime

  from common.kakao_calendar import ensure_calendar, record_failure
  from common.logger import log


  def cmd_ensure():
      log("ensure_calendar() 호출", "step")
      cal_id = ensure_calendar()
      if cal_id:
          log(f"calendar_id = {cal_id}", "ok")
          log("카카오톡 앱 → 톡캘린더에서 '자동발행기록' 서브캘린더 확인하세요", "info")
      else:
          log("calendar_id 획득 실패", "error")
          sys.exit(1)


  def cmd_record():
      log("record_failure() 호출 — 3 케이스", "step")

      # 1) 완전 실패
      ok1 = record_failure("test_pipeline", "완전 실패 스모크 테스트 — RED")
      log(f"[1] 완전실패 등록: {ok1}", "ok" if ok1 else "error")

      # 2) 부분 실패
      ok2 = record_failure("test_pipeline", "2/5건 발행", partial=True)
      log(f"[2] 부분실패 등록: {ok2}", "ok" if ok2 else "error")

      # 3) 긴 detail truncate
      ok3 = record_failure("test_pipeline", "x" * 600)
      log(f"[3] 600자 truncate 등록: {ok3}", "ok" if ok3 else "error")

      log("카카오톡 앱 → 톡캘린더 '자동발행기록' 에서 3건 확인하세요", "info")


  def main():
      args = sys.argv[1:] or ["all"]
      cmd = args[0]
      if cmd == "ensure":
          cmd_ensure()
      elif cmd == "record":
          cmd_record()
      elif cmd == "all":
          cmd_ensure()
          cmd_record()
      else:
          print(f"Unknown command: {cmd}")
          sys.exit(2)


  if __name__ == "__main__":
      main()
  ```

- [ ] **Step 2: 스크립트 실행해 실패 확인 (모듈이 아직 없음)**

  Run:
  ```bash
  python -m tools.test_kakao_calendar ensure
  ```
  Expected: `ModuleNotFoundError: No module named 'common.kakao_calendar'`

- [ ] **Step 3: `common/kakao_calendar.py` 초안 작성 — 서브캘린더 확보 로직만**

  Create `common/kakao_calendar.py`:
  ```python
  """
  Kakao Talk Calendar 연동 — 발행 실패 이벤트 자동 기록.

  - 최초 1회 서브캘린더 '자동발행기록' 생성 후 calendar_id 를
    data/kakao_calendar.json 에 캐시 (gitignore 대상)
  - record_failure() 가 notifier 로부터 호출됨. 예외를 전파하지 않음.
  - 401 시 common.kakao_token.refresh_access_token() 1회 호출 후 재시도.

  필요 scope: talk_calendar  (scripts/kakao_auth.py 참조)
  """
  from __future__ import annotations

  import json
  import os
  from datetime import datetime, timedelta, timezone

  import requests

  from common.kakao_token import get_access_token, refresh_access_token
  from common.logger import log


  _API_BASE      = "https://kapi.kakao.com/v2/api/calendar"
  _CACHE_PATH    = os.path.join(os.path.dirname(__file__), "..", "data", "kakao_calendar.json")
  _CALENDAR_NAME = "자동발행기록"
  _KST           = timezone(timedelta(hours=9))
  _DETAIL_MAX    = 500


  # ─── 캐시 ─────────────────────────────────────────────────────────────────────

  def _cache_path() -> str:
      return os.path.abspath(_CACHE_PATH)


  def _load_cached_id() -> str:
      try:
          with open(_cache_path(), encoding="utf-8") as f:
              return (json.load(f) or {}).get("calendar_id", "") or ""
      except (FileNotFoundError, json.JSONDecodeError):
          return ""


  def _save_cached_id(calendar_id: str) -> None:
      os.makedirs(os.path.dirname(_cache_path()), exist_ok=True)
      with open(_cache_path(), "w", encoding="utf-8") as f:
          json.dump({
              "calendar_id": calendar_id,
              "name":        _CALENDAR_NAME,
              "created_at":  datetime.now(_KST).isoformat(timespec="seconds"),
          }, f, ensure_ascii=False, indent=2)


  # ─── API 호출 헬퍼 ────────────────────────────────────────────────────────────

  def _request(method: str, path: str, *, data: dict | None = None) -> requests.Response | None:
      """POST 전용. 401 발생 시 refresh 후 1회 재시도. 예외는 None 반환."""
      token = get_access_token()
      if not token:
          log("카카오 access_token 없음 — 캘린더 기록 건너뜀", "warn")
          return None

      url = f"{_API_BASE}{path}"
      try:
          resp = requests.request(
              method, url,
              headers={"Authorization": f"Bearer {token}"},
              data=data,
              timeout=10,
          )
          if resp.status_code == 401:
              log("카카오 캘린더 401 — 토큰 갱신 후 재시도", "warn")
              new_token = refresh_access_token()
              if not new_token:
                  return None
              resp = requests.request(
                  method, url,
                  headers={"Authorization": f"Bearer {new_token}"},
                  data=data,
                  timeout=10,
              )
          return resp
      except Exception as e:
          log(f"카카오 캘린더 요청 오류: {e}", "error")
          return None


  # ─── 서브캘린더 확보 ───────────────────────────────────────────────────────────

  def ensure_calendar() -> str:
      """
      '자동발행기록' 서브캘린더의 calendar_id 반환.
      - 캐시에 있으면 그대로 반환
      - 없으면 기존 캘린더 목록에서 같은 이름 검색 → calendar_id 캐시
      - 그래도 없으면 신규 생성 → calendar_id 캐시
      - 실패 시 빈 문자열 반환 (호출부가 조용히 skip)
      """
      cached = _load_cached_id()
      if cached:
          return cached

      # 기존 목록 조회 (GET 은 별도 처리 — requests.get 직접 호출)
      token = get_access_token()
      if not token:
          return ""

      try:
          resp = requests.get(
              f"{_API_BASE}/calendars",
              headers={"Authorization": f"Bearer {token}"},
              timeout=10,
          )
          if resp.status_code == 401:
              token = refresh_access_token()
              if not token:
                  return ""
              resp = requests.get(
                  f"{_API_BASE}/calendars",
                  headers={"Authorization": f"Bearer {token}"},
                  timeout=10,
              )
          if not resp.ok:
              log(f"카카오 캘린더 목록 조회 실패: {resp.status_code} {resp.text[:200]}", "error")
              return ""

          calendars = (resp.json() or {}).get("calendars", []) or []
          for cal in calendars:
              if cal.get("name") == _CALENDAR_NAME:
                  cal_id = cal.get("id", "")
                  if cal_id:
                      _save_cached_id(cal_id)
                      log(f"기존 '{_CALENDAR_NAME}' 캘린더 재사용: {cal_id}", "ok")
                      return cal_id
      except Exception as e:
          log(f"카카오 캘린더 목록 조회 오류: {e}", "error")
          return ""

      # 신규 생성
      resp = _request("POST", "/create/calendar", data={
          "name":  _CALENDAR_NAME,
          "color": "RED",
      })
      if not resp or not resp.ok:
          body = resp.text[:200] if resp is not None else "no response"
          log(f"카카오 캘린더 생성 실패: {body}", "error")
          return ""

      cal_id = (resp.json() or {}).get("calendar_id", "")
      if not cal_id:
          log(f"캘린더 생성 응답 이상: {resp.text[:200]}", "error")
          return ""

      _save_cached_id(cal_id)
      log(f"'{_CALENDAR_NAME}' 캘린더 신규 생성: {cal_id}", "ok")
      return cal_id


  # ─── Public: record_failure (Task 3 에서 구현) ─────────────────────────────────

  def record_failure(pipeline: str, detail: str, *,
                     partial: bool = False,
                     started_at: datetime | None = None) -> bool:
      """Task 3 에서 구현."""
      raise NotImplementedError
  ```

- [ ] **Step 4: 스모크 스크립트 ensure 실행 — 캘린더 생성 확인**

  Run:
  ```bash
  python -m tools.test_kakao_calendar ensure
  ```
  Expected:
  - 콘솔에 `'자동발행기록' 캘린더 신규 생성: sub_XXXXXXXX` 로그
  - `data/kakao_calendar.json` 파일 생성됨
  - 카카오톡 모바일 앱 → 톡캘린더 → 좌상단 캘린더 리스트에 "자동발행기록" 항목 표시

  Verify:
  ```bash
  cat data/kakao_calendar.json
  ```
  Expected: `{"calendar_id": "...", "name": "자동발행기록", "created_at": "..."}`

- [ ] **Step 5: 두 번째 실행 — 캐시 hit 확인**

  Run:
  ```bash
  python -m tools.test_kakao_calendar ensure
  ```
  Expected: 신규 생성 로그가 뜨지 않고 `calendar_id = sub_XXXXXXXX` 만 출력 (캐시 경로)

- [ ] **Step 6: Commit**

  ```bash
  git add common/kakao_calendar.py tools/test_kakao_calendar.py
  git commit -m "feat(kakao-calendar): add ensure_calendar() and smoke script"
  ```

---

## Task 3: `record_failure()` 이벤트 등록 구현 (TDD)

**Files:**
- Modify: `common/kakao_calendar.py` (Task 2에서 `NotImplementedError` 스텁 → 실제 구현)

- [ ] **Step 1: 스모크 record 실행해 실패 확인 (스텁이 NotImplementedError 던짐)**

  Run:
  ```bash
  python -m tools.test_kakao_calendar record
  ```
  Expected: `NotImplementedError` 로 즉시 종료 — 아직 구현 안 됨 확인

- [ ] **Step 2: `record_failure()` 구현 — stub 자리에 실제 코드 삽입**

  `common/kakao_calendar.py` 의 스텁 함수를 아래로 교체:
  ```python
  def record_failure(pipeline: str, detail: str, *,
                     partial: bool = False,
                     started_at: datetime | None = None) -> bool:
      """
      실패 이벤트를 '자동발행기록' 서브캘린더에 등록한다.

      - pipeline: 파이프라인 이름 (예: 'coupang_to_wordpress')
      - detail:   실패 요약 (에러 메시지 또는 '2/5건 발행'). 500자 truncate
      - partial:  True면 부분실패(⚠️ ORANGE), False면 완전실패(❌ RED)
      - started_at: 실패 시각 (기본: now, KST)

      반환: 성공 True / 실패 False. 예외는 내부에서 흡수.
      """
      try:
          cal_id = ensure_calendar()
          if not cal_id:
              return False

          start = started_at or datetime.now(_KST)
          if start.tzinfo is None:
              start = start.replace(tzinfo=_KST)
          # Kakao API 제약: start_at 은 5분 격자 정렬 필수 (floor) — 미정렬 시 code=-2 거부
          start = start.replace(minute=(start.minute // 5) * 5, second=0, microsecond=0)
          end = start + timedelta(minutes=5)

          title_prefix = "⚠️" if partial else "❌"
          title = f"[{title_prefix} {pipeline}] {'부분실패' if partial else '실패'}"

          short_detail = (detail or "")[:_DETAIL_MAX]

          event_payload = {
              "calendar_id": cal_id,
              "event": json.dumps({
                  "title":       title,
                  "time":        {
                      "start_at":  start.isoformat(timespec="seconds"),
                      "end_at":    end.isoformat(timespec="seconds"),
                      "time_zone": "Asia/Seoul",
                      "all_day":   False,
                  },
                  "description": short_detail,
                  "color":       "ORANGE" if partial else "RED",
                  "reminders":   [],
              }, ensure_ascii=False),
          }

          resp = _request("POST", "/create/event", data=event_payload)
          if not resp or not resp.ok:
              body = resp.text[:200] if resp is not None else "no response"
              log(f"캘린더 이벤트 등록 실패: {body}", "warn")
              return False

          log(f"캘린더 이벤트 등록: {title}", "ok")
          return True

      except Exception as e:
          log(f"record_failure 내부 예외 (무시): {e}", "warn")
          return False
  ```

- [ ] **Step 3: 스모크 record 실행 — 3건 등록 확인**

  Run:
  ```bash
  python -m tools.test_kakao_calendar record
  ```
  Expected 콘솔:
  ```
  [1] 완전실패 등록: True
  [2] 부분실패 등록: True
  [3] 600자 truncate 등록: True
  ```

- [ ] **Step 4: 카카오톡 앱에서 육안 확인**

  모바일 카카오톡 → 톡캘린더 → "자동발행기록" 캘린더 필터링:
  - `[❌ test_pipeline] 실패` (RED) 1건
  - `[⚠️ test_pipeline] 부분실패` (ORANGE) 1건
  - `[❌ test_pipeline] 실패` (RED, 본문 500자로 잘린 x xxxxx...) 1건

  모두 현재 시각 기준 5분짜리 이벤트여야 함.

- [ ] **Step 5: 401 재시도 경로 수동 검증 (선택)**

  `.env` 의 `KAKAO_ACCESS_TOKEN` 값 끝에 `x` 한 글자 추가 → 강제로 만료 상태 유도:
  ```bash
  python -m tools.test_kakao_calendar record
  ```
  Expected 로그: `카카오 캘린더 401 — 토큰 갱신 후 재시도` 뜬 뒤 이벤트 등록 성공. 토큰은 refresh로 자동 복구됨.

- [ ] **Step 6: Commit**

  ```bash
  git add common/kakao_calendar.py
  git commit -m "feat(kakao-calendar): implement record_failure() with 401 retry"
  ```

---

## Task 4: `common/notifier.py` 훅 추가

**Files:**
- Modify: `common/notifier.py:114-151` (`notify_pipeline_result` / `notify_error` 본문 하단)

- [ ] **Step 1: import 확인**

  `common/notifier.py` 최상단에 `from .logger import log` 가 이미 존재하는지 확인:
  ```bash
  grep -n "from .logger import log" common/notifier.py
  ```
  Expected: 라인 16 근처에 이미 존재. 없으면 `import requests` 아래에 추가.

  캘린더 모듈은 **순환 참조/시작 시간 영향 회피**를 위해 Step 2 의 함수 **내부**에서 지연 import 한다 (모듈 상단 import 금지).

- [ ] **Step 2: `_record_calendar_failure` 내부 헬퍼 추가**

  `common/notifier.py` 의 `# ─── Public API ───` 섹션 직전 (라인 112 근처) 에 추가:
  ```python
  # ─── 캘린더 기록 ───────────────────────────────────────────────────────────────

  def _record_calendar_failure(pipeline: str, detail: str, *, partial: bool = False) -> None:
      """실패/부분실패 시 톡캘린더 '자동발행기록' 에 이벤트 등록. 실패해도 조용히 무시."""
      try:
          from common.kakao_calendar import record_failure
          record_failure(pipeline, detail, partial=partial)
      except Exception as e:
          log(f"캘린더 기록 실패 (무시): {e}", "warn")
  ```

- [ ] **Step 3: `notify_pipeline_result` 에 훅 삽입**

  현재 (line 114~135):
  ```python
  def notify_pipeline_result(pipeline: str, published: int, total: int,
                             details: str = "") -> None:
      """파이프라인 실행 결과 알림."""
      now = datetime.now().strftime("%Y-%m-%d %H:%M")
      if published == total and published > 0:
          emoji = "✅"
      elif published > 0:
          emoji = "⚠️"
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
      text += f"🕒 {now}"

      _notify(text)
  ```

  `_notify(text)` 다음 줄에 추가:
  ```python
      _notify(text)

      # 실패/부분실패 시 톡캘린더 기록 (성공은 기록 안 함)
      if total > 0 and published < total:
          partial = published > 0  # published==0 이면 완전실패, 1 이상이면 부분실패
          detail  = f"{published}/{total}건 발행"
          if details:
              detail += f" — {details}"
          _record_calendar_failure(pipeline, detail, partial=partial)
  ```

- [ ] **Step 4: `notify_error` 에 훅 삽입**

  현재 (line 138~151) 의 `_notify(text)` 다음 줄에 추가:
  ```python
      _notify(text)

      # 예외 기반 실패 → 완전실패로 캘린더 기록
      _record_calendar_failure(pipeline, err_msg, partial=False)
  ```

- [ ] **Step 5: 통합 스모크 — 가짜 실패 시나리오 실행**

  일회성 스모크 스크립트 작성하지 않고 Python REPL 로 직접 호출:
  ```bash
  python -c "
  from common.notifier import notify_pipeline_result, notify_error

  # 1) 완전 실패 (0/5)
  notify_pipeline_result('smoke_test', 0, 5, '네트워크 오류 시뮬레이션')

  # 2) 부분 실패 (2/5)
  notify_pipeline_result('smoke_test', 2, 5, '일부 상품 크롤링 실패')

  # 3) 성공 (5/5) — 캘린더 기록 안 되어야 함
  notify_pipeline_result('smoke_test', 5, 5, '정상')

  # 4) 예외 경로
  notify_error('smoke_test', RuntimeError('JWT 토큰 만료'))
  "
  ```

  Expected:
  - 텔레그램 4건 + 카카오톡 4건 메시지 도착 (기존 기능)
  - 톡캘린더 "자동발행기록" 에 3건 신규 이벤트:
    - `[❌ smoke_test] 실패` RED (0/5)
    - `[⚠️ smoke_test] 부분실패` ORANGE (2/5)
    - `[❌ smoke_test] 실패` RED (예외)
  - 성공 케이스(3번)는 **캘린더에 남지 않아야 함**

- [ ] **Step 6: 캘린더 장애 복원력 검증**

  `data/kakao_calendar.json` 을 잠시 삭제하고 KAKAO_ACCESS_TOKEN 을 빈 문자열로 강제:
  ```bash
  mv data/kakao_calendar.json /tmp/
  python -c "
  import os
  os.environ['KAKAO_ACCESS_TOKEN'] = ''
  os.environ['KAKAO_REFRESH_TOKEN'] = ''
  from common.notifier import notify_pipeline_result
  notify_pipeline_result('smoke_test', 0, 1, '토큰 없음 시뮬레이션')
  print('NOTIFIER RETURNED OK — 캘린더 실패가 흐름 차단 안 함')
  "
  mv /tmp/kakao_calendar.json data/
  ```
  Expected: 텔레그램 메시지 정상 도착 + `NOTIFIER RETURNED OK` 출력. 캘린더 경로는 조용히 skip.

- [ ] **Step 7: Commit**

  ```bash
  git add common/notifier.py
  git commit -m "feat(notifier): log partial/full failures to kakao talk calendar"
  ```

---

## Task 5: 실제 파이프라인 1개로 end-to-end 검증

**Files:** 없음 (기존 파이프라인 호출로 검증만)

- [ ] **Step 1: 짧은 파이프라인으로 강제 실패 유도**

  `pipelines/newspick_to_sns.py` 같은 세션 의존 파이프라인은 세션 만료 시 자연 실패한다. 가장 쉬운 경로는 환경변수를 일부러 비워서 실행:
  ```bash
  TELEGRAM_BOT_TOKEN=$TELEGRAM_BOT_TOKEN \
  TELEGRAM_CHAT_ID=$TELEGRAM_CHAT_ID \
  THREADS_ACCESS_TOKEN=INVALID_FORCE_FAIL \
  python -m pipelines.newspick_to_sns 2>&1 | tail -20
  ```

  (실행 전 `.env` 백업 불필요 — 환경변수만 오버라이드 함)

- [ ] **Step 2: 톡캘린더에서 실패 이벤트 확인**

  모바일 카카오톡 → 톡캘린더 → "자동발행기록" 에 `newspick_to_sns` 관련 실패 이벤트가 방금 시각으로 등록됐는지 육안 확인.

  이벤트가 없으면:
  - 콘솔 로그에서 `캘린더 이벤트 등록:` 로그 grep
  - 파이프라인이 실제로 `notify_pipeline_result` 또는 `notify_error` 를 호출했는지 확인 (일부 파이프라인은 내부에서만 로깅하고 notifier 를 안 부를 수 있음 — 그 경우는 Task 6 에서 해결)

- [ ] **Step 3: 캘린더 월간 뷰에서 가독성 확인**

  모바일 톡캘린더 월간 뷰 열어 RED/ORANGE 이벤트 색상이 구분되는지, 5분짜리 이벤트가 월간뷰에서 식별 가능한 수준인지 확인.

  너무 얇아서 안 보이면 이벤트 길이를 15분으로 늘리는 후속 조정을 Task 7 "Followups" 섹션에 기록.

---

## Task 6: README 업데이트

**Files:**
- Modify: `README.md` (기존 카카오 관련 섹션 근처)

- [ ] **Step 1: 현재 README 의 카카오 섹션 위치 파악**

  ```bash
  grep -n -i "kakao\|카카오" README.md | head -20
  ```

- [ ] **Step 2: `talk_calendar` scope 안내 추가**

  카카오 알림 설정 섹션 하단에 다음 블록 추가 (위치는 Step 1 결과에 맞게):
  ```markdown
  #### 톡캘린더 실패 로그 (선택)

  파이프라인이 실패·부분실패로 끝나면 자동으로 톡캘린더 "자동발행기록" 서브캘린더에
  빨강(완전실패)/주황(부분실패) 이벤트를 등록한다. 모바일에서 월간뷰로 실패 이력을
  한눈에 추적할 수 있다.

  **활성화 절차 (1회성):**

  1. https://developers.kakao.com → 앱 → 동의항목 → **캘린더(`talk_calendar`)** ON
  2. `python scripts/kakao_auth.py` 재실행 → 새 scope 포함된 토큰 재발급
  3. 스모크 테스트: `python -m tools.test_kakao_calendar all`
     - 카카오톡 톡캘린더에 "자동발행기록" 캘린더 + 테스트 이벤트 3건 확인

  **동작:**
  - 성공(`published == total`): 캘린더 기록 없음 (텔레그램/카톡 알림만)
  - 부분실패(`0 < published < total`): ORANGE 이벤트
  - 완전실패(`published == 0` 또는 예외): RED 이벤트
  - 토큰/scope 없으면 조용히 skip — 파이프라인 실행에 영향 없음

  **캐시 파일:** `data/kakao_calendar.json` (gitignore 대상, 최초 실행 시 자동 생성)
  ```

- [ ] **Step 3: Commit**

  ```bash
  git add README.md
  git commit -m "docs: add kakao talk_calendar failure log setup guide"
  ```

---

## Task 7: PIPELINE_STATUS 반영 + 최종 확인

**Files:**
- Modify: `PIPELINE_STATUS.md` (알림 시스템 섹션)

- [ ] **Step 1: `PIPELINE_STATUS.md` 의 알림 시스템 표 확장**

  기존 `## 알림 시스템` 표 바로 아래에 행 추가:
  ```markdown
  | 캘린더 기록 ✨신규 | 실패/부분실패 시 톡캘린더 '자동발행기록' 서브캘린더에 이벤트 등록 (성공은 기록 안 함). RED=완전실패 / ORANGE=부분실패. 토큰 없으면 skip |
  | 캘린더 모듈 | `common/kakao_calendar.py` — `record_failure()` 단일 퍼블릭 함수, 401 자동 refresh |
  | 캘린더 scope | `talk_calendar` 필요 — `scripts/kakao_auth.py` 에서 요청, 카카오 콘솔에서 ON 필수 |
  ```

- [ ] **Step 2: 최종 smoke 1회 더 — 실 운영 상태에서 확인**

  ```bash
  python -m tools.test_kakao_calendar all
  ```
  Expected:
  - 기존 캘린더 재사용 (`기존 '자동발행기록' 캘린더 재사용` 로그 또는 단순 캐시 히트)
  - 3건 이벤트 모두 등록
  - 카카오톡에서 총 6건+ 이벤트 (Task 2~5 에서 쌓인 것 포함) 확인

- [ ] **Step 3: 전체 diff 요약 확인**

  ```bash
  git log --oneline origin/main..HEAD
  git diff origin/main --stat
  ```
  Expected 커밋 목록:
  - `feat(kakao-auth): add talk_calendar scope for event logging`
  - `feat(kakao-calendar): add ensure_calendar() and smoke script`
  - `feat(kakao-calendar): implement record_failure() with 401 retry`
  - `feat(notifier): log partial/full failures to kakao talk calendar`
  - `docs: add kakao talk_calendar failure log setup guide`
  - (이 Task의) `docs: update PIPELINE_STATUS with calendar logging`

- [ ] **Step 4: Commit**

  ```bash
  git add PIPELINE_STATUS.md
  git commit -m "docs: update PIPELINE_STATUS with calendar logging"
  ```

---

## Followups (이 PR 범위 밖)

- 파이프라인별 색상 분리 (예: 쿠팡=파랑, 뉴스픽=녹색) — 현재는 심각도 기준 2색
- 이벤트 길이 조정 (월간뷰 가독성 이슈 시 5분 → 15분)
- 연속 실패 집계 (`[❌ x3 coupang_to_wordpress]`)
- 주간 실패 요약을 텔레그램으로 푸시 (캘린더 역방향 조회)
- PlayMCP 도구 등록 — Claude에서 "이번 주 발행 실패 보여줘" 자연어 조회
