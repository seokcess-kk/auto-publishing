"""heartbeat 단위 테스트."""
import common.heartbeat as heartbeat


def test_write_creates_file(tmp_path, monkeypatch):
    monkeypatch.setattr(heartbeat, "HEARTBEAT_FILE", tmp_path / "hb")
    heartbeat.write(pid=1234, registered=10, started_at="2026-05-11T00:00:00")
    assert (tmp_path / "hb").exists()


def test_read_returns_payload(tmp_path, monkeypatch):
    monkeypatch.setattr(heartbeat, "HEARTBEAT_FILE", tmp_path / "hb")
    heartbeat.write(pid=42, registered=20, started_at="2026-05-11T00:00:00")
    payload = heartbeat.read()
    assert payload is not None
    assert payload["pid"] == 42
    assert payload["registered"] == 20


def test_read_missing_returns_none(tmp_path, monkeypatch):
    monkeypatch.setattr(heartbeat, "HEARTBEAT_FILE", tmp_path / "absent")
    assert heartbeat.read() is None


def test_age_seconds(tmp_path, monkeypatch):
    monkeypatch.setattr(heartbeat, "HEARTBEAT_FILE", tmp_path / "hb")
    heartbeat.write(pid=1, registered=1, started_at="t")
    age = heartbeat.age_seconds()
    assert age is not None
    assert 0 <= age < 5  # 방금 썼으니 5초 이내


def test_age_seconds_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(heartbeat, "HEARTBEAT_FILE", tmp_path / "absent")
    assert heartbeat.age_seconds() is None


def test_clear_removes_file(tmp_path, monkeypatch):
    monkeypatch.setattr(heartbeat, "HEARTBEAT_FILE", tmp_path / "hb")
    heartbeat.write(pid=1, registered=1, started_at="t")
    assert (tmp_path / "hb").exists()
    heartbeat.clear()
    assert not (tmp_path / "hb").exists()


def test_clear_missing_no_error(tmp_path, monkeypatch):
    monkeypatch.setattr(heartbeat, "HEARTBEAT_FILE", tmp_path / "absent")
    heartbeat.clear()  # should not raise


def test_read_corrupted_json(tmp_path, monkeypatch):
    monkeypatch.setattr(heartbeat, "HEARTBEAT_FILE", tmp_path / "hb")
    (tmp_path / "hb").write_text("not json{{{", encoding="utf-8")
    assert heartbeat.read() is None
