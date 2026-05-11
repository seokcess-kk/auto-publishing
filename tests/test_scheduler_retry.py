"""scheduler 의 일시적/영구 에러 분류 테스트.

목표: 재시도 정책이 의도대로 동작 — 네트워크 지연만 재시도, 권한 에러는 즉시 실패.
"""
import socket
import ssl

from common.scheduler import _is_transient


# ── 일시적 에러 (재시도 대상) ─────────────────────────────────────────

def test_transient_timeout():
    assert _is_transient(TimeoutError("timed out")) is True
    assert _is_transient(socket.timeout("timed out")) is True


def test_transient_connection_error():
    assert _is_transient(ConnectionError("conn refused")) is True
    assert _is_transient(ConnectionResetError("reset")) is True


def test_transient_ssl_error():
    assert _is_transient(ssl.SSLError("handshake fail")) is True


# ── 영구 에러 (즉시 실패) ─────────────────────────────────────────────

def test_permanent_permission_in_message():
    # 일시적 예외 타입이라도 메시지에 영구 마커 있으면 재시도 안 함
    class FakeTimeout(TimeoutError):
        pass

    assert _is_transient(FakeTimeout("no privilege to write")) is False
    assert _is_transient(TimeoutError("403 forbidden")) is False
    assert _is_transient(ConnectionError("captcha required")) is False


def test_permanent_known_4xx_in_message():
    assert _is_transient(TimeoutError("401 unauthorized")) is False


def test_non_network_exception_not_transient():
    # ValueError, KeyError 등은 코드 버그라 재시도 의미 없음
    assert _is_transient(ValueError("bad input")) is False
    assert _is_transient(KeyError("missing")) is False


def test_login_failed_not_transient():
    assert _is_transient(ConnectionError("로그인 실패")) is False
    assert _is_transient(ConnectionError("login failed")) is False
