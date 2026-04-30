# Auto Publishing — Agent Guide

범용 AI 에이전트(OpenAI Codex, Hermes, 그 외 agents SDK 기반 도구)를 위한
프로젝트 가이드라인입니다.

---

## 프로젝트 개요

다중 플랫폼 자동 콘텐츠 발행 시스템.

```
sources/ ──→ pipelines/ ──→ publishers/
  ↑                              ↓
(콘텐츠 수집)            (티스토리·네이버·WordPress·SNS 등 발행)
              common/
         (logger·session·notifier·scheduler)
```

- **sources/** (20개): 쿠팡/알리 파트너스, 뉴스픽, 공공데이터, AI 생성 등 수집 모듈
- **pipelines/** (14개): 소스→퍼블리셔 조합. `SCHEDULE` 메타 선언으로 자동 스케줄링
- **publishers/** (11개): Tistory, WordPress, Naver Blog/Cafe, Twitter, Threads, Instagram, Pinterest, GitHub Pages
- **common/**: 공통 유틸 — `logger`, `session`, `notifier`, `scheduler`, `auth`, `ai_intro`

---

## graphify 지식 그래프

이 프로젝트는 지식 그래프로 인덱싱되어 있습니다.

| 항목 | 경로 |
|------|------|
| 그래프 데이터 | `graphify-out/graph.json` |
| 리포트 | `graphify-out/GRAPH_REPORT.md` |
| HTML 시각화 | `graphify-out/graph.html` |

**질의 방법:**

```bash
graphify query "<질문>"
graphify path "SessionManager" "notify_pipeline_result"
graphify explain "PostResult"
```

**코드 변경 후 그래프 갱신 (LLM 없음, 빠름):**

```bash
graphify update .
```

**전체 재빌드 (LLM 시맨틱 추출 포함, 느림):**

```bash
# Cursor / Claude Code 에서: /graphify .
graphify .
```

---

## 핵심 패턴

### 새 파이프라인 추가

```python
# pipelines/my_pipeline.py
from pipelines._kernel.base_runner import run_pipeline

SCHEDULE = {
    "env":  "SCHEDULE_MY_PIPELINE",   # .env에 시간 설정
    "func": "run",
}

def run() -> None:
    run_pipeline(
        pipeline_name="내 파이프라인",
        fetch_fn=lambda: MySource().fetch(count=3),
        publish_fn=lambda item: MyPublisher().post(
            title=item["title"],
            content=item["content"],
        ),
        count=3,
    )
```

### 발행 결과 처리

```python
from publishers.base import PostResult

result: PostResult = publisher.post(...)
if result.success:
    print(result.url)
else:
    print(result.message)   # result.error 는 없음 → message 사용
```

### 세션 관리

```python
from common.session import SessionManager

sm = SessionManager("naver_blog_myid")
sm.load()     # .sessions/naver_blog_myid.pkl 복원
sm.save()     # 저장
sm.get(url)   # HTTP GET (세션 쿠키 자동 포함)
```

---

## 환경 변수

`.env.example` 참조. 주요 변수:

- `GEMINI_API_KEY` — AI 콘텐츠 생성
- `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` — 발행 알림
- `KAKAO_ACCESS_TOKEN` — 카카오톡 알림
- `SCHEDULE_*` — 파이프라인 실행 시각 (HH:MM, 콤마 구분)

---

## 알림 채널

파이프라인 완료/실패 알림은 `common/notifier.py` 가 자동 발송.
**텔레그램은 콘텐츠 발행 플랫폼이 아닌 알림 전용** — 본인이 BotFather 로 생성한 봇 토큰을 `.env` 에 주입.

---

## git hook (자동 그래프 갱신)

`git commit` / `git checkout` 시 `graphify update .` 가 자동 실행됩니다.
커밋마다 `graphify-out/` 의 AST 그래프가 최신 코드를 반영합니다.
