"""backup 파이프라인 테스트.

검증 포인트:
- zip 생성 시 대상 파일이 모두 포함되는지
- rotation 이 오래된 zip 만 삭제하는지
- .env 토글 옵션
"""
import zipfile
from datetime import date, timedelta
from pathlib import Path

import pytest

import pipelines.backup as backup


@pytest.fixture
def isolated_project(tmp_path, monkeypatch):
    """가짜 프로젝트 루트 + 백업 디렉토리를 임시로 둠."""
    root = tmp_path / "proj"
    (root / "data").mkdir(parents=True)
    (root / "data" / "publish_queue.json").write_text('[]', encoding="utf-8")
    (root / ".sessions" / "newspick_profile").mkdir(parents=True)
    (root / ".sessions" / "newspick_profile" / "Cookies").write_text(
        "fake", encoding="utf-8")
    (root / ".env").write_text("FOO=bar", encoding="utf-8")

    backup_dir = tmp_path / "backups"

    monkeypatch.setattr(backup, "_BASE_DIR", root)
    monkeypatch.setattr(backup, "_DEFAULT_BACKUP_DIR", backup_dir)
    monkeypatch.delenv("BACKUP_DIR", raising=False)
    return root, backup_dir


def test_creates_dated_zip(isolated_project):
    root, backup_dir = isolated_project
    out = backup._create_backup()

    assert out.exists()
    assert out.name == f"{date.today().isoformat()}.zip"

    with zipfile.ZipFile(out) as zf:
        names = zf.namelist()
    # data + .sessions + .env 모두 포함
    assert any("publish_queue.json" in n for n in names)
    assert any("Cookies" in n for n in names)
    assert any(n == ".env" for n in names)


def test_excludes_env_when_disabled(isolated_project, monkeypatch):
    monkeypatch.setenv("BACKUP_INCLUDE_ENV", "false")
    out = backup._create_backup()
    with zipfile.ZipFile(out) as zf:
        names = zf.namelist()
    assert ".env" not in names
    # 다른 대상은 여전히 포함
    assert any("publish_queue.json" in n for n in names)


def test_rotation_keeps_recent_deletes_old(isolated_project, monkeypatch):
    root, backup_dir = isolated_project
    backup_dir.mkdir(exist_ok=True)

    today = date.today()
    # 오늘 / 13일 전 / 30일 전 — keep_days=14
    for d in (today, today - timedelta(days=13), today - timedelta(days=30)):
        (backup_dir / f"{d.isoformat()}.zip").write_text("x")

    monkeypatch.setenv("BACKUP_KEEP_DAYS", "14")
    deleted = backup._rotate(14)
    assert deleted == 1  # 30일 전 1개만 삭제

    remaining = sorted(p.name for p in backup_dir.iterdir())
    assert f"{today.isoformat()}.zip" in remaining
    assert f"{(today - timedelta(days=13)).isoformat()}.zip" in remaining
    assert f"{(today - timedelta(days=30)).isoformat()}.zip" not in remaining


def test_rotation_ignores_non_dated_files(isolated_project, monkeypatch):
    root, backup_dir = isolated_project
    backup_dir.mkdir(exist_ok=True)
    # 패턴 안 맞는 파일들 — 건드리지 않아야 함
    (backup_dir / "notes.txt").write_text("x")
    (backup_dir / "manual-backup.zip").write_text("x")

    backup._rotate(14)
    assert (backup_dir / "notes.txt").exists()
    assert (backup_dir / "manual-backup.zip").exists()


def test_list_backups(isolated_project):
    root, backup_dir = isolated_project
    backup_dir.mkdir(exist_ok=True)
    (backup_dir / "2026-05-01.zip").write_bytes(b"x" * 1024)
    (backup_dir / "2026-05-02.zip").write_bytes(b"x" * 2048)

    rows = backup.list_backups()
    assert len(rows) == 2
    assert rows[0]["name"] == "2026-05-01.zip"
    assert rows[1]["name"] == "2026-05-02.zip"
