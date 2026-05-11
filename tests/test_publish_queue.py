"""publish_queue 단위 테스트.

목표: ROI 매칭 정확도와 색인 파이프라인 무결성을 보장하는 가드들이 살아있는지.
- 절대 URL 가드 (Google Indexing API 가 거부하는 형태 사전 차단)
- 중복 URL 거부 (같은 URL 두 번 add 시 두 번째 무시)
- mark_status 의 X/O 변환 + 사유 보존
- mark_status_bulk 의 다건 갱신
"""
import json

import pytest

from common.publish_queue import (
    add_url, get_pending, mark_done, mark_status, mark_status_bulk,
)


@pytest.fixture
def queue_path(tmp_path):
    return str(tmp_path / "queue.json")


def test_add_url_basic(queue_path):
    assert add_url("https://example.com/a", "tistory",
                    title="hi", keyword="kw", source="coupang",
                    queue_path=queue_path) is True

    data = json.loads(open(queue_path, encoding="utf-8").read())
    assert len(data) == 1
    item = data[0]
    assert item["url"] == "https://example.com/a"
    assert item["platform"] == "tistory"
    assert item["keyword"] == "kw"
    assert item["source"] == "coupang"
    # 색인/백링크 초기값 X
    assert item["google_indexed"] == "X"
    assert item["naver_indexed"] == "X"
    assert item["backlinked"] == "X"


def test_add_url_rejects_non_absolute(queue_path):
    # 상대 URL — Google Indexing API 가 거부
    assert add_url("/path/only", "tistory", queue_path=queue_path) is False
    # 스킴 없는 URL
    assert add_url("example.com/a", "tistory", queue_path=queue_path) is False
    # ftp 같은 비-http 스킴
    assert add_url("ftp://example.com/a", "tistory", queue_path=queue_path) is False
    # 빈 문자열
    assert add_url("", "tistory", queue_path=queue_path) is False
    # 공백만
    assert add_url("   ", "tistory", queue_path=queue_path) is False


def test_add_url_rejects_duplicate(queue_path):
    assert add_url("https://example.com/a", "tistory", queue_path=queue_path) is True
    assert add_url("https://example.com/a", "tistory", queue_path=queue_path) is False
    data = json.loads(open(queue_path, encoding="utf-8").read())
    assert len(data) == 1


def test_get_pending_filters_by_field(queue_path):
    add_url("https://a.com/1", "tistory", queue_path=queue_path)
    add_url("https://a.com/2", "tistory", queue_path=queue_path)
    mark_done("https://a.com/1", "google_indexed", queue_path=queue_path)

    pend = get_pending("google_indexed", queue_path=queue_path)
    assert len(pend) == 1
    assert pend[0]["url"] == "https://a.com/2"

    pend_naver = get_pending("naver_indexed", queue_path=queue_path)
    assert len(pend_naver) == 2  # 둘 다 naver 는 아직 X


def test_get_pending_rejects_unknown_field(queue_path):
    with pytest.raises(ValueError):
        get_pending("nonexistent", queue_path=queue_path)


def test_mark_status_ok_promotes_to_O(queue_path):
    add_url("https://a.com/1", "tistory", queue_path=queue_path)
    assert mark_status("https://a.com/1", "google_indexed", "ok",
                        queue_path=queue_path) is True

    data = json.loads(open(queue_path, encoding="utf-8").read())
    item = data[0]
    assert item["google_indexed"] == "O"
    assert item["google_indexed_status"] == "ok"


def test_mark_status_failure_keeps_X_and_records_reason(queue_path):
    add_url("https://a.com/1", "tistory", queue_path=queue_path)
    mark_status("https://a.com/1", "google_indexed", "no_permission",
                 message="SA 거부", queue_path=queue_path)

    data = json.loads(open(queue_path, encoding="utf-8").read())
    item = data[0]
    # X 유지 — 색인 안 됐으니까
    assert item["google_indexed"] == "X"
    # 사유는 보존 — 대시보드에 표시
    assert item["google_indexed_status"] == "no_permission"
    assert item["google_indexed_message"] == "SA 거부"


def test_mark_status_truncates_long_message(queue_path):
    add_url("https://a.com/1", "tistory", queue_path=queue_path)
    long_msg = "x" * 500
    mark_status("https://a.com/1", "google_indexed", "error",
                 message=long_msg, queue_path=queue_path)

    data = json.loads(open(queue_path, encoding="utf-8").read())
    # 200자 cap
    assert len(data[0]["google_indexed_message"]) == 200


def test_mark_status_bulk(queue_path):
    for i in range(3):
        add_url(f"https://a.com/{i}", "tistory", queue_path=queue_path)

    results = {
        "https://a.com/0": "ok",
        "https://a.com/1": ("no_permission", "권한 없음"),
        "https://a.com/2": ("error", "타임아웃"),
    }
    changed = mark_status_bulk(results, "google_indexed", queue_path=queue_path)
    assert changed == 3

    data = json.loads(open(queue_path, encoding="utf-8").read())
    by_url = {it["url"]: it for it in data}
    assert by_url["https://a.com/0"]["google_indexed"] == "O"
    assert by_url["https://a.com/1"]["google_indexed"] == "X"
    assert by_url["https://a.com/1"]["google_indexed_status"] == "no_permission"
    assert by_url["https://a.com/2"]["google_indexed_message"] == "타임아웃"
