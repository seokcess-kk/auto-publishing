"""영속 프로파일(.sessions/aliexpress_login_profile)에 이미 로그인된 세션을
data/aliexpress_storage.json 으로 저장한다. portals 제휴 접근을 1회 확인하고,
확인되면 storage_state 를 저장한다.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
try:
    from dotenv import load_dotenv
    load_dotenv(REPO / ".env")
except Exception:
    pass

from playwright.sync_api import sync_playwright

PROFILE = REPO / ".sessions" / "aliexpress_login_profile"
STORAGE = REPO / "data" / "aliexpress_storage.json"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
TRACK = os.getenv("ALIEXPRESS_TRACKING_ID", "wordpress")


def _portals_ok(ctx) -> bool:
    url = ("https://portals.aliexpress.com/tools/linkGenerate/generatePromotionLink.htm"
           f"?trackId={TRACK}&targetUrl=https%3A%2F%2Fwww.aliexpress.com")
    try:
        res = ctx.request.get(url, headers={
            "accept": "application/json, text/plain, */*",
            "referer": "https://portals.aliexpress.com/affiportals/web/link_generator.htm",
            "user-agent": UA,
        }, timeout=15000)
        if not res.ok:
            return False
        return res.text().strip().startswith("{")
    except Exception:
        return False


with sync_playwright() as p:
    ctx = p.chromium.launch_persistent_context(
        user_data_dir=str(PROFILE),
        headless=True,
        user_agent=UA,
        locale="ko-KR",
        args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
    )
    # 락 안정화 후 portals 접근 확인 (wind-control 대비 3회 재시도)
    ok = False
    for i in range(3):
        if _portals_ok(ctx):
            ok = True
            break
        print(f"  portals 확인 재시도 {i+1}/3...")
        time.sleep(4)

    if not ok:
        print("✗ portals 제휴 접근 미확인 — 저장하지 않음")
        ctx.close()
        sys.exit(1)

    STORAGE.parent.mkdir(parents=True, exist_ok=True)
    ctx.storage_state(path=str(STORAGE))
    ctx.close()

print(f"✓ 세션 저장 완료: {STORAGE}")
print("portals 제휴 접근 확인됨 — 진짜 제휴 로그인 세션입니다.")
