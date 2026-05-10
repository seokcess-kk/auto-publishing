"""
색인 자격 검증 헬퍼

Google Indexing API + Naver Search Advisor 자격이 .env 에 채워졌는지,
실제 색인 요청이 통과하는지를 1건 URL 로 시험한다.

실행:
    python tools/test_indexing.py [URL]

URL 생략 시 publish_queue.json 의 가장 최근 URL 1개 사용.

각 채널 결과 출력 — ok/limit/no_permission/error/(자격없음).
실제 운영 색인 큐에는 영향 없음 (publish_queue 의 indexed 플래그 갱신 안 함).
"""
import json
import os
import sys
from pathlib import Path

_BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BASE_DIR))

from dotenv import load_dotenv
load_dotenv(_BASE_DIR / ".env")


def _pick_url() -> str:
    if len(sys.argv) >= 2:
        return sys.argv[1].strip()
    queue_path = _BASE_DIR / "data" / "publish_queue.json"
    if not queue_path.exists():
        print("✗ publish_queue.json 없음 — URL 인자를 직접 넘기세요")
        sys.exit(1)
    with open(queue_path, "r", encoding="utf-8") as f:
        queue = json.load(f)
    queue = sorted(queue, key=lambda it: it.get("queued_at", ""), reverse=True)
    if not queue:
        print("✗ publish_queue 가 비어있음 — URL 인자 필요")
        sys.exit(1)
    return queue[0]["url"]


def test_google(url: str) -> str:
    print("=== Google Indexing API ===")
    # 자격 점검
    has_default = bool(os.getenv("GOOGLE_INDEXING_KEY_DEFAULT"))
    has_legacy  = bool(os.getenv("GOOGLE_INDEXING_SA_JSON"))
    use_adc = os.getenv("GOOGLE_INDEXING_USE_ADC", "").lower() == "true"
    if not (has_default or has_legacy or use_adc):
        print("  ✗ GOOGLE_INDEXING_KEY_DEFAULT / GOOGLE_INDEXING_SA_JSON 미설정")
        print("    또는 GOOGLE_INDEXING_USE_ADC=true 미설정")
        return "skip"

    try:
        from common.indexing_google import _get_sa_json_path, submit_urls
    except Exception as e:
        print(f"  ✗ 모듈 import 실패: {e}")
        return "error"

    sa_path = _get_sa_json_path(domain="", allow_default=True)
    if use_adc:
        print("  인증   : Application Default Credentials")
    elif not sa_path:
        print("  ✗ SA JSON 파일 경로 결정 실패 — env 값 확인")
        return "skip"
    elif not os.path.exists(sa_path):
        print(f"  ✗ SA JSON 파일이 존재하지 않음: {sa_path}")
        return "error"
    else:
        print(f"  SA JSON: {sa_path}")
    print(f"  URL    : {url}")
    print("  → 색인 요청 중...")
    try:
        result = submit_urls([url])
    except Exception as e:
        print(f"  ✗ 호출 예외: {e}")
        return "error"

    status = result.get(url, "error")
    icon = {"ok": "✓", "limit": "⚠", "no_permission": "✗",
            "error": "✗"}.get(status, "?")
    msg_map = {
        "ok":            "성공 — Google Indexing 요청 통과",
        "limit":         "일일 한도 200건 초과",
        "no_permission": "권한 없음 — Search Console 소유자 권한 확인 필요",
        "error":         "API 오류 — 위 로그 확인",
    }
    print(f"  {icon} 결과: {status} ({msg_map.get(status, '알 수 없음')})")
    return status


def test_naver(url: str) -> str:
    print()
    print("=== Naver Search Advisor ===")
    if not (os.getenv("NAVER_SEARCHADVISOR_USERNAME") and
             os.getenv("NAVER_SEARCHADVISOR_PASSWORD")):
        print("  ✗ NAVER_SEARCHADVISOR_USERNAME / PASSWORD 미설정")
        return "skip"

    print(f"  USERNAME: {os.getenv('NAVER_SEARCHADVISOR_USERNAME')}")
    print(f"  URL     : {url}")
    print("  → 색인 요청 중... (Playwright 브라우저 첫 실행 시 시간 걸림)")

    try:
        from common.indexing_naver import submit_urls
    except Exception as e:
        print(f"  ✗ 모듈 import 실패: {e}")
        return "error"

    try:
        result = submit_urls([url])
    except Exception as e:
        print(f"  ✗ 호출 예외: {e}")
        return "error"

    status = result.get(url, "error")
    icon = "✓" if status == "ok" else "✗"
    print(f"  {icon} 결과: {status}")
    return status


if __name__ == "__main__":
    url = _pick_url()
    print(f"테스트 URL: {url}\n")

    g = test_google(url)
    n = test_naver(url)

    print()
    print("=== 종합 ===")
    print(f"  Google: {g}")
    print(f"  Naver : {n}")
    if g in ("ok", "limit") and n == "ok":
        print("  → 자격 정상. 다음 SCHEDULE_INDEX 시간에 자동 색인 활성화됨.")
    elif g == "skip" and n == "skip":
        print("  → 양쪽 자격 모두 미설정. 아래 가이드 참고 후 .env 채우기:")
        print("     https://developers.google.com/search/apis/indexing-api")
        print("     https://searchadvisor.naver.com")
    else:
        print("  → 일부 실패. 위 메시지의 원인 해결 후 재시도.")
