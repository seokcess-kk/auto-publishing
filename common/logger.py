"""
공통 컬러 콘솔 로거
- 기존 스크립트들의 ANSI 컬러 출력 패턴을 통합
- get_logger(name) 팩토리로 named logger 사용 가능 (Python logging 모듈 기반)
- log(msg, level) 는 하위 호환 wrapper 로 유지
"""
import logging
import sys
from datetime import datetime

# ANSI 컬러 코드
C_RESET  = "\033[0m"
C_BOLD   = "\033[1m"
C_RED    = "\033[91m"
C_GREEN  = "\033[92m"
C_YELLOW = "\033[93m"
C_BLUE   = "\033[94m"
C_CYAN   = "\033[96m"
C_GRAY   = "\033[90m"

# logging 레벨 매핑
_LEVEL_MAP: dict[str, int] = {
    "info":    logging.INFO,
    "ok":      logging.INFO,
    "success": logging.INFO,
    "warn":    logging.WARNING,
    "warning": logging.WARNING,
    "error":   logging.ERROR,
    "step":    logging.DEBUG,
}


class _ColorFormatter(logging.Formatter):
    """ANSI 컬러를 적용하는 logging.Formatter."""

    _FMT: dict[int, str] = {
        logging.DEBUG:   f"{C_CYAN}{C_BOLD}>> %(message)s{C_RESET}",
        logging.INFO:    f"{C_GRAY}[%(asctime)s]{C_RESET} {C_BLUE}[INFO]{C_RESET}  %(message)s",
        logging.WARNING: f"{C_GRAY}[%(asctime)s]{C_RESET} {C_YELLOW}[WARN]{C_RESET}  %(message)s",
        logging.ERROR:   f"{C_GRAY}[%(asctime)s]{C_RESET} {C_RED}[ERROR]{C_RESET} %(message)s",
    }

    def format(self, record: logging.LogRecord) -> str:
        fmt = self._FMT.get(record.levelno, "%(message)s")
        formatter = logging.Formatter(fmt, datefmt="%H:%M:%S")
        return formatter.format(record)


def get_logger(name: str) -> logging.Logger:
    """named logger 반환. 중복 핸들러 없이 컬러 콘솔 출력."""
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    logger.setLevel(logging.DEBUG)
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(_ColorFormatter())
    logger.addHandler(handler)
    logger.propagate = False
    return logger


def _now() -> str:
    return datetime.now().strftime("%H:%M:%S")


def log(msg: str, level: str = "info") -> None:
    """레벨별 컬러 콘솔 출력. (하위 호환 wrapper)"""
    ts = f"{C_GRAY}[{_now()}]{C_RESET}"
    lv = level.lower()
    if lv == "info":
        print(f"{ts} {C_BLUE}[INFO]{C_RESET}  {msg}")
    elif lv in ("ok", "success"):
        print(f"{ts} {C_GREEN}[OK]{C_RESET}    {msg}")
    elif lv in ("warn", "warning"):
        print(f"{ts} {C_YELLOW}[WARN]{C_RESET}  {msg}")
    elif lv == "error":
        print(f"{ts} {C_RED}[ERROR]{C_RESET} {msg}", file=sys.stderr)
    elif lv == "step":
        print(f"{ts} {C_CYAN}{C_BOLD}>> {msg}{C_RESET}")
    else:
        print(f"{ts} {msg}")
