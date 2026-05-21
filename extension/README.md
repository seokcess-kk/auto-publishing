# Auto Publishing — Tistory Bridge Extension

Tistory 가 2026-05-16+ 부터 `/manage/post.json` 에 DKAPTCHA (Daum/Kakao 자체
한국어 비주얼 캡차) 를 도입해 Playwright/Selenium 등 자동화 도구의 발행을
완전 차단했습니다. CDP 가 연결된 모든 Chrome 에서도 캡차 위젯이 빈 화면으로
serve 됩니다.

이 확장은 우회용 — **사용자의 평소 Chrome** (자동화 도구 미연결) 에서 실행되어
Daum 캡차가 정상 렌더되는 컨텍스트를 활용합니다. 캡차 풀이만 사용자가 1회씩
직접 하고, 그 외 모든 단계 (글 생성 / editor 폼 작성 / publish 클릭) 는 자동화.

## 아키텍처

```
Python 파이프라인 (coupang/aliexpress/newspick → tistory)
        │
        │ enqueue
        ▼
data/tistory_queue.json    ← 발행 대기열
        │
        │ GET /next
        ▼
Bridge HTTP server (localhost:5757)
        │
        │ poll every 10s
        ▼
Chrome Extension (background.js)
        │
        │ open <blog>.tistory.com/manage/newpost
        ▼
content.js  ── editor 자동 작성 + '공개 발행' 클릭
        │
        ▼
[ 사용자 DKAPTCHA 풀이 ]
        │
        ▼
발행 완료 → POST /done → publish_queue.json 갱신
```

## 설치 (1회)

1. `chrome://extensions/` 진입
2. 우상단 **개발자 모드** ON
3. **압축해제된 확장 프로그램 로드** 클릭
4. 이 폴더 (`extension/`) 선택
5. 설치 확인 — 도구모음에 퍼즐 아이콘 → "Auto Publishing — Tistory Bridge" 핀 고정 권장

## 운영 절차

### A. 파이프라인을 브릿지 모드로 전환

`.env` 에 설정:

```
TISTORY_PUBLISHER=bridge
```

다음 슬롯부터 `coupang_to_tistory` / `aliexpress_to_tistory` / `newspick_to_tistory`
가 web publisher 대신 `publishers/tistory_bridge.py` 를 써 큐에 적재.

### B. Bridge server 실행 (자동)

**기본 운영 — 스케줄러 내장**:
`TISTORY_PUBLISHER=bridge` 면 `pipelines.scheduler_runner` 가 시작할 때 bridge HTTP
서버를 daemon thread 로 자동 임베드. **별도 터미널 불필요**. 스케줄러를 재시작하면
bridge 도 함께 뜬다.

스케줄러 재시작 (Task Scheduler 등록된 경우):
```powershell
Stop-ScheduledTask -TaskName AutoPublishing_Scheduler
Start-ScheduledTask -TaskName AutoPublishing_Scheduler
```

**디버깅 / 개발용 — 독립 실행**:
스케줄러 없이 bridge 만 띄우려면:
```
python -m pipelines.tistory_bridge
```
스케줄러가 임베드 bridge 를 띄우려고 시도할 때 포트 충돌을 감지하면 silent skip
하므로, 독립 bridge 와 스케줄러를 동시에 운영해도 안전.

### C. 발행 흐름 (자동)

1. 스케줄러가 파이프라인 트리거 → 글 생성 후 큐에 enqueue
2. 확장이 10초 주기로 `/next` 폴링 → 새 글 감지
3. 확장이 새 탭으로 `<blog>.tistory.com/manage/newpost?_apid=<id>` 진입
4. content.js 가 제목/본문/태그 자동 입력 → '완료' → '공개' → '공개 발행' 클릭
5. **DKAPTCHA 위젯 등장 → 사용자가 풀이 후 '답변 제출'**
6. URL 이 발행된 글로 이동하면 확장이 자동 감지 → `/done` 보고
7. bridge 가 `publish_queue.json` 갱신 → 색인/백링크 파이프라인 input

## Popup UI

- **활성 토글** — 확장 polling on/off
- **bridge 상태** — `localhost:5757/healthz` 응답 확인
- **활성 작업** — 현재 처리 중인 글
- **큐 통계** — pending / claimed / done / failed 카운트

## 트러블슈팅

| 증상 | 원인 / 해결 |
|---|---|
| popup 에 "bridge 미응답" | bridge server 미실행 → `python -m pipelines.tistory_bridge` 실행 |
| 새 탭 열리는데 폼 자동 작성 안 됨 | content script 미주입 — `chrome://extensions/` 에서 확장 reload |
| 캡차 위젯이 빈 화면 | 평소 Chrome 인지 확인 — `--remote-debugging-port` 같은 플래그로 띄운 Chrome 이면 캡차 차단됨 |
| 5분 안에 캡차 못 풀면 fail 처리 | content.js 의 `WAIT_MAX` 조정 가능. fail 항목은 큐에 남아 사용자가 수동 재시도 가능 |
| 한꺼번에 여러 글 처리하고 싶음 | 현재는 sequential (한 글 처리 끝나면 다음). 동시 실행은 캡차 풀이 race 위험 |

## 보안 메모

- bridge server 는 `127.0.0.1` 만 listen (외부 접근 차단)
- 확장 host_permissions 도 `localhost:5757` 와 `*.tistory.com` 만
- 자격증명 / API 키 노출 없음 — 큐 항목은 단순 글 데이터 (title/content/tags)

## 비활성화

`.env` 의 `TISTORY_PUBLISHER` 를 `web` 으로 되돌리면 즉시 기존 (실패하는) 경로
복귀. 확장 자체는 `chrome://extensions/` 에서 토글 OFF.
