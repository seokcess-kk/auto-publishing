"""tokenId 추출 패턴 진단 (임시) — token 관련 키워드 광범위 탐색."""
import os, re, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from dotenv import load_dotenv
load_dotenv()

from publishers.naver_blog import NaverBlogPublisher

blog = NaverBlogPublisher(
    os.getenv("NAVER_BLOG_ID"),
    os.getenv("NAVER_USERNAME"),
    os.getenv("NAVER_PASSWORD"),
)
if not blog.login():
    print("login failed"); sys.exit(1)

sess = blog.session_mgr
for path in ("/postwrite", "/manage/newpost"):
    url = f"https://blog.naver.com/{blog.blog_id}{path}"
    r = sess.get(url, headers={"user-agent": "Mozilla/5.0"})
    print(f"=== {url}  status={r.status_code}  len={len(r.text)} ===")
    # 광범위 키워드 검색
    for kw in ("tokenId", "token", "csrf", "_token", "_csrf", "nonce",
                "secretKey", "editorSource", "naverToken"):
        if kw in r.text:
            idx = r.text.find(kw)
            print(f"  '{kw}' 위치 {idx}:")
            print(f"    ...{r.text[max(0,idx-30):idx+150]}...")
    # 모든 script src 출력 (별도 token 발급 endpoint 추정용)
    print("  외부 script src:")
    for m in re.finditer(r'<script[^>]+src="([^"]+)"', r.text):
        print(f"    {m.group(1)[:120]}")
    print()
