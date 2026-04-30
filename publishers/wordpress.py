"""
WordPress 자동 발행 Publisher
- REST API (Application Password 기반 Basic Auth)
- XML-RPC 지원

참조: 03.Platform_Blog/wordpress/basic_wordpress_sample.py
     03.Platform_Blog/wordpress/wordpress(xmlrpc)_coopang(api)_자동발행/
"""
import os
from typing import Optional

import requests

from common.auth import wp_basic_auth_header, wp_jwt_auth_header
from common.image import download as download_image, cleanup as cleanup_image, get_suffix
from common.logger import log
from .base import Publisher, PostResult


class WordPressPublisher(Publisher):
    """WordPress REST API 발행기."""

    def __init__(self, site_url: str, username: str = "",
                 app_password: str = "", jwt_token: str = ""):
        """
        Args:
            site_url:     WordPress 사이트 URL (예: 'https://example.com')
            username:     WordPress 사용자명 (Basic Auth용)
            app_password: WordPress Application Password (Basic Auth용)
            jwt_token:    JWT 토큰 (Bearer Auth용, app_password 대신 사용 가능)
        """
        self.site_url    = site_url.rstrip("/")
        self.api_base    = f"{self.site_url}/wp-json/wp/v2"
        if jwt_token:
            self.auth_header = wp_jwt_auth_header(jwt_token)
        else:
            self.auth_header = wp_basic_auth_header(username, app_password)
        self.session     = requests.Session()
        self.session.headers.update(self.auth_header)

    def login(self) -> bool:
        """인증 확인 (REST API → /users/me)."""
        try:
            resp = self.session.get(f"{self.api_base}/users/me", timeout=5)
            ok   = resp.status_code == 200
            if ok:
                log(f"WordPress 인증 성공: {self.site_url}", "ok")
            else:
                log(f"WordPress 인증 실패: {resp.status_code}", "error")
            return ok
        except Exception as e:
            log(f"WordPress 연결 실패: {e}", "error")
            return False

    # ─── 카테고리 / 태그 ───────────────────────────────────────────────────────

    def get_categories(self) -> list[dict]:
        """카테고리 목록 반환."""
        try:
            resp = self.session.get(f"{self.api_base}/categories", params={"per_page": 100})
            return resp.json() if resp.ok else []
        except Exception:
            return []

    def get_category_id(self, name: str) -> Optional[int]:
        """카테고리명으로 ID 반환. 없으면 None."""
        for cat in self.get_categories():
            if cat.get("name") == name or cat.get("slug") == name:
                return cat["id"]
        return None

    def get_or_create_tag(self, tag_name: str) -> int:
        """태그명으로 ID 반환 (없으면 생성)."""
        resp = self.session.get(
            f"{self.api_base}/tags",
            params={"search": tag_name, "per_page": 5},
        )
        if resp.ok:
            for t in resp.json():
                if t.get("name") == tag_name:
                    return t["id"]

        # 없으면 생성
        resp2 = self.session.post(f"{self.api_base}/tags", json={"name": tag_name})
        if resp2.ok:
            return resp2.json()["id"]
        return 0

    # ─── 이미지 업로드 ────────────────────────────────────────────────────────

    def upload_image(self, local_path: str) -> str:
        """WordPress 미디어 라이브러리에 이미지 업로드 후 URL 반환."""
        ext  = os.path.splitext(local_path)[1].lstrip(".")
        mime = f"image/{ext}" if ext not in ("jpg",) else "image/jpeg"
        headers = {
            **self.auth_header,
            "Content-Type":        mime,
            "Content-Disposition": f'attachment; filename="{os.path.basename(local_path)}"',
        }
        with open(local_path, "rb") as f:
            resp = requests.post(f"{self.api_base}/media", data=f, headers=headers)
        if resp.ok:
            url = resp.json().get("source_url", "")
            log(f"WordPress 이미지 업로드: {url}", "ok")
            return url
        log(f"WordPress 이미지 업로드 실패: {resp.status_code}", "error")
        return ""

    # ─── 포스트 발행 ──────────────────────────────────────────────────────────

    def post(self, title: str, content: str,
             tags: list[str] = None, category: str = "",
             image_url: str = "", **kwargs) -> PostResult:
        """WordPress 포스트 발행.

        kwargs:
            status: 'publish' | 'draft' | 'private' (기본: 'publish')
            excerpt: 발췌문
            slug:   슬러그
        """
        log(f"WordPress 발행: {title}", "step")

        # 카테고리
        cat_ids = []
        if category:
            cid = self.get_category_id(category)
            if cid:
                cat_ids = [cid]

        # 태그
        tag_ids = []
        for tag in (tags or []):
            tid = self.get_or_create_tag(tag)
            if tid:
                tag_ids.append(tid)

        # 대표 이미지
        featured_media = 0
        if image_url:
            suffix   = get_suffix(image_url)
            tmp_path = download_image(image_url, suffix)
            try:
                img_url = self.upload_image(tmp_path)
                # 미디어 ID 조회
                resp = self.session.get(f"{self.api_base}/media",
                                        params={"search": os.path.basename(tmp_path)})
                if resp.ok and resp.json():
                    featured_media = resp.json()[0].get("id", 0)
            finally:
                cleanup_image(tmp_path)

        payload = {
            "title":          title,
            "content":        content,
            "status":         kwargs.get("status", "publish"),
            "categories":     cat_ids,
            "tags":           tag_ids,
            "featured_media": featured_media,
            "excerpt":        kwargs.get("excerpt", ""),
            "slug":           kwargs.get("slug", ""),
        }

        resp = self.session.post(f"{self.api_base}/posts", json=payload)
        if resp.status_code == 201:
            data    = resp.json()
            post_url = data.get("link", "")
            post_id  = str(data.get("id", ""))
            log(f"WordPress 발행 성공: {post_url}", "ok")
            return PostResult(success=True, url=post_url, post_id=post_id)
        else:
            msg = resp.text[:200]
            log(f"WordPress 발행 실패 ({resp.status_code}): {msg}", "error")
            return PostResult(success=False, message=msg)

    def post_with_ids(self, title: str, content: str,
                      category_id: int, tag_id: int,
                      excerpt: str = "", slug: str = "",
                      status: str = "publish") -> PostResult:
        """이미 알고 있는 category_id/tag_id 로 직접 POST (조회 없음)."""
        log(f"WordPress 발행: {title}", "step")
        payload = {
            "status":     status,
            "slug":       slug,
            "title":      title,
            "content":    content,
            "categories": [category_id] if category_id else [],
            "tags":       [tag_id] if tag_id else [],
            "excerpt":    excerpt,
        }
        resp = self.session.post(f"{self.api_base}/posts", json=payload, timeout=30)
        if resp.status_code == 201:
            data     = resp.json()
            post_url = data.get("link", "")
            post_id  = str(data.get("id", ""))
            log(f"WordPress 발행 성공: {post_url}", "ok")
            return PostResult(success=True, url=post_url, post_id=post_id)
        msg = resp.text[:200]
        log(f"WordPress 발행 실패 ({resp.status_code}): {msg}", "error")
        return PostResult(success=False, message=msg)


# ─── XML-RPC 발행기 (대안) ───────────────────────────────────────────────────

class WordPressXmlRpcPublisher(Publisher):
    """WordPress XML-RPC 발행기 (python-wordpress-xmlrpc 사용)."""

    def __init__(self, site_url: str, username: str, password: str):
        self.site_url = site_url.rstrip("/")
        self.username = username
        self.password = password
        self._client  = None

    def _get_client(self):
        if self._client is None:
            try:
                from wordpress_xmlrpc import Client
            except ImportError:
                raise ImportError("python-wordpress-xmlrpc 패키지 필요: pip install python-wordpress-xmlrpc")
            self._client = Client(
                f"{self.site_url}/xmlrpc.php",
                self.username,
                self.password,
            )
        return self._client

    def login(self) -> bool:
        try:
            self._get_client()
            log(f"WordPress XML-RPC 연결: {self.site_url}", "ok")
            return True
        except Exception as e:
            log(f"WordPress XML-RPC 연결 실패: {e}", "error")
            return False

    def upload_image(self, local_path: str) -> str:
        from wordpress_xmlrpc.methods import media
        import mimetypes
        client = self._get_client()
        mime, _ = mimetypes.guess_type(local_path)
        with open(local_path, "rb") as f:
            data = {
                "name":   os.path.basename(local_path),
                "type":   mime or "image/jpeg",
                "bits":   f.read(),
                "overwrite": False,
            }
        result = client.call(media.UploadFile(data))
        return result.get("url", "")

    def post(self, title: str, content: str,
             tags: list[str] = None, category: str = "",
             image_url: str = "", **kwargs) -> PostResult:
        from wordpress_xmlrpc import WordPressPost
        from wordpress_xmlrpc.methods.posts import NewPost

        log(f"WordPress XML-RPC 발행: {title}", "step")
        client = self._get_client()

        wp_post = WordPressPost()
        wp_post.title   = title
        wp_post.content = content
        wp_post.post_status = kwargs.get("status", "publish")
        if tags:
            wp_post.terms_names = {"post_tag": tags}
        if category:
            wp_post.terms_names = {**getattr(wp_post, "terms_names", {}),
                                    "category": [category]}

        post_id = client.call(NewPost(wp_post))
        post_url = f"{self.site_url}/?p={post_id}"
        log(f"XML-RPC 발행 성공: {post_url}", "ok")
        return PostResult(success=True, url=post_url, post_id=str(post_id))

    def get_categories(self) -> list[dict]:
        from wordpress_xmlrpc.methods import taxonomies
        client = self._get_client()
        cats = client.call(taxonomies.GetTerms("category"))
        return [{"id": c.id, "name": c.name, "slug": c.slug} for c in cats]
