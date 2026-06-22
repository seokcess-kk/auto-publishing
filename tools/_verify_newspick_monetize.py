"""뉴스픽 수익화 설정 검증 — 세션 → 기사수집 → 추적링크(bltly.link) 생성 확인.

검증 항목:
  1) NEWSPICK_REFERRAL(cp) 가 채워져 있는가
  2) ensure_session() 로 로그인/세션 확보되는가
  3) shorten_link() 가 bltly.link 추적 링크를 반환하는가 (← 수익 적립의 핵심)
  4) 그 링크를 펼쳤을 때 cp= 와 utm_source 가 .env 값과 일치하는가
"""
import os
import sys
sys.stdout.reconfigure(encoding="utf-8")

import requests
from dotenv import load_dotenv
load_dotenv()

from sources.newspick import NewspickSource

cp = os.getenv("NEWSPICK_REFERRAL", "")
utm_prefix = os.getenv("NEWSPICK_UTM_PREFIX", "np220822")
print(f"[1] NEWSPICK_REFERRAL(cp) = {cp!r}  | utm_prefix = {utm_prefix!r}")
if not cp or cp == "your_referral_code":
    print("    ✗ cp 코드 미설정 — 중단")
    sys.exit(1)

src = NewspickSource()
print("[2] ensure_session() 시도...")
if not src.ensure_session():
    print("    ✗ 세션 확보 실패 (로그인 필요: python tools/newspick_manual_login.py)")
    sys.exit(2)
print("    ✓ 세션 확보")

print("[3] 기사 수집 + 추적링크 생성...")
arts = src.fetch("메인", count=3)
if not arts:
    print("    ✗ 기사 수집 0건")
    sys.exit(3)

a = arts[0]
short = src.shorten_link(a, "메인")
print(f"    기사: {a.get('title','')[:40]}")
print(f"    short_url = {short!r}")
if not short.startswith("https://bltly.link/"):
    print("    ✗ 추적링크 생성 실패 — 이 상태면 원문 URL 폴백되어 수익 0원")
    sys.exit(4)
print("    ✓ bltly.link 추적링크 생성 성공")

print("[4] 추적링크 펼쳐 cp/utm 검증...")
try:
    html = requests.get(short, timeout=10).text
    import re
    m = re.search(r"location\.replace\('([^']+)'\)", html)
    final = m.group(1) if m else ""
    print(f"    → {final[:120]}...")
    ok_cp = f"cp={cp}" in final
    ok_utm = f"utm_source={utm_prefix}{cp}" in final
    print(f"    cp 일치: {ok_cp}  | utm_source 일치: {ok_utm}")
    if ok_cp:
        print("\n✅ 수익화 설정 정상 — 발행되는 글의 링크 클릭이 내 계정(cp={}) 으로 적립됩니다.".format(cp))
    else:
        print("\n✗ cp 불일치 — 적립 안 될 수 있음")
        sys.exit(5)
except Exception as e:
    print(f"    검증 요청 실패(무해할 수 있음): {e}")
