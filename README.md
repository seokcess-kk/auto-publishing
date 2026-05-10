# Auto Publishing

> **콘텐츠 소스 → 자동 발행 파이프라인 프레임워크**
> 키워드 수집부터 콘텐츠 생성·발행·SNS 백링크·실시간 알림까지 한 호스트에서
> 끝나는 멀티 플랫폼 자동화 시스템.

Python 3.9+ · Playwright · WordPress / Tistory / Naver / GitHub Pages / Twitter / Threads / Pinterest / Instagram

---

## 목차

1. [무엇을 하는 프로젝트인가](#무엇을-하는-프로젝트인가)
2. [핵심 기능](#핵심-기능)
3. [전체 흐름](#전체-흐름)
4. [빠른 시작](#빠른-시작-5분)
5. [디렉토리 구조](#디렉토리-구조)
6. [키워드 수집 시스템](#키워드-수집-시스템)
7. [콘텐츠 소스](#콘텐츠-소스)
8. [발행 플랫폼](#발행-플랫폼)
9. [파이프라인 카탈로그](#파이프라인-카탈로그)
10. [스케줄러](#스케줄러)
    - [Windows 운영](#windows-운영-작업-스케줄러)
11. [알림 시스템](#알림-시스템-텔레그램--카카오톡-병행)
12. [환경변수](#환경변수-env)
13. [발행 콘텐츠 예시](#발행-콘텐츠-예시)
14. [기술 스택](#기술-스택)
15. [트러블슈팅 · FAQ](#트러블슈팅--faq)
16. [면책 및 법적 고지](#면책-및-법적-고지)
17. [기여하기](#기여하기)
18. [라이선스](#라이선스)
19. [감사의 말](#감사의-말)

---

## 무엇을 하는 프로젝트인가

여러 **콘텐츠 소스**(상품·뉴스·공공데이터)에서 자료를 모아 **여러 플랫폼**
(블로그·카페·SNS)에 자동 발행하는 파이프라인 모음입니다. 각 파이프라인은
독립 모듈로 등록되며, 스케줄러가 `.env` 시간표를 보고 알아서 실행합니다.

**대상 사용자**

- 동일 콘텐츠를 여러 채널에 반복 발행해야 하는 1인 크리에이터·블로거
- 자체 사이트 백링크를 SNS 로 자동 푸시하고 싶은 운영자
- 키워드 풀 기반 어필리에이트 마케팅을 자동화하고 싶은 개인

**대상이 아닌 경우**

- 클릭 한 번으로 끝나는 SaaS 가 필요한 경우 → 본 프로젝트는 자체 호스팅 +
  본인 계정·API 키 등록이 필요합니다.
- 스팸·대량 자동 게시가 목표인 경우 → [면책 및 법적 고지](#면책-및-법적-고지)
  를 먼저 읽으세요. 각 플랫폼 ToS 준수는 본인 책임입니다.

---

## 핵심 기능

- **키워드 풀 5,000+ 자동 수집** — ItemScout · Pandarank · 네이버 DataLab
  3중 소스, **API 키 불필요**. 발행 완료 키워드는 영구 기록되어 중복 발행
  자동 차단.
- **상품 크롤링** — 쿠팡 (Chrome CDP 모바일 에뮬레이션), 알리익스프레스
  (Playwright + storage_state). 어필리에이트 링크는 AF 코드 기반으로
  자체 생성.
- **콘텐츠 자동 생성** — Claude CLI (Max 플랜) → Gemini API 폴백으로 도입부·
  요약 자동 작성.
- **멀티 플랫폼 발행** — WordPress (REST API + JWT) · Tistory · 네이버 블로그
  (RSA 로그인) · 네이버 카페 · GitHub Pages (Jekyll) · Twitter · Threads
  (Graph API) · Pinterest · Instagram.
- **레지스트리 기반 스케줄러** — 파이프라인 모듈 상단의 `SCHEDULE` dict 만
  선언하면 `scheduler_runner` 가 자동 발견. 새 파이프라인 추가 시 스케줄러
  코드 수정 불필요.
- **2채널 실시간 알림** — 파이프라인 성공·부분실패·예외를 텔레그램과 카카오톡
  나와의 채팅으로 동시 발송. OAuth 토큰 자동 갱신 내장.
- **공공데이터 통합** — 일출일몰 (한국천문연구원), 부동산 실거래가, 청약홈
  분양정보 등 무료 공공 API 활용.

---

## 전체 흐름

```
[키워드 수집]                     [콘텐츠 소스]                    [발행 플랫폼]
ItemScout (12 카테고리)         ┬→ 쿠팡 상품 (CDP + Playwright)  ─→ WordPress · GitHub Pages
Pandarank (194 카테고리)        ├→ 알리익스프레스 (Playwright)    ─→ Pinterest · 네이버 카페
DataLab  (연령/성별/기기 차원)  ├→ 뉴스픽 아티클                  ─→ Twitter · Threads · Instagram
(5,000+개 키워드 풀)            ├→ 정책브리핑 RSS                 ─→ 티스토리 (역할별 다중 블로그)
                               ├→ 일출/일몰 공공데이터           ─→ 네이버 블로그/카페
                               └→ 부동산 공공데이터              ─→ 자체 URL → SNS 백링크

      Claude CLI / Gemini AI (도입부 생성)    텔레그램 + 카카오톡 (병행 알림)
```

---

## 빠른 시작 (5분)

### 1. 사전 요구사항
- Python 3.9 이상
- Google Chrome (쿠팡 모바일 크롤링용)
- 발행하려는 플랫폼의 **본인 계정** (예: WordPress 관리자, 티스토리, 네이버)

### 2. 설치

```bash
git clone <this-repo-url>
cd auto-publishing

python3 -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate

pip install -r requirements.txt
playwright install chromium
```

### 3. 환경변수 설정

```bash
cp .env.example .env
# 에디터로 .env 열고 필요한 값만 채움 — 사용하지 않는 플랫폼은 빈칸이어도 됨
```

> **공개 저장소 사용자 주의** — 동봉된 `.env.example` 의 일부 값(블로그 ID,
> 채널 ID, 백링크 URL, 봇 핸들 등)은 원저자의 운영 환경 디폴트입니다.
> 반드시 **본인 환경 값으로 교체**하세요. 자세한 항목은 [환경변수](#환경변수-env)
> 참조.

### 4. 단일 파이프라인 실행 (검증)

가장 의존성이 적은 일출일몰 파이프라인으로 동작 확인을 권장합니다:

```bash
python3 -m pipelines.riseset_to_tistory
```

성공 시 텔레그램·카카오톡 (설정한 경우)으로 발행 완료 알림이 옵니다.

### 5. 스케줄러 가동

```bash
nohup python3 -m pipelines.scheduler_runner > scheduler.log 2>&1 &
tail -f scheduler.log
```

`.env` 의 `SCHEDULE_*` 시간표대로 등록된 파이프라인이 매일 자동 실행됩니다.

---

## 디렉토리 구조

```
auto-publishing/
├── .env                         # 환경변수 (API 키, 계정 — git 제외)
├── .env.example                 # 환경변수 템플릿
├── requirements.txt             # Python 의존성
├── config.json                  # WordPress 멀티 프로필 (선택)
│
├── data/                        # 운영 데이터 (git 제외)
│   ├── keyword_pool.json        # ItemScout 수집 키워드 풀
│   └── used_keywords.json       # 발행 완료 키워드 (영구 기록)
│
├── sources/                     # 콘텐츠 소스 모듈
│   ├── itemscout_keywords.py    # ItemScout 키워드 풀 (12 카테고리)
│   ├── pandarank_keywords.py    # 판다랭크 (대 10 + 중 184 카테고리)
│   ├── datalab_keywords.py      # 네이버 DataLab (연령·성별·기기 차원)
│   ├── coupang.py               # 쿠팡 상품 + 파트너스 링크 (모바일 CDP)
│   ├── aliexpress.py            # 알리 상품 + 제휴 링크 (Playwright)
│   ├── newspick.py              # 뉴스픽 기사 크롤링
│   ├── realestate.py            # 공공데이터 부동산 실거래
│   ├── riseset.py               # 한국천문연구원 일출/일몰
│   ├── cheongyak.py             # 청약홈 분양정보
│   ├── korea_policy.py          # 정책브리핑 RSS (26부처+청)
│   ├── policy_news.py           # 문화체육관광부 정책브리핑 API
│   ├── kakaomap.py              # 카카오맵 로컬 검색
│   ├── entertainment.py         # 멜론·구글 트렌드·알라딘 베스트셀러
│   ├── finance.py               # 환율·가상화폐·KOSPI/KOSDAQ
│   ├── hotdeal.py               # 뽐뿌·클리앙·루리웹 핫딜 통합
│   ├── knowledge.py             # 명언·영단어·GeekNews·GitHub 트렌딩
│   ├── weather.py               # 생활날씨
│   ├── rss_feed.py              # 범용 RSS 피드 파서
│   ├── sitemap_crawler.py       # WordPress/Tistory 사이트맵 (백링크)
│   └── gemini_generator.py      # Gemini 글 생성
│
├── publishers/                  # 발행 플랫폼 모듈
│   ├── base.py                  # 추상 Publisher 클래스
│   ├── wordpress.py             # WordPress REST API
│   ├── github_pages.py          # GitHub Pages (Jekyll Markdown push)
│   ├── tistory.py               # 티스토리
│   ├── naver_blog.py            # 네이버 블로그 (RSA 로그인 + SE 에디터)
│   ├── naver_cafe.py            # 네이버 카페
│   ├── twitter.py               # 트위터/X (쿠키 기반)
│   ├── threads.py               # Meta Threads (공식 Graph API)
│   ├── pinterest.py             # Pinterest API
│   ├── pinterest_playwright.py  # Pinterest Playwright (Google 로그인)
│   └── instagram.py             # Instagram (Playwright)
│
├── pipelines/                   # 소스 → 발행 파이프라인
│   ├── _kernel/                 # 공통 run() 골격
│   │   ├── product_wp.py        # 상품→WP 공통 (쿠팡/알리 공유)
│   │   └── newspick.py          # 뉴스픽→Publisher 공통
│   ├── _riseset_common.py       # 일출/일몰 공통 빌더 (naver/tistory 공유)
│   ├── coupang_to_wordpress.py  # 쿠팡 → WordPress (멀티 프로필)
│   ├── coupang_to_github.py     # 쿠팡 → GitHub Pages
│   ├── coupang_to_pinterest.py  # 쿠팡 → Pinterest
│   ├── aliexpress_to_wordpress.py  # 알리 → WordPress
│   ├── aliexpress_to_tistory.py    # 알리 → 티스토리
│   ├── newspick_to_tistory.py   # 뉴스픽 → 티스토리
│   ├── newspick_to_wordpress.py # 뉴스픽 → WordPress
│   ├── newspick_to_naver.py     # 뉴스픽 → 네이버 블로그/카페
│   ├── newspick_to_sns.py       # 뉴스픽 → 트위터 + 스레드
│   ├── riseset_to_naver.py      # 일출/일몰 → 네이버 블로그
│   ├── riseset_to_tistory.py    # 일출/일몰 → 티스토리
│   ├── policy_to_tistory.py     # 정책브리핑 RSS → 티스토리
│   ├── backlink_to_sns.py       # 자체 블로그 URL → Twitter/Threads
│   ├── realestate_to_blog.py    # 부동산 → 티스토리/네이버
│   └── scheduler_runner.py      # registry 기반 스케줄 실행
│
├── common/                      # 공통 유틸리티
│   ├── logger.py                # 컬러 콘솔 로그
│   ├── auth.py                  # 인증 (Naver RSA, WP, Coupang HMAC)
│   ├── session.py               # 쿠키/세션 저장·복원
│   ├── image.py                 # 이미지 다운로드
│   ├── url_shortener.py         # URL 단축 (is.gd / TinyURL)
│   ├── scheduler.py             # schedule 라이브러리 래퍼
│   ├── notifier.py              # 텔레그램 + 카카오톡 병행 알림
│   ├── kakao_token.py           # 카카오 OAuth access_token 자동 갱신
│   ├── kakao_calendar.py        # 톡캘린더 실패 로그 (선택)
│   ├── threads_token.py         # Meta Threads 장기 토큰 갱신
│   ├── ai_intro.py              # AI 도입부 생성 (Claude CLI → Gemini)
│   ├── product_html.py          # 상품 카드 HTML 템플릿 (ProductTheme)
│   ├── product_card.py          # '오늘의 추천 상품' 단일 카드 렌더
│   ├── wp_profiles.py           # WordPress 프로필 로더
│   ├── tistory_blogs.py         # 티스토리 역할별 블로그 라우팅
│   ├── backlink_state.py        # 백링크 URL 발행 이력
│   ├── tag_generator.py         # 실시간 트렌드 해시태그
│   ├── forbidden_keywords.py    # 금지 키워드 필터
│   └── aliexpress_login.py      # 알리 Playwright 로그인 헬퍼
│
└── scripts/
    └── kakao_auth.py            # 카카오 OAuth 초기 토큰 발급 (1회)
```

### 공통 모듈 핵심 요약

| 모듈 | 역할 |
|------|------|
| `pipelines/_kernel/product_wp.py` | 쿠팡/알리 WP 파이프라인 공통 `run()` 골격. `ProductWpConfig` 에 소스 팩토리·테마·env 키만 정의하면 동일 실행 루프 재사용. |
| `pipelines/_kernel/newspick.py` | 뉴스픽 → 단일 Publisher 골격. `NewspickConfig` 에 Publisher 팩토리만 주입. |
| `pipelines/_riseset_common.py` | 일출/일몰 본문 빌더 (지역 표·시각 카드·상품 카드). naver/tistory 공유. |
| `common/product_html.py` | `ProductTheme` + `render_product_post()` 로 쿠팡·알리 카드 HTML 단일 템플릿 렌더. |
| `common/ai_intro.py` | Claude CLI → Gemini 자동 폴백. `CLAUDE_CLI_PATH` 로 경로 외부화. |
| `common/wp_profiles.py` | `config.json` 의 `wordpress_profiles` 로더. 여러 WP 사이트에 동일 파이프라인 반복 발행. |
| `common/tistory_blogs.py` | 티스토리 다중 블로그를 역할별로 라우팅 (`realestate`/`riseset`/`newspick`/`policy`/`backlink`/`aliexpress`). |
| `common/notifier.py` | 텔레그램 + 카카오톡 **병행 발송**. 한쪽만 설정해도 동작. 401 시 카카오 토큰 자동 갱신. |
| `common/kakao_token.py` | 카카오 access_token 을 refresh_token 으로 자동 갱신하고 `.env` 업데이트. |

---

## 키워드 수집 시스템

### ItemScout (1순위)

ItemScout 내부 API로 12개 카테고리에서 월간 검색량 기준 키워드를 수집합니다.
**API 키 불필요**.

| 카테고리 | cid | 카테고리 | cid |
|---------|-----|---------|-----|
| 패션의류 | 1 | 식품 | 7 |
| 패션잡화 | 2 | 스포츠/레저 | 8 |
| 화장품/미용 | 3 | 생활/건강 | 9 |
| 디지털/가전 | 4 | 여가/생활편의 | 10 |
| 가구/인테리어 | 5 | 면세점 | 11 |
| 출산/육아 | 6 | 도서 | 45830 |

- 카테고리당 최대 500개, 전체 최대 6,000개 수집
- 중복 제거 후 약 5,500개 키워드 확보
- 월간 검색수 기준 내림차순 정렬
- `data/keyword_pool.json` 에 저장

```bash
# 키워드 풀 수동 수집 (약 15초)
python3 -c "from sources.itemscout_keywords import collect_all_keywords; collect_all_keywords()"
```

### 네이버 DataLab (확장)

ItemScout 실패 시 네이버 DataLab 쇼핑인사이트를 크롤링합니다. **API 키 불필요**.

10개 쇼핑 카테고리 × 연령 6단계(10s~60s) × 성별(f/m) × 기기(pc/mo) ×
시간단위(date/week/month). 차원별 결과 차이 반영 (예: "주짓수도복"은 20대 전용,
"고무나라"는 모바일 전용).

### Pandarank (트렌드)

판다랭크 내부 API 로 대분류 10 + 중분류 184 = 총 194개 카테고리에서 실시간
bestKeyword 를 수집. 실시간 상승/하락 (`rank_change: up/down/keep`) 정보 제공.

### 다중 소스 통합

```python
from sources.itemscout_keywords import collect_all_keywords_multi

collect_all_keywords_multi(sources=["itemscout", "pandarank", "datalab"])
```

- 우선순위: ItemScout (정량) → Pandarank (트렌드) → DataLab (세그먼트)
- 중복 시 기존 record 유지 + 부가 필드(`rank_change`, `dim`) 병합

### 키워드 중복 방지

| 파일 | 역할 |
|------|------|
| `data/keyword_pool.json` | 수집된 전체 키워드 (5,000+) |
| `data/used_keywords.json` | 발행 완료 키워드 (영구 보관) |

- 발행 성공한 키워드는 `used_keywords.json` 에 영구 기록 → 두 번 발행 금지
- 풀 잔여가 50개 이하면 자동 재수집
- 하루 3건 발행 기준 약 5년 분량

```bash
python3 -c "from sources.itemscout_keywords import get_pool_status; print(get_pool_status())"
```

---

## 콘텐츠 소스

| 모듈 | 출처 | 인증 | 비고 |
|------|------|------|------|
| `coupang.py` | 쿠팡 모바일 검색 | 없음 (CDP 크롤링) | 파트너스 AF 코드로 링크 자체 생성 |
| `aliexpress.py` | AliExpress 검색 | 본인 계정 (Playwright) | 첫 로그인 수동 (캡차) → storage_state |
| `newspick.py` | 뉴스픽 RSS/HTML | 없음 | 카테고리: 추천/유머/사연/웹툰/뷰티 등 |
| `realestate.py` | 공공데이터 실거래가 | `DATA_GO_KR_KEY` | 지역·아파트별 실거래 |
| `riseset.py` | 한국천문연구원 | `DATA_GO_KR_KEY` | 일출·일몰·월출·월몰·박명 |
| `cheongyak.py` | 청약홈 | 없음 | 분양 단지 정보 |
| `korea_policy.py` | korea.kr RSS | 없음 | 26부처+청 정책뉴스 |
| `kakaomap.py` | 카카오맵 로컬검색 | `KAKAO_REST_API_KEY` | 키워드/카테고리 검색 |
| `entertainment.py` | 멜론·구글 트렌드·알라딘 | 없음 | 차트·베스트셀러 |
| `finance.py` | 환율·코인·KOSPI/KOSDAQ | 없음 | 실시간 시세 |
| `hotdeal.py` | 뽐뿌·클리앙·루리웹 | 없음 | 핫딜 통합 |
| `weather.py` | 네이버 검색 | 없음 | 생활날씨 |

### 쿠팡 크롤링 방식

쿠팡은 **로컬 Chrome 을 모바일 모드로 실행**해 직접 크롤링합니다.

1. Google Chrome 을 Galaxy S21 모바일 에뮬레이션 (390x844) 으로 기동
2. Playwright 가 CDP (Chrome DevTools Protocol) 로 해당 크롬에 접속
3. 쿠팡 메인 → 검색 페이지 이동 후 결과 HTML 파싱
4. 상품명·가격·할인율·평점·리뷰수·이미지 추출
5. AF 코드 + CHANNEL_ID 로 파트너스 링크 자체 생성

### 봇 탐지 회피 (실 브라우저 환경)

- `headless` 모드 미사용 (실제 브라우저 창 렌더링)
- `AutomationControlled` 기능 비활성화
- 모바일 User-Agent 사용

### 파트너스 링크 생성

```
https://link.coupang.com/re/{ptype}?lptag={AF_CODE}&subid={CHANNEL_ID}&pageKey={key}...
```

`COUPANG_CHANNEL_ID_*` 환경변수로 **파이프라인별 채널 분리** 가능 (WP /
GitHub / Pinterest / 네이버블로그 / 네이버카페 / Threads / Twitter).

---

## 발행 플랫폼

| 모듈 | 인증 방식 | 비고 |
|------|----------|------|
| `wordpress.py` | JWT Bearer + REST API | 멀티 프로필 (`config.json` `wordpress_profiles`) |
| `github_pages.py` | git push (SSH 키) | Jekyll Markdown 형식 |
| `tistory.py` | Kakao OAuth 세션 | 역할별 다중 블로그 라우팅 |
| `naver_blog.py` | RSA 암호화 로그인 + CDP 폴백 | SE 에디터 API |
| `naver_cafe.py` | RSA 로그인 + Chrome 프로필 쿠키 | 카페 게시판 발행 |
| `twitter.py` | 쿠키 기반 (`browser-cookie3`) | X 잠김 방지 throttle 권장 |
| `threads.py` | Meta Graph API + 장기 토큰 | 자동 갱신 |
| `pinterest.py` | Pinterest API | 보드 생성/핀 등록 |
| `pinterest_playwright.py` | Google 로그인 (Playwright) | API 한계 회피용 |
| `instagram.py` | Playwright (publisher only) | 캡션·이미지 게시 |

---

## 파이프라인 카탈로그

프로젝트 루트에서 `python3 -m pipelines.<이름>` 형태로 실행합니다.

### 상품 → 블로그·SNS

```bash
python3 -m pipelines.coupang_to_wordpress     # 쿠팡 → WordPress (멀티 프로필)
python3 -m pipelines.coupang_to_github        # 쿠팡 → GitHub Pages
python3 -m pipelines.coupang_to_pinterest     # 쿠팡 → Pinterest
python3 -m pipelines.aliexpress_to_wordpress  # 알리 → WordPress (전체 프로필)
python3 -m pipelines.aliexpress_to_tistory    # 알리 → 티스토리
```

`coupang_to_wordpress` 흐름:

1. WordPress JWT 인증 확인
2. ItemScout 키워드 풀에서 미사용 키워드 N 개 선택
3. 각 키워드로 쿠팡 모바일 크롤링 (상품 10개)
4. HTML 카드 템플릿으로 포스트 본문 생성
5. WordPress REST API 로 발행
6. 발행 성공한 키워드를 `used_keywords.json` 에 기록

> **알리 첫 실행** — 캡차 수동 대응을 위해 `ALIEXPRESS_HEADLESS=false` 로
> 실행 후 로그인. `storage_state` 가 저장되어 이후 자동 재사용됩니다.

### 뉴스 → 멀티 채널

```bash
python3 -m pipelines.newspick_to_tistory
python3 -m pipelines.newspick_to_wordpress
NAVER_TARGET=blog python3 -m pipelines.newspick_to_naver
NAVER_TARGET=cafe python3 -m pipelines.newspick_to_naver
python3 -m pipelines.newspick_to_sns          # 트위터 + 스레드
```

### 공공데이터 → 블로그

```bash
python3 -m pipelines.riseset_to_naver         # 일출/일몰 → 네이버 블로그
python3 -m pipelines.riseset_to_tistory       # 일출/일몰 → 티스토리
python3 -m pipelines.policy_to_tistory        # 정책브리핑 RSS → 티스토리
python3 -m pipelines.policy_to_tistory --count 5
python3 -m pipelines.realestate_to_blog       # 부동산 → 티스토리/네이버
```

### 자체 사이트 백링크 → SNS

자체 블로그 URL 들을 사이트맵에서 수집하여 Twitter / Threads 에 반복 노출.

```bash
python3 -m pipelines.backlink_to_sns
```

### 새 파이프라인 추가하기

`pipelines/<my_pipeline>.py` 파일에 `run()` 함수와 `SCHEDULE` dict 만 정의하면
`scheduler_runner` 가 자동 발견합니다.

```python
# pipelines/my_pipeline.py
SCHEDULE = {
    "env":  "SCHEDULE_MY_PIPELINE",
    "func": "run",
    "args_from_env": (
        "MY_CATEGORY:default",
        "MY_COUNT:3:int",
    ),
}

def run(category: str, count: int) -> dict:
    # 작업 수행
    return {"published": count, "total": count}
```

`.env` 에 `SCHEDULE_MY_PIPELINE=07:00,19:00` 추가만으로 매일 2회 실행됩니다.

---

## 스케줄러

각 파이프라인 모듈 상단의 `SCHEDULE` 메타를 `pkgutil` 로 자동 스캔합니다.

```python
SCHEDULE = {
    "env":  "SCHEDULE_COUPANG_WP",      # 시간 읽을 .env 키
    "func": "run",                       # 호출할 함수명
    "args_from_env": (                   # (선택) 인자를 env 에서 읽기
        "NEWSPICK_CATEGORY:추천",         # 기본값
        "POST_COUNT:3:int",               # 타입 캐스팅
    ),
}
```

```bash
# 포그라운드
python3 -m pipelines.scheduler_runner

# 백그라운드 (터미널 닫아도 유지)
nohup python3 -m pipelines.scheduler_runner > scheduler.log 2>&1 &

# 로그 모니터링
tail -f scheduler.log
```

### 기본 스케줄 환경변수 (HH:MM, 콤마 구분)

```ini
SCHEDULE_COUPANG_WP=07:00                # 쿠팡→WordPress
SCHEDULE_COUPANG_GITHUB=07:30            # 쿠팡→GitHub Pages
SCHEDULE_COUPANG_PINTEREST=08:00         # 쿠팡→Pinterest
SCHEDULE_ALIEXPRESS_WP=11:30             # 알리→WordPress
SCHEDULE_ALIEXPRESS_TISTORY=12:30        # 알리→티스토리
SCHEDULE_ALIEXPRESS_THREADS=14:30        # 알리→Threads
SCHEDULE_ALIEXPRESS_NAVER_BLOG=13:30     # 알리→네이버블로그
SCHEDULE_NEWSPICK_TISTORY=09:00,18:00
SCHEDULE_NEWSPICK_WP=10:00,19:00
SCHEDULE_NEWSPICK_NAVER=11:00
SCHEDULE_NEWSPICK_SNS=08:00,20:00
SCHEDULE_RISESET_NAVER=06:30
SCHEDULE_RISESET_TISTORY=06:40
SCHEDULE_POLICY_TISTORY=12:00,22:00
SCHEDULE_BACKLINK_SNS=13:00,21:00
SCHEDULE_REALESTATE=06:00
SCHEDULE_THREADS_REFRESH=03:00           # Threads 토큰 갱신
```

값을 비우면 해당 파이프라인은 자동 실행에서 제외됩니다.

### Windows 운영 (작업 스케줄러)

Windows에는 `nohup` 이 없으므로 **작업 스케줄러**에 등록해 사용자 로그온 시
자동으로 띄우는 방식을 권장합니다. PowerShell 설치 스크립트가 동봉돼 있어
1회 실행으로 끝납니다.

```powershell
# 프로젝트 루트에서
powershell -ExecutionPolicy Bypass -File scripts\install_scheduler_task.ps1
```

스크립트 동작:
- `AutoPublishingScheduler` 라는 이름의 작업을 생성 (재실행 시 기존 것 자동 제거)
- 트리거: 현재 사용자 **로그온 시**
- 액션: `scripts\run_scheduler.bat` (PYTHONIOENCODING=utf-8 으로 `python -m
  pipelines.scheduler_runner` 실행, stdout 을 `scheduler.log` 에 누적)
- 설정: 실행 시간 제한 없음, 실패 시 1분 간격 3회 재시도, 배터리 무관 실행
- Principal: Interactive (브라우저 자동화에 데스크톱 세션 필요)

| 작업 | PowerShell |
|------|-----------|
| 즉시 시작 | `Start-ScheduledTask -TaskName 'AutoPublishingScheduler'` |
| 정지 | `Stop-ScheduledTask -TaskName 'AutoPublishingScheduler'` |
| 상태 확인 | `Get-ScheduledTask -TaskName 'AutoPublishingScheduler'` |
| 로그 모니터링 | `Get-Content scheduler.log -Tail 50 -Wait` |
| 작업 제거 | `Unregister-ScheduledTask -TaskName 'AutoPublishingScheduler' -Confirm:$false` |

#### 운영 주의사항

**콘솔 한글 mojibake** — Windows 기본 콘솔 코드 페이지 (cp949) 가 로그의
`—` `📊` 등을 못 출력해 `UnicodeEncodeError` 가 난다. wrapper 배치가
`PYTHONIOENCODING=utf-8` 을 강제하므로 작업 스케줄러로 돌릴 때는 문제
없지만, 직접 CLI 로 디버깅할 때는 실행 전에 다음을 적용한다.

```powershell
$env:PYTHONIOENCODING = 'utf-8'
python -u -m pipelines.scheduler_runner
```

**orphan Playwright Chromium 정리** — 파이프라인을 실행 도중 강제 종료
(Ctrl+C / TaskKill / 작업 스케줄러 정지) 하면 부모 Python 만 죽고 Playwright
가 띄운 Chromium 자식 프로세스가 남아 `.sessions/<name>_profile/` 잠금을
유지한다. 다음 실행이 `BrowserType.launch_persistent_context: Target page,
context or browser has been closed` 로 실패하면 다음을 1회 실행한다.

```powershell
Get-CimInstance Win32_Process |
    Where-Object { $_.ExecutablePath -like '*ms-playwright*' } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
```

(사용자가 평소 쓰는 Google Chrome 은 `Program Files\Google\Chrome\...` 에
설치돼 있어 위 필터에 걸리지 않으니 안전하다.)

**카카오 토큰 초기 발급** — `scripts/kakao_auth.py` 가 띄우는 브라우저는
`https://localhost:5000/?code=...` 로 리다이렉트되며 "사이트에 연결할 수
없습니다" 페이지가 뜬다. 이게 정상 — 주소창의 URL 전체를 복사해 터미널에
붙여넣으면 토큰이 발급된다.

---

## 알림 시스템 (텔레그램 + 카카오톡 병행)

`common/notifier.py` 가 파이프라인 완료/에러/스케줄러 시작 시 **텔레그램 +
카카오톡 나와의 채팅** 양쪽으로 동시 발송합니다. 한쪽만 설정해도 정상 동작.

### 텔레그램 (1순위)

1. [@BotFather](https://t.me/BotFather) 에서 봇 생성 → 토큰 획득
2. 본인이 봇과 대화 시작 (`/start`)
3. `https://api.telegram.org/bot{TOKEN}/getUpdates` 로 `chat_id` 확인

```ini
TELEGRAM_BOT_TOKEN=123456:ABC...
TELEGRAM_CHAT_ID=987654321
```

### 카카오톡 나와의 채팅 (병행)

엔드포인트: `https://kapi.kakao.com/v2/api/talk/memo/default/send`
access_token (6h) 만료 시 refresh_token (60d) 으로 자동 갱신.

**초기 발급 (1회)**

1. [developers.kakao.com](https://developers.kakao.com) → 앱 생성 → 카카오 로그인 ON
2. Redirect URI 에 `https://localhost:5000` 등록
3. 동의항목에 **"카카오톡 메시지 전송"** 필수 동의 추가
4. `.env` 에 `KAKAO_REST_API_KEY` 입력 후 `python scripts/kakao_auth.py` 실행
5. `.env` 에 `KAKAO_ACCESS_TOKEN`, `KAKAO_REFRESH_TOKEN` 자동 저장

### 톡캘린더 실패 로그 (선택)

파이프라인이 실패·부분실패로 끝나면 카카오톡 톡캘린더 `자동발행기록` 서브
캘린더에 빨강(완전실패) / 주황(부분실패) 이벤트가 자동 등록됩니다. 모바일에서
월간뷰로 실패 이력을 한눈에 추적할 수 있습니다.

**활성화**

1. developers.kakao.com → 앱 → 동의항목 → **캘린더(`talk_calendar`)** 를
   "이용 중 동의" 로 설정
2. `scripts/kakao_auth.py` 의 scope 가 `"talk_message,talk_calendar"` 인지 확인
3. `python scripts/kakao_auth.py` 재실행 → 새 scope 포함 토큰 재발급
4. 스모크 테스트: `python -m tools.test_kakao_calendar all`

**동작**

| 상황 | 캘린더 기록 |
|------|------------|
| `published == total` (전체 성공) | 없음 (텔레그램·카톡 메시지만) |
| `0 < published < total` (부분실패) | ORANGE 이벤트 |
| `published == 0` 또는 예외 | RED 이벤트 |
| 토큰·scope 없음 | 조용히 skip — 파이프라인 영향 없음 |

이벤트는 5분 단위 (카카오 API 제약: `start_at` 은 5분 격자 floor).

---

## 환경변수 (.env)

`.env.example` 을 복사하여 `.env` 를 만들고 값을 채워넣습니다.
사용하지 않는 플랫폼은 빈 값으로 두면 해당 기능만 비활성화됩니다.

> **공개 사용자 주의** — `.env.example` 의 일부 디폴트(블로그 ID, 채널명,
> 백링크 URL, 봇 핸들)는 원저자 운영 환경의 실값입니다. 본인 환경에 맞게
> **반드시 교체**하세요.

### WordPress

| 변수 | 설명 | 필수 |
|------|------|------|
| `WP_SITE_URL` | WordPress 사이트 URL | ✓ |
| `WP_USERNAME` | WordPress 사용자명 | ✓ |
| `WP_JWT_TOKEN` | JWT 인증 토큰 | ✓ |
| `WP_APP_PASSWORD` | Application Password (대체 인증) | - |
| `WP_CATEGORY_ID` | 발행 카테고리 ID | ✓ |
| `WP_TAG_ID` | 발행 태그 ID | ✓ |

여러 사이트에 발행하려면 `config.json` 의 `wordpress_profiles` 배열을 사용합니다
(`config.json.example` 참조).

### 쿠팡 파트너스 (파이프라인별 채널 ID 분리)

| 변수 | 설명 | 필수 |
|------|------|------|
| `COUPANG_AF_CODE` | 파트너스 AF 코드 | ✓ |
| `COUPANG_CHANNEL_ID` | 기본 채널 (파이프라인별 오버라이드 없을 때) | ✓ |
| `COUPANG_CHANNEL_ID_WP` | WordPress 채널 | - |
| `COUPANG_CHANNEL_ID_GITHUB` | GitHub Pages (비우면 repo 이름으로 자동 감지) | - |
| `COUPANG_CHANNEL_ID_PINTEREST` | Pinterest 채널 | - |
| `COUPANG_CHANNEL_ID_NAVERBLOG` | 네이버 블로그 채널 | - |
| `COUPANG_CHANNEL_ID_NAVERCAFE` | 네이버 카페 채널 | - |
| `COUPANG_CHANNEL_ID_THREADS` | Threads 채널 | - |
| `COUPANG_CHANNEL_ID_TWITTER` | Twitter 채널 | - |
| `COUPANG_FAKE_LINK` | 폴백용 링크 | - |
| `COUPANG_PRODUCT_COUNT` | 키워드당 상품 수 (기본 3) | - |
| `COUPANG_ACCESS_KEY` / `COUPANG_SECRET_KEY` | 파트너스 공식 API 키 (선택) | - |

### 알리익스프레스

| 변수 | 설명 | 필수 |
|------|------|------|
| `ALIEXPRESS_EMAIL` / `ALIEXPRESS_PASSWORD` | 알리 로그인 | ✓ |
| `ALIEXPRESS_TRACKING_ID` | 제휴 링크 tracking ID (기본 `wordpress`) | - |
| `ALIEXPRESS_HEADLESS` | 첫 로그인은 `false` (캡차 대응) | - |
| `ALIEXPRESS_LOGIN_WAIT` | 수동 로그인 대기 시간(초) | - |
| `ALIEXPRESS_POST_COUNT` | 1회 실행당 키워드/발행 수 | - |
| `ALIEXPRESS_PRODUCT_COUNT` | 1글당 상품 수 | - |

### 네이버

| 변수 | 설명 | 필수 |
|------|------|------|
| `NAVER_USERNAME` / `NAVER_PASSWORD` | 네이버 계정 | 뉴스픽/블로그/카페 |
| `NAVER_BLOG_ID` | 블로그 ID | 블로그 |
| `NAVER_CAFE_ID` / `NAVER_CAFE_MENU_ID` | 카페 URL ID / 게시판 ID | 카페 |
| `NAVER_CHROME_PROFILE` | 쿠키 추출용 Chrome 프로필 (예: `Profile 2`) | 카페 |
| `NAVER_CLIENT_ID` / `NAVER_CLIENT_SECRET` | DataLab API 키 | DataLab |

### 티스토리 (역할별 다중 블로그 라우팅)

| 변수 | 역할 |
|------|------|
| `TISTORY_BLOG_NAME` | 폴백 블로그 ID (`<name>.tistory.com` 의 `<name>`) |
| `TISTORY_BLOG_REALESTATE` | 분양정보 |
| `TISTORY_BLOG_RISESET` | 일출일몰 |
| `TISTORY_BLOG_NEWSPICK` | 뉴스픽 |
| `TISTORY_BLOG_POLICY` | 정책정보 |
| `TISTORY_BLOG_RESERVED` | 예약 슬롯 |
| `TISTORY_BLOG_BACKLINK` | 백링크 소스 |
| `TISTORY_BLOG_ALIEXPRESS` | 알리익스프레스 상품글 |
| `TISTORY_CATEGORY` | 카테고리명 (선택) |

> 단일 블로그만 운영하면 `TISTORY_BLOG_NAME` 만 채워도 됩니다.

### 카카오 (알림 + 로컬 검색)

| 변수 | 설명 | 필수 |
|------|------|------|
| `KAKAO_REST_API_KEY` | Developers REST API 키 | 카카오 기능 전반 |
| `KAKAO_ACCESS_TOKEN` | 알림 발송용 (자동 갱신) | 카카오톡 알림 |
| `KAKAO_REFRESH_TOKEN` | 갱신용 | 카카오톡 알림 |
| `KAKAO_NATIVE_APP_KEY` / `KAKAO_JAVASCRIPT_KEY` / `KAKAO_ADMIN_KEY` | 기타 앱 키 | - |

### 백링크

| 변수 | 설명 | 필수 |
|------|------|------|
| `BACKLINK_SITES` | 자체 블로그 URL (콤마 구분) | ✓ |
| `BACKLINK_TARGETS` | 발행 플랫폼 (`twitter,threads`) | - |
| `BACKLINK_COUNT` | 1회 실행당 플랫폼별 발행 수 | - |
| `BACKLINK_THROTTLE` | 발행 사이 대기 초 (X 잠김 방지, 기본 300) | - |
| `BACKLINK_STRATEGIES` | `jetpack,yoast,wp_core,rest` | - |
| `BACKLINK_MESSAGE_PREFIX` | 메시지 프리픽스 | - |

### 정책브리핑

| 변수 | 설명 |
|------|------|
| `POLICY_FEEDS` | korea.kr RSS 피드명 (기본 `정책뉴스,보도자료,이슈인사이트`) |
| `POLICY_POST_COUNT` | 1회 실행당 발행 수 (기본 3) |

### 알림 / 기타

| 변수 | 설명 | 필수 |
|------|------|------|
| `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` | 텔레그램 알림 | 알림용 |
| `GEMINI_API_KEY` | Gemini API 키 | AI 폴백 |
| `DATA_GO_KR_KEY` | 공공데이터포털 키 | 부동산/일출일몰 |
| `AI_PROVIDER` | `claude` (기본) 또는 `gemini` | - |
| `SCHEDULE_THREADS_REFRESH` | Threads 토큰 갱신 시각 (기본 `03:00`) | - |

---

## 발행 콘텐츠 예시

상품 파이프라인은 아래 구조의 HTML 카드로 발행됩니다:

```
제목: "{키워드} TOP10 추천 - {1위 상품명}"

본문:
┌─────────────────────────────────────┐
│ 분석 도구를 이용하여 데이터 기반으로  │
│ 상품을 추천해드리고 있습니다          │
├──────────┬──────────────────────────┤
│          │ (1) 상품명               │
│  상품    │ ̶할̶인̶전̶ 할인가격          │
│  이미지  │ ⭐ 4.8 (1,234개 리뷰)   │
│          │ | 내일(목) 도착 보장      │
├──────────┼──────────────────────────┤
│          │ (2) 상품명               │
│  상품    │ ...                      │
│  이미지  │                          │
├──────────┴──────────────────────────┤
│ ※ 파트너스 활동을 통해 일정액의      │
│   수수료를 제공받을 수 있습니다.      │
└─────────────────────────────────────┘
```

> **어필리에이트 의무 고지** — 모든 어필리에이트 카드 푸터에 자동으로 의무
> 고지 문구가 들어갑니다. 이는 공정거래위원회 추천·보증 심사지침 준수 목적
> 이며 임의로 제거하지 않는 것을 권장합니다.

---

## 기술 스택

| 구성 요소 | 기술 |
|----------|------|
| 언어 | Python 3.9+ |
| 쿠팡 크롤링 | Chrome CDP + Playwright (모바일 에뮬레이션) |
| 알리 크롤링 | Playwright + storage_state |
| 키워드 수집 | ItemScout / Pandarank / 네이버 DataLab (모두 API 키 불필요) |
| WordPress 발행 | REST API + JWT Bearer (멀티 프로필) |
| 티스토리 발행 | Kakao OAuth 세션 + 역할별 다중 블로그 라우팅 |
| 네이버 발행 | RSA 암호화 로그인 + CDP 쿠키 폴백, SE 에디터 API |
| HTML 파싱 | BeautifulSoup4 + lxml |
| 스케줄링 | `schedule` 라이브러리 + `SCHEDULE` 메타 자동 발견 |
| AI 생성 | Claude CLI → Gemini 자동 폴백 |
| 알림 | 텔레그램 Bot API + 카카오톡 OAuth |
| 환경변수 | `python-dotenv` |

---

## 트러블슈팅 · FAQ

<details>
<summary><b>Q. 쿠팡 크롤링이 매번 빈 결과를 반환합니다</b></summary>

A. Chrome 이 모바일 모드로 실행됐는지, headless 가 아닌지 확인하세요.
`headless=False` 가 강제됩니다 — CI/원격 서버에서 동작하지 않을 수 있습니다.
로컬 데스크탑(또는 Xvfb 가 있는 헤드리스 X 서버) 실행을 권장합니다.

</details>

<details>
<summary><b>Q. 알리익스프레스가 캡차 페이지에서 멈춥니다</b></summary>

A. `common/aliexpress_login.py` 의 자동 로그인 흐름은 카카오 SSO 경로만
지원합니다. Google 계정 연동, 이메일/비밀번호 직접 로그인 등 다른 방식이면
**`tools/aliexpress_manual_login.py` 로 1회 수동 로그인** 후 storage_state
를 저장하세요. 카카오 SSO 인 경우 `ALIEXPRESS_HEADLESS=false` 첫 실행으로
캡차 통과해도 됩니다.

```bash
python tools/aliexpress_manual_login.py    # Google/이메일 등 비-카카오
```

저장 후 `data/aliexpress_storage.json` 이 생성되며 이후 실행은 자동.

</details>

<details>
<summary><b>Q. 뉴스픽 / 네이버 / 알리 자동 로그인이 캡차/2FA 에서 막힙니다</b></summary>

A. 각 플랫폼별 수동 로그인 헬퍼를 1회 실행하면 영속 프로필/세션 쿠키가
저장돼 이후 파이프라인이 자동 인증됩니다. 모두 `tools/` 아래.

```bash
python tools/naver_manual_login.py        # → .sessions/naver_blog_<BLOG_ID>.pkl
python tools/aliexpress_manual_login.py   # → data/aliexpress_storage.json
python tools/newspick_manual_login.py     # → .sessions/newspick_profile/ (영속)
```

각 헬퍼는 headful Chromium 을 띄워 사용자가 직접 로그인 (캡차/2FA/Google/Kakao
등 어떤 방식이든 OK) 한 뒤 SESSION/auth 쿠키 존재를 검증해 저장합니다.

특히 newspick 은 `--disable-popup-blocking` 등 Chromium 인자가 추가돼
about:blank popup 이슈를 우회합니다.

</details>

<details>
<summary><b>Q. 카카오톡 알림이 안 옵니다</b></summary>

A. 가장 흔한 원인은 동의항목 설정입니다. developers.kakao.com → 앱 → 동의
항목에서 **"카카오톡 메시지 전송"** 이 "이용 중 동의"인지 확인하세요. 그 후
`python scripts/kakao_auth.py` 로 토큰 재발급. 401 응답이 나면 `notifier`
가 자동으로 refresh 를 시도합니다.

</details>

<details>
<summary><b>Q. 네이버 로그인이 RSA 암호화에서 실패합니다</b></summary>

A. 네이버는 새 IP / Playwright 자동화에 캡차·2단계 인증을 자주 띄워 RSA
자동 로그인이 막힙니다. **`python tools/naver_manual_login.py`** 로 1회
수동 로그인하면 `.sessions/naver_blog_<BLOG_ID>.pkl` 에 쿠키가 저장돼
이후 파이프라인이 자동 인증됩니다. 계정을 바꾸면 새 BLOG_ID 로 다시
1회 실행 필요.

`common/auth.py` 의 RSA 키 변경에 따른 일시 오류라면 재시도로 해결되기도
합니다. 지속 실패 시 Chrome 프로필 쿠키 폴백 (`NAVER_CHROME_PROFILE`) 도
가능합니다.

</details>

<details>
<summary><b>Q. 트위터/X 가 자주 잠깁니다</b></summary>

A. `BACKLINK_THROTTLE=300` (초) 이상으로 발행 간격을 벌리세요. 짧은 간격
연속 발행이 잠김 트리거입니다. 또한 일일 발행량을 10건 이하로 유지하는 것을
권장합니다.

</details>

<details>
<summary><b>Q. 키워드가 빠르게 소진됩니다</b></summary>

A. `data/used_keywords.json` 은 영구 기록입니다. 의도적으로 재사용하려면
해당 파일에서 일부 키워드를 제거하거나, 다중 소스(`collect_all_keywords_multi`)
로 풀을 확장하세요.

</details>

<details>
<summary><b>Q. 스케줄러가 실행되지 않습니다</b></summary>

A. (1) `.env` 의 `SCHEDULE_*` 값이 비어있는지 확인 — 빈 값이면 자동 제외됩니다.
(2) `tail -f scheduler.log` 로 실제 등록된 작업 수를 확인하세요. (3) 모듈에
`SCHEDULE` dict 와 `run` 함수가 둘 다 있어야 자동 발견됩니다.

</details>

<details>
<summary><b>Q. WordPress JWT 토큰이 만료됐습니다</b></summary>

A. JWT Authentication 플러그인을 통해 새 토큰을 발급받으세요. 만료가 잦다면
`WP_APP_PASSWORD` (Application Password) 방식이 더 안정적입니다.

</details>

---

## 면책 및 법적 고지

이 프로젝트는 **교육·연구·개인 자동화 목적의 오픈소스 도구**입니다.
사용 시 다음 사항을 본인 책임 하에 확인하세요.

1. **각 플랫폼의 이용약관(ToS) 준수** — 본 도구는 WordPress, 티스토리,
   네이버, 카카오, Twitter/X, Threads, Pinterest, Instagram, 쿠팡 등의
   공식 또는 비공식 인터페이스를 사용합니다. 일부 동작(자동 게시, 사이트
   크롤링, 쿠키 기반 인증)은 해당 플랫폼의 약관에 의해 제한될 수 있습니다.
   **계정 정지·차단 등 결과는 사용자 본인의 책임**입니다.

2. **어필리에이트·광고 표시 의무** — 쿠팡 파트너스, 알리익스프레스 어필리에이트,
   기타 제휴 마케팅 사용 시 각국 법령(한국: 공정거래위원회 추천·보증 심사
   지침, 미국: FTC 가이드라인 등)에 따른 광고성 표시 의무를 준수해야 합니다.
   본 도구는 카드 푸터에 자동 고지를 삽입하지만, **최종 검토와 추가 의무는
   사용자에게 있습니다**.

3. **개인정보 및 자격 증명** — `.env`, `.sessions/`, `data/`, `config.json`
   에는 비밀번호·OAuth 토큰·쿠키가 저장됩니다. 이들은 `.gitignore` 로
   관리되지만, **공개 저장소·CI 로그·스크린샷에 노출되지 않도록 본인이
   주의**해야 합니다.

4. **공공 데이터 출처 표기** — 일출일몰·부동산·정책 등 공공데이터 사용 시
   해당 기관의 출처 표기 의무를 준수해야 합니다. 기본 템플릿에는 출처가
   포함되어 있습니다.

5. **저작권** — 뉴스픽 등 외부 소스의 콘텐츠를 그대로 재발행하는 경우 저작권
   침해 소지가 있습니다. 요약·재가공·출처 명시 등 정당한 사용 형태를 권장합니다.

6. **무보증 (No Warranty)** — 본 소프트웨어는 "있는 그대로(AS-IS)" 제공되며,
   상품성·특정 목적 적합성·비침해성에 대한 어떠한 명시적·묵시적 보증도
   하지 않습니다. 사용으로 인해 발생하는 모든 손해(계정 정지, 데이터 손실,
   법적 분쟁 등)에 대해 저자는 책임을 지지 않습니다.

> **핵심 원칙**: 본인의 사이트·계정·데이터에 대해서만 자동화하고, 타인의
> 자산을 침해하지 마세요. 의심스러우면 사용을 중단하고 법률 자문을 받으세요.

---

## 기여하기

이슈와 PR 환영합니다. 큰 변경은 먼저 이슈를 열어 논의를 권장합니다.

### 개발 워크플로

```bash
git clone <fork-url>
cd auto-publishing
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium

# 개발 후 실 환경에서 단일 파이프라인으로 검증
python3 -m pipelines.<your_pipeline>
```

### 새 파이프라인 작성 가이드

1. `pipelines/<name>.py` 생성 — 모듈 상단에 docstring 으로 목적 명시
2. `SCHEDULE` dict + `run()` 함수 정의 (위 [예시](#새-파이프라인-추가하기) 참조)
3. 실행 결과는 `{"published": int, "total": int, ...}` 형태 dict 반환 권장
   — `notifier` 가 자동으로 성공/부분실패/실패를 판별
4. 외부 API 호출은 `common/` 의 기존 헬퍼(`auth`, `session`, `notifier`,
   `url_shortener`) 를 우선 사용
5. 환경변수는 `.env.example` 에 디폴트 값과 주석 함께 추가

### 코드 스타일

- Python 3.9+ 문법 사용 가능 (typing, walrus 등)
- 타입 힌트 권장
- 주석은 **왜** 그렇게 했는지 (How 가 아닌 Why) 위주로
- f-string 우선 사용
- 외부 의존성은 `requirements.txt` 에 명시

### 보안 가드레일

- `.env`, `config.json`, `.sessions/` 절대 커밋 금지 (`.gitignore` 확인)
- 비밀 키·토큰·비밀번호는 PR 본문이나 스크린샷에도 노출되지 않도록 주의
- 새 외부 API 추가 시 인증 정보는 반드시 환경변수로 주입

---

## 라이선스

[MIT License](LICENSE) © 2026 MoonbirdThinker

자유롭게 사용·수정·배포할 수 있으며, 저작권 표시 및 라이선스 사본을 포함하면
됩니다. 무보증 — 자세한 내용은 `LICENSE` 파일과 [면책 및 법적 고지](#면책-및-법적-고지)
참조.

---

## 감사의 말

이 프로젝트는 다음 오픈소스 프로젝트와 공공 서비스 위에 만들어졌습니다.

- [Playwright](https://playwright.dev/) — 브라우저 자동화 엔진
- [BeautifulSoup4](https://www.crummy.com/software/BeautifulSoup/) — HTML 파싱
- [requests](https://requests.readthedocs.io/) — HTTP 클라이언트
- [schedule](https://schedule.readthedocs.io/) — 스케줄링
- [python-dotenv](https://github.com/theskumar/python-dotenv) — 환경변수 로드
- [공공데이터포털](https://www.data.go.kr/) — 부동산·일출일몰 등 공공 API
- [한국천문연구원](https://www.kasi.re.kr/) — 일출/일몰 데이터
- [korea.kr](https://www.korea.kr/) — 정책브리핑 RSS

기여자, 이슈 리포터, 그리고 운영 중 발생한 엣지 케이스를 공유해 주신 분들께
감사드립니다.
