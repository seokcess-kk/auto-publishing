"""
.env 인라인 주석 보정 유틸.

배경:
    .env / .env.example 의 컨벤션은
        KEY=                      # 설명
    형태로 빈 값에도 인라인 주석을 단다. python-dotenv 1.x 는 값이 비었을 때
    `# 설명` 부분을 통째로 값으로 읽어들여, `if os.getenv(KEY)` 같은 truthy 검사를
    잘못 통과시키고 ('# 설명' 이 그대로 블로그 ID/카테고리 등으로 흘러간다).

대응:
    값이 `#` 으로 시작하면 인라인 주석 누수로 간주하고 빈 문자열로 정정한다.
    common 패키지 import 시 한 번만 실행 — 순서상 모든 pipeline 이
    `load_dotenv()` 다음에 `from common.X import Y` 를 호출하므로 안전하다.
"""
import os


def cleanup_env_inline_comments() -> int:
    """`#` 프리픽스 값을 빈 문자열로 정정. 정정한 항목 수 반환."""
    fixed = 0
    for key, val in list(os.environ.items()):
        if val.strip().startswith("#"):
            os.environ[key] = ""
            fixed += 1
    return fixed


def getenv_clean(name: str, default: str = "") -> str:
    """os.getenv + 인라인 주석 누수 방어. 신규 코드는 이쪽 권장."""
    val = os.getenv(name, default).strip()
    if val.startswith("#"):
        return default
    return val
