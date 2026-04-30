# 톡캘린더 발행 실패 기록 — 설계 문서

- 작성일: 2026-04-22
- 상태: Draft → User Review

## 1. 배경

`common/notifier.py`가 파이프라인 실행 결과를 텔레그램·카카오톡으로 알린다. 알림은 순간적이라 스크롤이 내려가면 과거 실패 이력이 눈에 들어오지 않고, 몇 주 전 어느 파이프라인이 연쇄 실패했는지 확인하기 어렵다.

톡캘린더는 모바일에서 월간 뷰를 바로 볼 수 있고, 카카오 OAuth 토큰(`KAKAO_ACCESS_TOKEN`)이 이미 `common/kakao_token.py`로 관리되므로 추가 인증 설계 없이 붙일 수 있다.

## 2. 목표

파이프라인이 **실패(`failed`) 또는 부분 실패(`partial`)**로 끝나면 톡캘린더 전용 서브캘린더 `자동발행기록`에 5분짜리 빨간 이벤트를 자동 등록한다. 성공은 기록하지 않는다.

### 비목표

- 성공 이벤트 기록 (기존 알림으로 충분)
- 양방향 스케줄 편집 (캘린더 → 파이프라인 스케줄 변경)
- 이벤트 수정·삭제 (append-only)
- PlayMCP 도구 등록 (개인 자동화 목적과 맞지 않음)
- 주간/월간 리포트 생성 (캘린더 자체가 리포트)

## 3. 성공 기준

1. 파이프라인이 실패하면 1분 이내 톡캘린더에 이벤트가 등록된다.
2. 캘린더 기록 실패가 파이프라인 실행 결과나 기존 알림 채널(텔레그램·카톡)에 영향을 주지 않는다.
3. `KAKAO_ACCESS_TOKEN`이 만료돼도 refresh_token으로 자동 갱신 후 재시도한다.
4. 최초 1회 서브캘린더를 생성하고 이후 실행은 캐시된 `calendar_id`를 재사용한다.
5. 모든 기존 파이프라인이 코드 수정 없이 자동 적용된다.

## 4. 아키텍처

```
pipelines/*.py  (수정 없음)
        │
        └─ notify_pipeline_result(...) / notify_error(...)
                  │
                  ▼
common/notifier.py  (수정)
  기존: 텔레그램 + 카카오톡 발송
  추가: 실패/부분실패/예외 → calendar.record_failure() 호출
                  │
                  ▼
common/kakao_calendar.py  (신규)
  ├─ ensure_calendar("자동발행기록") → calendar_id
  ├─ record_failure(pipeline, detail, started_at) → event_id
  └─ 401 시 refresh_access_token() 후 1회 재시도
                  │
                  ▼
Kakao Talk Calendar REST API
  POST /v2/api/calendar/create/sub
  POST /v2/api/calendar/create/event

data/kakao_calendar.json  (신규, gitignore)
  { "calendar_id": "sub_xxx", "created_at": "..." }
```

## 5. 컴포넌트

### 5.1 `common/kakao_calendar.py` (신규)

**책임**: 톡캘린더 서브캘린더 관리와 실패 이벤트 등록. 단일 퍼블릭 함수 `record_failure()`만 외부에서 호출된다.

**공개 함수**

```
record_failure(pipeline: str, detail: str, started_at: datetime | None = None) -> bool
```

- `pipeline`: 파이프라인 이름 (예: `coupang_to_wordpress`). 제목에 그대로 사용.
- `detail`: 실패 요약. 에러 메시지 or `"3/5 실패"` 같은 부분실패 표현. 500자로 truncate.
- `started_at`: 실패 시작 시각. None이면 `datetime.now()`.
- 반환: 성공 `True`, 실패(토큰 없음/API 오류/네트워크 등) `False`. **예외는 밖으로 전파하지 않는다.**

**내부 동작**

1. `_get_calendar_id()`: `data/kakao_calendar.json` 조회 → 없으면 `ensure_calendar()` 호출 → 캐시 저장
2. `_build_event()`: 제목·시간·설명·색상을 담은 이벤트 payload 생성
3. `_post_event(token, payload)`: API 호출. 401이면 `refresh_access_token()` 1회 호출 후 재시도
4. 모든 단계의 예외는 `log()` 로 경고만 남기고 `False` 반환

**이벤트 스키마** (Kakao Talk Calendar 이벤트 생성 API 기준)

| 필드 | 값 |
|---|---|
| `title` | 부분실패 `[⚠️ {pipeline}] 2/5건` · 완전실패 `[❌ {pipeline}] 실패` |
| `time.start_at` | `started_at` (ISO8601, KST) — **5분 격자로 floor 필수** (API 제약: `code=-2 "The minimum unit of start_at is 5 minutes."`) |
| `time.end_at` | `start_at + 5분` |
| `time.time_zone` | `Asia/Seoul` |
| `color` | 부분실패 `ORANGE` · 완전실패 `RED` (월간뷰에서 심각도 구분) |
| `description` | `detail` (500자 이내) |
| `reminders` | `[]` (빈 배열 — 알림 불필요) |

### 5.2 `common/notifier.py` (수정)

**변경 범위**: 기존 함수 2개에 캘린더 훅 추가. 시그니처 불변.

```
notify_pipeline_result(pipeline, published, total, details=""):
    # 기존 텔레그램/카톡 발송 로직 그대로

    # 신규: 실패/부분실패 시 캘린더 기록
    if total > 0 and published < total:
        _record_calendar_failure(pipeline, f"{published}/{total}건 발행", ...)

notify_error(pipeline, error):
    # 기존 로직 그대로
    # 신규: 캘린더 기록
    _record_calendar_failure(pipeline, err_msg, ...)
```

**`_record_calendar_failure()` 내부 헬퍼**

```
def _record_calendar_failure(pipeline: str, detail: str) -> None:
    try:
        from common.kakao_calendar import record_failure
        record_failure(pipeline, detail)
    except Exception as e:
        log(f"캘린더 기록 실패 (무시): {e}", "warn")
```

`_send_telegram` / `_send_kakao` 와 동일한 실패 격리 패턴을 따른다.

### 5.3 `data/kakao_calendar.json` (신규)

gitignore 대상. 구조:

```json
{
  "calendar_id": "sub_xxxxxxxxxxxxx",
  "name": "자동발행기록",
  "created_at": "2026-04-22T14:00:00+09:00"
}
```

파일 부재 시 `ensure_calendar()`가 API로 서브캘린더를 만들고 파일에 기록한다.

### 5.4 Kakao 앱 설정 변경 (1회성 수동 작업)

현재 앱은 `talk_message` scope만 받은 상태다. 캘린더 API를 쓰려면:

1. 카카오 개발자 콘솔에서 앱 → 동의항목 → **`talk_calendar` 활성화**
2. `scripts/kakao_auth.py` 재실행해 새 scope 포함 토큰 재발급
3. `.env`의 `KAKAO_ACCESS_TOKEN` / `KAKAO_REFRESH_TOKEN` 갱신 확인

이 단계는 README에 명시하고, 토큰이 없으면 `record_failure()`는 조용히 `False` 반환.

## 6. 데이터 플로우

### 정상 케이스 (파이프라인 부분 실패)

```
쿠팡 파이프라인 run()
  └─ 2/5 발행 성공
  └─ notify_pipeline_result("coupang_to_wordpress", 2, 5, "...")
       ├─ _send_telegram() ✅
       ├─ _send_kakao()    ✅
       └─ _record_calendar_failure("coupang_to_wordpress", "2/5건 발행")
            └─ record_failure()
                 ├─ _get_calendar_id() → 캐시 hit
                 ├─ _post_event(token, payload) → 201 Created
                 └─ return True
```

### 토큰 만료 케이스

```
record_failure()
  └─ _post_event(token) → 401
       └─ refresh_access_token() → 새 token
       └─ _post_event(new_token) → 201
```

### 토큰/권한 없음 케이스

```
record_failure()
  └─ get_access_token() → "" (scope 부족)
       └─ log("캘린더 토큰 없음 — 건너뜀", "warn")
       └─ return False
```

## 7. 에러 처리 원칙

| 시나리오 | 처리 |
|---|---|
| 네트워크 오류 | log 경고, `False` 반환 |
| 401 | refresh 후 1회 재시도, 실패 시 `False` |
| 403 (scope 부족) | log 경고 후 `False`, 재시도 안 함 |
| 404 (calendar_id 만료) | 캐시 삭제 후 `ensure_calendar()` 재호출, 1회 재시도 |
| 5xx | log 경고, `False` 반환 (retry 없음 — 다음 실패 때 다시 시도) |
| 예상 못 한 예외 | `try/except Exception` 으로 모두 삼킴 |

**철칙**: 캘린더 기록 실패가 notifier 체인을 끊지 않는다.

## 8. 테스트 전략

### 8.1 유닛 (없음)

`requests.post`를 목킹하는 유닛테스트 대신, 실제 API를 치는 수동 스모크 테스트를 택한다. 프로젝트 전반이 라이브 API 의존이라 일관성 유지.

### 8.2 스모크 스크립트 `tools/test_kakao_calendar.py` (신규)

```
python -m tools.test_kakao_calendar
```

실행 내용:
1. `ensure_calendar()` → 카카오톡 앱에서 "자동발행기록" 서브캘린더 생성 여부 육안 확인
2. `record_failure("test_pipeline", "스모크 테스트")` 3회 호출
   - 1번: 정상
   - 2번: 일부러 잘못된 토큰 강제 주입 → 401 → refresh 경로 검증
   - 3번: `detail` 600자로 truncate 동작 확인
3. 카카오톡 앱에서 캘린더 열어 3건 이벤트 확인

### 8.3 통합 검증

실제 파이프라인 1개(`coupang_to_wordpress`)의 발행 카운트를 강제로 `0/5`로 만들어 실패 알림 발생시킨 뒤 캘린더 등록 확인.

## 9. 롤아웃

1. **Phase 1**: `common/kakao_calendar.py` + 스모크 스크립트 작성, scope 갱신, 스모크 통과
2. **Phase 2**: `common/notifier.py`에 `_record_calendar_failure()` 훅 추가
3. **Phase 3**: 1~2주 운영 관찰, 캘린더 노이즈/누락 여부 점검
4. **Phase 4**: README에 scope 재발급 절차와 캘린더 캐시 파일 설명 반영

## 10. 범위 밖 (확장 후보)

- 파이프라인별 색상 분리 (쿠팡=파랑, 뉴스픽=녹색 등) — 일단 RED 단색으로 충분
- 실패 연속 횟수 집계 후 캘린더 제목에 표시 (`[❌ x3]`)
- 캘린더 → 텔레그램 주간 리포트 역방향 요약
- PlayMCP 도구 등록 (Claude에서 "이번 주 발행 실패 보여줘" 조회용)

## 11. 관련 파일

- `common/notifier.py` — 수정
- `common/kakao_calendar.py` — 신규
- `common/kakao_token.py` — 수정 없음 (기존 refresh 로직 재사용)
- `data/kakao_calendar.json` — 신규, gitignore
- `tools/test_kakao_calendar.py` — 신규
- `scripts/kakao_auth.py` — 수정 없음 (scope 갱신은 카카오 콘솔 설정만 바꾸면 됨)
- `README.md` — 설치 가이드 섹션에 `talk_calendar` scope 추가 안내
