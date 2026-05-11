"""pytest 공통 설정.

- 프로젝트 루트를 sys.path 에 추가 → `from common.* import` 동작 보장
- .env 자동 로드는 차단 (테스트는 격리된 임시 환경에서)
"""
import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

# 테스트 중 실수로 .env 가 로드되는 것 방지
os.environ.setdefault("DOTENV_PATH_DISABLED", "1")
