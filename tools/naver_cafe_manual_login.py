"""
네이버 카페 수동 로그인 → 세션 쿠키 저장.

사용법 (프로젝트 루트에서):
    .venv/bin/python -m tools.naver_cafe_manual_login

브라우저(headless=False)가 열리면 네이버에 직접 로그인하세요. 도구가
NID_AUT/NID_SES 쿠키 등장을 자동으로 감지해 .sessions/naver_cafe_<CAFE_ID>.pkl
에 저장합니다 (Enter 입력 불필요). 카페 publisher 가 즉시 사용 가능.

저장 형식: {name: value} dict (SessionManager.load() 호환)
"""
from __future__ import annotations

import os
import pickle
import sys
import time
from pathlib import Path

from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

from common.logger import log  # noqa: E402


CAFE_ID    = os.getenv("NAVER_CAFE_ID", "")
SESSION_DIR = ROOT / ".sessions"
SESSION_PATH = SESSION_DIR / f"naver_cafe_{CAFE_ID}.pkl"


def collect_and_save() -> int:
    if not CAFE_ID:
        log(".env 의 NAVER_CAFE_ID 가 비어 있음", "error")
        return 1

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        log("playwright 미설치", "error")
        return 1

    SESSION_DIR.mkdir(exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=False,
            args=["--disable-blink-features=AutomationControlled"],
        )
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            locale="ko-KR",
        )
        page = context.new_page()

        log("브라우저가 열립니다 — 네이버에 로그인하세요 (NID_AUT 쿠키 등장 자동 감지)", "step")
        log(f"  로그인 후 자동으로 cafe.naver.com/{CAFE_ID} 방문해 카페 쿠키 수집", "info")
        page.goto(
            "https://nid.naver.com/nidlogin.login?mode=form&url=https://www.naver.com",
            wait_until="domcontentloaded", timeout=30000,
        )

        # 로그인 완료 감지 — NID_AUT + NID_SES 쿠키 등장 폴링 (최대 5분)
        deadline = time.time() + 300
        last_url = ""
        tick = 0
        logged_in = False
        while time.time() < deadline:
            tick += 1
            try:
                cur = page.url
            except Exception:
                cur = last_url
            if cur != last_url:
                log(f"  URL → {cur}", "info")
                last_url = cur

            cookies = context.cookies(["https://www.naver.com", "https://nid.naver.com"])
            names = {c["name"] for c in cookies}
            if "NID_AUT" in names and "NID_SES" in names:
                log("  NID_AUT/NID_SES 감지 — 카페 페이지 방문 후 저장", "ok")
                logged_in = True
                break

            if tick % 5 == 0:
                log(f"  [폴링 {tick}s] naver 쿠키 {len(names)}개", "info")
            time.sleep(1)

        if not logged_in:
            log("로그인 감지 시간 초과 (5분)", "error")
            browser.close()
            return 2

        # 카페 페이지 방문해 카페 도메인 쿠키도 확보
        try:
            page.goto(
                f"https://cafe.naver.com/{CAFE_ID}",
                wait_until="domcontentloaded", timeout=15000,
            )
            time.sleep(2)
        except Exception:
            pass

        cookies = context.cookies([
            "https://www.naver.com",
            "https://nid.naver.com",
            "https://cafe.naver.com",
            "https://m.cafe.naver.com",
        ])
        browser.close()

    cookie_names = sorted({c["name"] for c in cookies})
    log(f"최종 수집 쿠키 {len(cookies)}개 (이름: {cookie_names})", "info")

    if "NID_AUT" not in cookie_names or "NID_SES" not in cookie_names:
        log("⚠️  최종 쿠키에 NID_AUT/NID_SES 없음 — 로그인 미완료", "warn")
        return 3

    cookie_dict = {c["name"]: c["value"] for c in cookies}
    with open(SESSION_PATH, "wb") as f:
        pickle.dump(cookie_dict, f)
    log(f"세션 저장 완료: {SESSION_PATH}", "ok")

    # 즉시 검증
    log("NaverCafePublisher 로그인 검증", "step")
    from publishers.naver_cafe import NaverCafePublisher
    pub = NaverCafePublisher(
        CAFE_ID,
        os.getenv("NAVER_USERNAME", ""),
        os.getenv("NAVER_PASSWORD", ""),
    )
    if pub.login():
        log("✅ 카페 세션 유효 — 파이프라인에서 바로 사용 가능", "ok")
        return 0
    log("❌ 세션 검증 실패", "error")
    return 1


if __name__ == "__main__":
    sys.exit(collect_and_save())
