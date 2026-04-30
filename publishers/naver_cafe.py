"""
네이버 카페 자동 발행 Publisher
- RSA 로그인 + 세션 쿠키
- 글쓰기, 댓글, 좋아요

참조: 01.Platform_Naver/naver_cafe/네이버카페_쿠팡파트너스/naver_cafe_basic_이미지글_댓글_배포용_ver6.py
"""
import os
import time
import random
from typing import Optional

import requests
from requests_toolbelt import MultipartEncoder

from common.auth import naver_login, naver_login_cdp
from common.image import download as download_image, cleanup as cleanup_image, get_suffix
from common.logger import log
from common.session import SessionManager
from .base import Publisher, PostResult


CAFE_API_BASE = "https://apis.naver.com/cafe-web/cafe-editor-api"


def _build_content_json(html_content: str) -> str:
    """네이버 카페 SE 에디터용 contentJson 생성 (간단한 텍스트 문단 형태)."""
    import json
    import uuid

    def _id(prefix: str = "SE-") -> str:
        return f"{prefix}{uuid.uuid4()}"

    # HTML → 단순 텍스트 문단 (태그 단순 제거). 추후 이미지/링크 컴포넌트 확장 가능.
    import re
    text = re.sub(r"<br\s*/?>", "\n", html_content)
    text = re.sub(r"</p>", "\n", text)
    text = re.sub(r"<[^>]+>", "", text)
    lines = [ln.strip() for ln in text.splitlines()]
    lines = [ln for ln in lines if ln]

    components = []
    for line in lines:
        components.append({
            "id": _id(),
            "layout": "default",
            "value": [{
                "id": _id(),
                "nodes": [{"id": _id(), "value": line, "@ctype": "textNode"}],
                "@ctype": "paragraph",
            }],
            "@ctype": "text",
        })
    if not components:
        components.append({
            "id": _id(),
            "layout": "default",
            "value": [{
                "id": _id(),
                "nodes": [{"id": _id(), "value": "", "@ctype": "textNode"}],
                "@ctype": "paragraph",
            }],
            "@ctype": "text",
        })

    document = {
        "document": {
            "version": "2.8.0",
            "theme": "default",
            "language": "ko-KR",
            "id": _id("01"),
            "components": components,
            "di": {"dif": False, "dio": [
                {"dis": "N", "dia": {"t": 0, "p": 0, "st": 1, "sk": 0}},
                {"dis": "N", "dia": {"t": 0, "p": 0, "st": 27, "sk": 6}},
            ]},
        },
        "documentId": "",
    }
    return json.dumps(document, ensure_ascii=False)


class NaverCafePublisher(Publisher):
    """네이버 카페 발행기."""

    def __init__(self, cafe_id: str, username: str, password: str):
        """
        Args:
            cafe_id:  네이버 카페 URL ID (예: 'mycafe')
            username: 네이버 아이디
            password: 네이버 비밀번호
        """
        self.cafe_id     = cafe_id
        self.username    = username
        self.password    = password
        self.session_mgr = SessionManager(f"naver_cafe_{cafe_id}")
        self._cafe_no: Optional[str] = None

    def login(self) -> bool:
        """네이버 로그인. 저장 세션 → CDP(Chrome 프로필) → RSA 순서."""
        if self.session_mgr.load():
            if self._is_logged_in():
                self._fetch_cafe_no()
                return True
            log("저장된 세션 만료, 재로그인 시도", "warn")
            self.session_mgr.delete()

        ok = naver_login_cdp(self.session_mgr.session)
        if ok:
            self.session_mgr.save()
            self._fetch_cafe_no()
            return True

        log("[Naver Cafe] CDP 실패, RSA 로그인 시도", "warn")
        ok = naver_login(self.session_mgr.session, self.username, self.password)
        if ok:
            self.session_mgr.save()
            self._fetch_cafe_no()
            return True

        log("[Naver Cafe] 로그인 실패 — Chrome 프로필에 네이버 로그인 필요", "error")
        return False

    def _is_logged_in(self) -> bool:
        """현재 쿠키로 로그인 상태인지 확인.

        네이버가 GNB 디자인을 변경하면서 '로그아웃' 텍스트가 사라져 단일
        문자열 검사로는 살아있는 세션도 '만료' 로 오판한다. 여러 마커
        후보 + NID_AUT 쿠키 존재 + 카페 페이지 응답을 종합 판단.
        """
        # 1) NID_AUT 쿠키 자체가 없으면 로그아웃 확정
        cookie_names = {c.name for c in self.session_mgr.session.cookies}
        if "NID_AUT" not in cookie_names:
            return False

        # 2) 네이버 메인의 로그인 marker (GNB 변경에도 살아남을 후보 다중)
        try:
            resp = self.session_mgr.get("https://www.naver.com", timeout=5)
            markers = [
                "로그아웃", "MY 영역", "gnb_my", "nid_my",
                'class="MyView',           # 신규 GNB
                'data-clk="nlu',            # 로그인 사용자 전용 컴포넌트
            ]
            if any(m in resp.text for m in markers):
                return True
        except Exception:
            pass

        # 3) 카페 페이지 자체 검증 — m.cafe.naver.com 은 비로그인 시 nid 로그인으로 리다이렉트
        try:
            resp = self.session_mgr.get(
                f"https://cafe.naver.com/{self.cafe_id}", timeout=5,
                allow_redirects=False,
            )
            # 200 + redirect 없음 = 로그인 상태 정상
            if resp.status_code == 200 and "/nidlogin" not in (resp.headers.get("location") or ""):
                return True
        except Exception:
            pass
        return False

    def _fetch_cafe_no(self) -> None:
        """카페 URL에서 카페 번호(cafeNo) 추출."""
        import re
        url  = f"https://cafe.naver.com/{self.cafe_id}"
        resp = self.session_mgr.get(url, timeout=10)
        # 여러 패턴 시도
        for pattern in [
            r'"cafeNo"\s*:\s*"?(\d+)',
            r'clubid=(\d+)',
            r'/cafes/(\d+)',
            r'cafeId["\s:]+(\d+)',
        ]:
            m = re.search(pattern, resp.text)
            if m:
                self._cafe_no = m.group(1)
                log(f"카페 번호: {self._cafe_no}", "ok")
                return
        # 패턴 실패 시 환경변수에서 직접 읽기
        import os
        fallback = os.getenv("NAVER_CAFE_CLUB_ID", "")
        if fallback:
            self._cafe_no = fallback
            log(f"카페 번호 (env fallback): {self._cafe_no}", "ok")

    @property
    def cafe_no(self) -> str:
        if not self._cafe_no:
            self._fetch_cafe_no()
        return self._cafe_no or ""

    # ─── 카테고리(게시판) ─────────────────────────────────────────────────────

    def get_categories(self) -> list[dict]:
        """카페 게시판 목록 반환."""
        url  = f"https://apis.naver.com/cafe-web/cafe2/CafeMainMenu.json"
        resp = self.session_mgr.get(url, params={"cafeId": self.cafe_no}, timeout=5)
        try:
            menus = resp.json().get("message", {}).get("result", {}).get("menus", [])
            return [{"id": m.get("menuId"), "name": m.get("menuName")} for m in menus
                    if m.get("menuType") == "A"]  # A = 일반 게시판
        except Exception:
            return []

    def get_category_id(self, name: str) -> Optional[str]:
        for cat in self.get_categories():
            if cat.get("name") == name:
                return str(cat.get("id", ""))
        return None

    # ─── SmartEditor v2 이미지 업로드 (cafe.upphoto 4-step) ───────────────────

    def upload_image_se(self, local_path: str, *, menu_id: str = "1") -> Optional[dict]:
        """네이버 카페 SmartEditor v2 이미지 업로드. 4-step:

        1) cafe-editor-api/v2/cafes/{cafeNo}/editor (token 획득)
        2) platform.editor.naver.com/.../session-key (sessionKey 획득)
        3) cafe.upphoto.naver.com/{sessionKey}/simpleUpload/0 (실제 업로드)
        4) XML 응답 파싱 → {url, path, fileName, fileSize, width, height}

        Old_Source naver_cafe/...adpick_ver6.py 의 흐름 이식.
        """
        import re

        try:
            # Step 1: editor token
            r1 = self.session_mgr.get(
                f"https://apis.naver.com/cafe-web/cafe-editor-api/v2/cafes/{self.cafe_no}/editor",
                params={"experienceMode": "true", "menuId": menu_id, "from": "pc"},
                headers={
                    "accept": "application/json, text/plain, */*",
                    "origin": "https://cafe.naver.com",
                    "referer": (
                        f"https://cafe.naver.com/ca-fe/cafes/{self.cafe_no}"
                        f"/menus/{menu_id}/articles/write?boardType=L"
                    ),
                    "x-cafe-product": "pc",
                },
                timeout=10,
            )
            if not r1.ok:
                log(f"[cafe upload] editor token 실패 ({r1.status_code}): {r1.text[:200]}", "error")
                return None
            token = r1.json().get("result", {}).get("token", "")
            if not token:
                log(f"[cafe upload] editor token 응답 비어있음: {r1.text[:200]}", "error")
                return None
            time.sleep(1)

            # Step 2: session-key
            r2 = self.session_mgr.get(
                "https://platform.editor.naver.com/api/cafepc001/v1/photo-uploader/session-key",
                headers={
                    "accept": "application/json",
                    "origin": "https://cafe.naver.com",
                    "referer": (
                        f"https://cafe.naver.com/ca-fe/cafes/{self.cafe_no}"
                        f"/menus/{menu_id}/articles/write?boardType=L"
                    ),
                    "se-app-id": "SE-b88be568-1657-40a4-9cae-aad5c6db647d",
                    "se-authorization": token,
                },
                timeout=10,
            )
            if not r2.ok:
                log(f"[cafe upload] session-key 실패 ({r2.status_code}): {r2.text[:200]}", "error")
                return None
            session_key = r2.json().get("sessionKey", "")
            if not session_key:
                log(f"[cafe upload] session-key 비어있음: {r2.text[:200]}", "error")
                return None
            time.sleep(1)

            # Step 3: simpleUpload
            ext  = os.path.splitext(local_path)[1].lstrip(".").lower() or "png"
            mime = "image/jpeg" if ext == "jpg" else f"image/{ext}"
            naver_id = self.username or os.getenv("NAVER_USERNAME", "")
            with open(local_path, "rb") as f:
                files = {"image": (os.path.basename(local_path), f, mime)}
                r3 = self.session_mgr.session.post(
                    f"https://cafe.upphoto.naver.com/{session_key}/simpleUpload/0",
                    params={
                        "userId": naver_id,
                        "extractExif": "true",
                        "extractAnimatedCnt": "true",
                        "autorotate": "true",
                        "extractDominantColor": "false",
                        "type": "",
                        "customQuery": "",
                        "denyAnimatedImage": "false",
                        "skipXcamFiltering": "false",
                    },
                    headers={
                        "accept": "*/*",
                        "origin": "https://cafe.naver.com",
                        "referer": (
                            f"https://cafe.naver.com/ca-fe/cafes/{self.cafe_no}"
                            f"/menus/{menu_id}/articles/write?boardType=L"
                        ),
                    },
                    files=files,
                    timeout=30,
                )
            if not r3.ok:
                log(f"[cafe upload] simpleUpload 실패 ({r3.status_code}): {r3.text[:200]}", "error")
                return None

            # Step 4: XML 응답 파싱
            text = r3.text
            def _xml_field(name: str, src: str = text) -> str:
                m = re.search(rf"<{name}>(.*?)</{name}>", src, re.DOTALL)
                return m.group(1).strip() if m else ""

            # cafe.upphoto 응답 XML 의 <url> 태그가 곧 SmartEditor imageNode 의
            # 'path' 역할 (예: '/MjAyNi8wNC8yNi8x...PNG'). Old_Source ver6 에서도
            # image_url = upload_info['url'] 한 변수를 src/path 둘 다에 사용.
            path     = _xml_field("url")  # ← 핵심: <url> 이 path 역할
            filename = _xml_field("fileName") or os.path.basename(local_path)
            filesize = _xml_field("fileSize") or "100000"
            width    = _xml_field("width") or "600"
            height   = _xml_field("height") or "600"

            if not path:
                log(f"[cafe upload] XML 파싱 실패: {text[:300]}", "error")
                return None

            # src 는 cafeptthumb-phinf 도메인 + path 조합
            src = f"https://cafeptthumb-phinf.pstatic.net{path}?type=w1600"
            log(f"[cafe upload] OK — {path}", "ok")
            return {
                "url":      path,    # 호환성용 별칭 (path 와 동일)
                "src":      src,
                "path":     path,
                "filename": filename,
                "filesize": int(filesize) if str(filesize).isdigit() else 100000,
                "width":    int(width)    if str(width).isdigit()    else 600,
                "height":   int(height)   if str(height).isdigit()   else 600,
            }
        except Exception as e:
            log(f"[cafe upload] 예외: {e}", "error")
            return None

    # ─── document JSON 직접 발행 ──────────────────────────────────────────────

    def post_with_document(
        self, *, title: str, content_json: str,
        menu_id: str, tags: Optional[list[str]] = None,
    ) -> PostResult:
        """SmartEditor v2 document JSON 문자열을 그대로 카페에 발행.

        cafe_smarteditor.build_*_document() 결과를 받아 발행한다. articleId
        를 응답에서 받은 뒤 본문에 '%ARTICLE_ID%' placeholder 가 있으면 실제
        ID 로 치환해 update 하는 후속 처리는 호출자 책임.
        """
        log(f"카페 발행 (SE document): {title}", "step")
        url = (
            f"https://apis.naver.com/cafe-web/cafe-editor-api/v2.0/cafes/"
            f"{self.cafe_no}/menus/{menu_id}/articles"
        )
        payload = {
            "article": {
                "cafeId":         str(self.cafe_no),
                "contentJson":    content_json,
                "from":           "pc",
                "menuId":         str(menu_id),
                "subject":        title,
                "tagList":        list(tags or []),
                "editorVersion":  4,
                "parentId":       0,
                "open":           True,
                "naverOpen":      True,
                "externalOpen":   True,
                "enableComment":  True,
                "enableScrap":    True,
                "enableCopy":     True,
                "useAutoSource":  False,
                "cclTypes":       [],
                "useCcl":         False,
            }
        }
        headers = {
            "accept": "application/json, text/plain, */*",
            "content-type": "application/json",
            "origin": "https://cafe.naver.com",
            "referer": (
                f"https://cafe.naver.com/ca-fe/cafes/{self.cafe_no}"
                f"/menus/{menu_id}/articles/write"
            ),
            "x-cafe-product": "pc",
        }
        resp = self.session_mgr.post(url, json=payload, headers=headers)
        log(f"카페 응답 [{resp.status_code}]: {resp.text[:300]}", "info")
        if resp.status_code in (200, 201):
            try:
                data = resp.json()
            except Exception:
                data = {}
            if not isinstance(data, dict):
                data = {}
            result = data.get("result") or data.get("message", {}).get("result") or {}
            if not isinstance(result, dict):
                result = {}
            art_id = str(result.get("articleId") or result.get("id") or "")
            if art_id:
                post_url = f"https://cafe.naver.com/{self.cafe_id}/{art_id}"
                log(f"카페 발행 성공: {post_url}", "ok")
                return PostResult(success=True, url=post_url, post_id=art_id)
        log(f"카페 발행 실패 ({resp.status_code}): {resp.text[:200]}", "error")
        return PostResult(success=False, message=resp.text[:200])

    def update_article(
        self, *, article_id: str, title: str, content_json: str,
        menu_id: str, tags: Optional[list[str]] = None,
    ) -> bool:
        """발행 후 articleId 가 들어간 content 로 글 수정.

        Old_Source ver6 와 동일하게 **POST** (PUT 아님) 로 호출하고
        'headId: 0' 등 신규 필드 포함. PUT 이면 500 에러 반환됨.
        """
        url = (
            f"https://apis.naver.com/cafe-web/cafe-editor-api/v2.0/cafes/"
            f"{self.cafe_no}/articles/{article_id}"
        )
        payload = {
            "article": {
                "cafeId":         str(self.cafe_no),
                "contentJson":    content_json,
                "from":           "pc",
                "headId":         0,
                "menuId":         str(menu_id),
                "subject":        title,
                "tagList":        list(tags or []),
                "editorVersion":  4,
                "parentId":       0,
                "open":           True,
                "naverOpen":      True,
                "externalOpen":   True,
                "enableComment":  True,
                "enableScrap":    True,
                "enableCopy":     True,
                "useAutoSource":  False,
                "cclTypes":       [],
                "useCcl":         False,
            }
        }
        try:
            resp = self.session_mgr.session.post(
                url, json=payload,
                headers={
                    "accept": "application/json, text/plain, */*",
                    "content-type": "application/json",
                    "origin": "https://cafe.naver.com",
                    "referer": (
                        f"https://cafe.naver.com/ca-fe/cafes/{self.cafe_no}"
                        f"/articles/{article_id}/modify"
                    ),
                    "x-cafe-product": "pc",
                },
                timeout=15,
            )
            ok = resp.status_code in (200, 201)
            if ok:
                log(f"카페 글 업데이트 완료 (articleId: {article_id})", "ok")
            else:
                log(f"카페 글 업데이트 실패 ({resp.status_code}): {resp.text[:200]}", "warn")
            return ok
        except Exception as e:
            log(f"카페 글 업데이트 예외: {e}", "warn")
            return False

    # ─── 이미지 업로드 (구버전 — 단순 URL 반환) ───────────────────────────────

    def upload_image(self, local_path: str) -> str:
        """네이버 카페 이미지 업로드 (구식, ArticleImageUpload.nhn)."""
        url  = f"https://cafe.naver.com/ArticleImageUpload.nhn"
        ext  = os.path.splitext(local_path)[1].lstrip(".")
        mime = f"image/{ext}" if ext != "jpg" else "image/jpeg"
        with open(local_path, "rb") as f:
            encoder = MultipartEncoder(fields={
                "cafeId": self.cafe_no,
                "image":  (os.path.basename(local_path), f, mime),
            })
            resp = self.session_mgr.post(
                url,
                data=encoder,
                headers={"Content-Type": encoder.content_type},
            )
        try:
            data = resp.json()
            img_url = data.get("imageUrl", "")
            log(f"카페 이미지 업로드: {img_url}", "ok")
            return img_url
        except Exception as e:
            log(f"카페 이미지 업로드 실패: {e}", "error")
            return ""

    # ─── 포스트 발행 ──────────────────────────────────────────────────────────

    def post(self, title: str, content: str,
             tags: list[str] = None, category: str = "",
             image_url: str = "", **kwargs) -> PostResult:
        """네이버 카페 글 발행.

        kwargs:
            menu_id: 게시판 ID (직접 지정 시 category 무시)
            open_type: 0=전체공개
        """
        log(f"네이버 카페 발행: {title}", "step")

        # 게시판 ID
        menu_id = str(kwargs.get("menu_id", ""))
        if not menu_id and category:
            menu_id = self.get_category_id(category) or ""

        # 이미지
        img_url_str = ""
        if image_url:
            suffix   = get_suffix(image_url)
            tmp_path = download_image(image_url, suffix)
            try:
                img_url_str = self.upload_image(tmp_path)
            finally:
                cleanup_image(tmp_path)

        if img_url_str:
            content = f'<img src="{img_url_str}" style="max-width:100%"><br>{content}'

        content_json = _build_content_json(content)
        payload = {
            "article": {
                "cafeId":         str(self.cafe_no),
                "contentJson":    content_json,
                "from":           "pc",
                "menuId":         str(menu_id),
                "subject":        title,
                "tagList":        list(tags or []),
                "editorVersion":  4,
                "parentId":       0,
                "open":           True,
                "naverOpen":      True,
                "externalOpen":   True,
                "enableComment":  True,
                "enableScrap":    True,
                "enableCopy":     True,
                "useAutoSource":  False,
                "cclTypes":       [],
                "useCcl":         False,
            }
        }
        headers = {
            "accept": "application/json, text/plain, */*",
            "content-type": "application/json",
            "origin": "https://cafe.naver.com",
            "referer": f"https://cafe.naver.com/ca-fe/cafes/{self.cafe_no}/menus/{menu_id}/articles/write",
            "x-cafe-product": "pc",
            "user-agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        }
        url  = f"https://apis.naver.com/cafe-web/cafe-editor-api/v2.0/cafes/{self.cafe_no}/menus/{menu_id}/articles"
        resp = self.session_mgr.post(url, json=payload, headers=headers)

        log(f"카페 응답 [{resp.status_code}]: {resp.text[:500]}", "info")

        if resp.status_code in (200, 201):
            try:
                data = resp.json()
            except Exception:
                data = {}
            if not isinstance(data, dict):
                data = {}
            message = data.get("message") if isinstance(data.get("message"), dict) else {}
            result  = data.get("result") or message.get("result") or {}
            if not isinstance(result, dict):
                result = {}
            art_id = str(result.get("articleId") or result.get("id") or data.get("articleId") or "")
            status = message.get("status") or data.get("code") or ""
            ok_flag = (str(status) in ("200", "0", "SUCCESS", "success") or art_id)
            if art_id or ok_flag:
                post_url = f"https://cafe.naver.com/{self.cafe_id}/{art_id}" if art_id else ""
                log(f"카페 발행 성공: {post_url}", "ok")
                return PostResult(success=True, url=post_url, post_id=art_id)
            log(f"카페 발행 응답 200이나 articleId 없음: {str(data)[:300]}", "error")
            return PostResult(success=False, message=f"empty result: {str(data)[:200]}")
        log(f"카페 발행 실패 ({resp.status_code}): {resp.text[:200]}", "error")
        return PostResult(success=False, message=resp.text[:200])

    # ─── 좋아요 ───────────────────────────────────────────────────────────────

    def like_article(self, article_id: str) -> bool:
        """카페 글에 좋아요."""
        url  = f"https://apis.naver.com/cafe-web/cafe-article-api/v1.0/cafes/{self.cafe_no}/articles/{article_id}/sympathy"
        resp = self.session_mgr.post(url, json={})
        ok   = resp.status_code in (200, 201)
        if ok:
            log(f"좋아요 완료: {article_id}", "ok")
        return ok

    # ─── 댓글 ─────────────────────────────────────────────────────────────────

    def post_comment(self, article_id: str, comment: str,
                     *, sticker_id: Optional[str] = None) -> bool:
        """카페 글에 댓글 작성.

        엔드포인트: cafe-mobile/CommentPost.json — Old_Source ver6 패턴.
        application/x-www-form-urlencoded 로 cafeId/articleId/content/stickerId/
        requestFrom 전송. 응답 commentId 가 있으면 성공으로 판정.

        sticker_id 지정 시 댓글에 카페 기본 스티커 첨부 ('cafe_012-1-185-160' 등).
        """
        url = "https://apis.naver.com/cafe-web/cafe-mobile/CommentPost.json"
        data = {
            "content":     comment,
            "cafeId":      str(self.cafe_no),
            "articleId":   str(article_id),
            "requestFrom": "A",
        }
        if sticker_id:
            data["stickerId"] = sticker_id

        headers = {
            "accept": "application/json, text/plain, */*",
            "content-type": "application/x-www-form-urlencoded",
            "origin": "https://cafe.naver.com",
            "referer": (
                f"https://cafe.naver.com/ca-fe/cafes/{self.cafe_no}"
                f"/articles/{article_id}"
            ),
            "x-cafe-product": "pc",
        }
        try:
            resp = self.session_mgr.session.post(
                url, data=data, headers=headers, timeout=15,
            )
        except Exception as e:
            log(f"댓글 작성 예외: {e}", "error")
            return False

        if not resp.ok:
            log(f"댓글 작성 실패 ({resp.status_code}): {resp.text[:200]}", "error")
            return False

        try:
            payload = resp.json()
        except Exception:
            payload = {}

        # CommentPost.json 응답 형태: {"message": {...}, "result": {"commentId": ...}}
        # 또는 평탄한 {"commentId": ...}
        comment_id = (
            payload.get("commentId")
            or payload.get("result", {}).get("commentId")
            or payload.get("message", {}).get("result", {}).get("commentId")
        )
        if comment_id:
            log(f"댓글 작성 완료 (commentId: {comment_id})", "ok")
            return True

        log(f"댓글 응답 200 이나 commentId 없음: {resp.text[:300]}", "warn")
        return False
