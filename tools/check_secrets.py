"""
시크릿 누출 정기 점검 스크립트.

검사 항목:
  1. .gitignore 에 핵심 시크릿 패턴이 모두 포함되어 있는지
  2. git ls-files 결과에 시크릿/세션 파일이 추적되고 있지 않은지
  3. .env 와 .env.example 의 키 집합 동기화 — .env.example 에만 있는 키
     (env 에 추가해야 할 키) 또는 .env 에만 있는 키 (example 동기화 필요)
  4. .env 값에 예시 placeholder ("your_...", "xxx") 가 남아있는지

실행:
    python tools/check_secrets.py

리턴 코드:
    0 = 깨끗함
    1 = 경고 (운영 권장사항 불일치)
    2 = 위험 (실제 시크릿 누출 가능)
"""
import re
import subprocess
import sys
from pathlib import Path

# Windows 콘솔 cp949 에서 이모지 출력 시 UnicodeEncodeError 방지
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):
    pass

_BASE_DIR = Path(__file__).resolve().parent.parent

_REQUIRED_GITIGNORE = (
    ".env",
    ".sessions/",
    "data/",
    ".runtime/",
    "backups/",
)

_SECRET_FILE_PATTERNS = (
    re.compile(r"(^|/)\.env(\.|$)"),       # .env, .env.local — but not .env.example
    re.compile(r"(^|/)\.sessions(/|$)"),
    re.compile(r"\.session$"),
    re.compile(r"_token\.json$"),
    re.compile(r"credentials\.json$"),
    re.compile(r"(^|/)\.secrets(/|$)"),
)

_PLACEHOLDER_RE = re.compile(r"^(your_|xxx|<.*>|placeholder)", re.IGNORECASE)


# ── 검사 함수 ─────────────────────────────────────────────────────────

def check_gitignore() -> list[str]:
    """필수 패턴이 빠진 게 있으면 경고 리스트 반환."""
    gi = _BASE_DIR / ".gitignore"
    if not gi.exists():
        return [".gitignore 파일 자체가 없음 — 즉시 생성 필요"]
    text = gi.read_text(encoding="utf-8")
    missing = []
    for p in _REQUIRED_GITIGNORE:
        # 줄 단위 정확히 일치 검사
        if not re.search(rf"(?m)^{re.escape(p)}\s*(#.*)?$", text):
            missing.append(p)
    if missing:
        return [f".gitignore 에 누락: {missing}"]
    return []


def check_tracked_secrets() -> list[str]:
    """git ls-files 에 시크릿 파일이 추적되고 있는지."""
    try:
        out = subprocess.run(
            ["git", "ls-files"], cwd=_BASE_DIR,
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=15,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        return [f"git 실행 실패: {e}"]
    if out.returncode != 0:
        return [f"git ls-files 실패: {out.stderr[:200]}"]

    leaks = []
    for line in out.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        # .env.example 은 안전 — 예외
        if line.endswith(".env.example"):
            continue
        for pat in _SECRET_FILE_PATTERNS:
            if pat.search(line):
                leaks.append(line)
                break
    return leaks


def _parse_env(path: Path) -> dict:
    """.env 파일에서 키=값 추출. 주석/빈 줄 무시."""
    result = {}
    if not path.exists():
        return result
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        k, _, v = line.partition("=")
        result[k.strip()] = v.strip().strip("'\"")
    return result


def check_env_sync() -> tuple[list[str], list[str]]:
    """.env 와 .env.example 키 동기화 검사.

    Returns:
        (only_in_example, only_in_env)
        only_in_example: .env 에 채워야 할 새 키
        only_in_env:     .env.example 에 등록해야 할 키 (문서화 누락)
    """
    env_path = _BASE_DIR / ".env"
    eg_path  = _BASE_DIR / ".env.example"

    env_keys = set(_parse_env(env_path).keys())
    eg_keys  = set(_parse_env(eg_path).keys())

    only_in_example = sorted(eg_keys - env_keys)
    only_in_env     = sorted(env_keys - eg_keys)
    return only_in_example, only_in_env


def check_env_placeholders() -> list[str]:
    """.env 값에 예시 placeholder 가 남아있는지."""
    env = _parse_env(_BASE_DIR / ".env")
    leaks = []
    for k, v in env.items():
        if v and _PLACEHOLDER_RE.match(v):
            leaks.append(f"{k}={v[:40]}")
    return leaks


# ── 실행 ──────────────────────────────────────────────────────────────

def main() -> int:
    print("=" * 60)
    print(" 시크릿 누출 점검")
    print("=" * 60)

    danger = 0
    warn = 0

    # 1. .gitignore
    print("\n[1/4] .gitignore 필수 패턴 ...", end=" ")
    rows = check_gitignore()
    if rows:
        print("⚠️")
        for r in rows:
            print(f"  • {r}")
        warn += 1
    else:
        print("✅")

    # 2. git tracked secrets
    print("[2/4] git tracked 시크릿 파일 ...", end=" ")
    rows = check_tracked_secrets()
    if rows:
        print("🚨 위험")
        for r in rows:
            print(f"  • {r}")
        print("  → git rm --cached <파일> 로 추적 해제 후 commit 필요")
        danger += 1
    else:
        print("✅")

    # 3. .env 동기화
    print("[3/4] .env ↔ .env.example 키 동기화 ...", end=" ")
    missing_in_env, missing_in_example = check_env_sync()
    if missing_in_env or missing_in_example:
        print("⚠️")
        if missing_in_env:
            print(f"  • .env 에 추가 필요 ({len(missing_in_env)}개): "
                  f"{', '.join(missing_in_env[:5])}"
                  f"{'...' if len(missing_in_env) > 5 else ''}")
        if missing_in_example:
            print(f"  • .env.example 에 등록 필요 ({len(missing_in_example)}개): "
                  f"{', '.join(missing_in_example[:5])}"
                  f"{'...' if len(missing_in_example) > 5 else ''}")
        warn += 1
    else:
        print("✅")

    # 4. placeholder 값
    print("[4/4] .env 값 placeholder ...", end=" ")
    rows = check_env_placeholders()
    if rows:
        print("⚠️")
        for r in rows:
            print(f"  • {r}")
        warn += 1
    else:
        print("✅")

    print("\n" + "=" * 60)
    if danger:
        print(f"🚨 위험 {danger}건 — 즉시 조치 필요")
        return 2
    if warn:
        print(f"⚠️  경고 {warn}건 — 운영 권장사항 확인")
        return 1
    print("✅ 깨끗함")
    return 0


if __name__ == "__main__":
    sys.exit(main())
