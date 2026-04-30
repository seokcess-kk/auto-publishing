"""
네이버 블로그 자동 발행 Publisher
- requests + RabbitWrite.naver API (Old Source 방식)
- SE 에디터 JSON documentModel 직접 조립 (table, text, link 등 네이티브 컴포넌트)

발행 흐름:
  1. 저장 세션 / CDP / RSA 로그인
  2. documentModel + populationParams 조립
  3. RabbitWrite.naver POST → 발행 완료
  4. (선택) 댓글로 쿠팡 링크 작성
"""
import json
import os
import re
import time
import uuid
from urllib import parse

from common.auth import naver_login_cdp, naver_login
from common.logger import log
from common.session import SessionManager
from .base import Publisher, PostResult


_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)


def _se_uuid() -> str:
    return f"SE-{uuid.uuid4()}"


# ─── SE 에디터 컴포넌트 빌더 ──────────────────────────────────────────────

def _styled_node(text: str, bold=False, color="#000000", size="fs15",
                 bg="#ffffff", link_url="") -> dict:
    """스타일이 적용된 textNode."""
    node = {
        "id": _se_uuid(),
        "value": text,
        "style": {
            "fontColor": color,
            "fontFamily": "system",
            "fontSizeCode": size,
            "backgroundColor": bg,
            "bold": bold,
            "italic": False,
            "@ctype": "nodeStyle",
        },
        "@ctype": "textNode",
    }
    if link_url:
        node["link"] = {"url": link_url, "@ctype": "urlLink"}
    return node


def _paragraph(nodes: list[dict], align="left") -> dict:
    """SE 에디터 paragraph."""
    p = {
        "id": _se_uuid(),
        "nodes": nodes,
        "@ctype": "paragraph",
    }
    if align != "left":
        p["style"] = {"align": align, "lineHeight": 1.6, "@ctype": "paragraphStyle"}
    return p


def _text_component(paragraphs: list[dict]) -> dict:
    """SE 에디터 text 컴포넌트."""
    return {
        "id": _se_uuid(),
        "layout": "default",
        "value": paragraphs,
        "@ctype": "text",
    }


def _table_cell(paragraphs: list[dict], bg="#ffffff", width=100, height=43,
                border="border-top:none;border-right:1px solid rgb(210,210,210);"
                       "border-left:none;border-bottom:1px solid rgb(210,210,210);") -> dict:
    """SE 에디터 tableCell."""
    return {
        "id": _se_uuid(),
        "borderInlineStyle": border,
        "colSpan": 1, "rowSpan": 1,
        "width": width, "height": height,
        "backgroundColor": bg,
        "value": paragraphs,
        "@ctype": "tableCell",
    }


def _table_row(cells: list[dict]) -> dict:
    return {"cells": cells, "@ctype": "tableRow"}


def _table_component(rows: list[dict], col_count=1, width=38) -> dict:
    """SE 에디터 table 컴포넌트."""
    return {
        "id": _se_uuid(),
        "layout": "default",
        "align": "center",
        "width": width,
        "rows": rows,
        "columnCount": col_count,
        "borderInlineStyle": (
            "border-top:1px solid rgb(210,210,210);"
            "border-right:none;border-left:1px solid rgb(210,210,210);"
            "border-bottom:none;border-collapse:separate;"
        ),
        "@ctype": "table",
    }


def _empty_line() -> dict:
    """빈 줄 text 컴포넌트."""
    return _text_component([_paragraph([_styled_node("")])])


# ─── 일출일몰 전용 documentModel 빌더 ─────────────────────────────────────

def build_riseset_document(title: str, intro: str,
                           info_list: list[dict],
                           product: dict = None,
                           blog_id: str = "",
                           comment_url: str = "") -> dict:
    """일출일몰 포스트용 SE documentModel 조립.

    Args:
        title: 포스트 제목
        intro: AI 생성 도입부 텍스트
        info_list: 지역별 일출일몰 정보 [{location, sunrise, sunset, moonrise, moonset, ...}]
        product: 쿠팡 상품 dict {name, price, image, affiliate_url, ...}
        blog_id: 블로그 ID
    """
    components = []

    # ── 1. 제목
    components.append({
        "id": _se_uuid(),
        "layout": "default",
        "title": [_paragraph([_styled_node(title)])],
        "subTitle": None,
        "align": "left",
        "@ctype": "documentTitle",
    })

    # ── 2. 도입부
    if intro:
        intro_paragraphs = []
        for line in intro.split("\n"):
            line = line.strip()
            if line:
                intro_paragraphs.append(
                    _paragraph([_styled_node(line, color="#555555", size="fs15")])
                )
        if intro_paragraphs:
            components.append(_text_component(intro_paragraphs))
            components.append(_empty_line())

    # ── 3. 대표 시각 요약 (서울 기준)
    if info_list:
        main = info_list[0]
        summary_nodes = [
            _styled_node("🌅 일출 ", bold=True, color="#FF8F00", size="fs16"),
            _styled_node(main.get("sunrise", ""), bold=True, color="#FF8F00", size="fs34"),
            _styled_node("   🌇 일몰 ", bold=True, color="#EF6C00", size="fs16"),
            _styled_node(main.get("sunset", ""), bold=True, color="#EF6C00", size="fs34"),
        ]
        components.append(_text_component([
            _paragraph(summary_nodes, align="center")
        ]))

        moon_nodes = [
            _styled_node("🌕 월출 ", color="#5C6BC0", size="fs15"),
            _styled_node(main.get("moonrise", ""), bold=True, color="#5C6BC0", size="fs18"),
            _styled_node("   🌑 월몰 ", color="#37474F", size="fs15"),
            _styled_node(main.get("moonset", ""), bold=True, color="#37474F", size="fs18"),
        ]
        components.append(_text_component([
            _paragraph(moon_nodes, align="center")
        ]))
        components.append(_empty_line())

    # ── 4. 지역별 테이블
    if info_list:
        # 컬럼 너비 비율: 지역(20%) + 일출(20%) + 일몰(20%) + 월출(20%) + 월몰(20%)
        col_width = 20

        # 헤더 행
        header_cells = []
        for h in ["지역", "일출", "일몰", "월출", "월몰"]:
            header_cells.append(_table_cell(
                [_paragraph([_styled_node(h, bold=True, color="#333333", size="fs13")], align="center")],
                bg="#FFF3E0", width=col_width,
            ))
        rows = [_table_row(header_cells)]

        # 데이터 행
        colors = {"sunrise": "#FF8F00", "sunset": "#EF6C00",
                  "moonrise": "#5C6BC0", "moonset": "#37474F"}
        for info in info_list:
            data_cells = [
                _table_cell([_paragraph([_styled_node(
                    info["location"], bold=True, size="fs13")], align="center")],
                    width=col_width),
            ]
            for key, col in colors.items():
                data_cells.append(_table_cell([_paragraph([_styled_node(
                    info.get(key, ""), color=col, size="fs13")], align="center")],
                    width=col_width))
            rows.append(_table_row(data_cells))

        components.append(_table_component(rows, col_count=5, width=97))

        # 출처
        components.append(_text_component([
            _paragraph([_styled_node(
                "출처: 한국천문연구원 (data.go.kr)", color="#999999", size="fs11")], align="right")
        ]))
        components.append(_empty_line())

    # ── 5. 쿠팡 상품 카드 (테이블)
    if product:
        name = product.get("name", "")
        price = product.get("price", "")
        aff_url = product.get("affiliate_url", "")
        review = product.get("review_count", "")
        discount = product.get("discount_rate", "")

        # 상품 정보 행
        info_paragraphs = [
            _paragraph([_styled_node("📦 오늘의 추천 상품", bold=True, color="#333333", size="fs11")],
                       align="center"),
            _paragraph([_styled_node("")]),
            _paragraph([_styled_node(name, bold=True, color="#333333", size="fs15")], align="center"),
            _paragraph([_styled_node("")]),
        ]

        # 가격
        price_nodes = []
        if discount:
            price_nodes.append(_styled_node(f"{discount} ", color="#999999", size="fs13"))
        if price:
            price_nodes.append(_styled_node(f"￦ {price}", bold=True, color="#e4000f", size="fs34"))
        if price_nodes:
            info_paragraphs.append(_paragraph(price_nodes, align="center"))

        # 리뷰
        if review and review != "0":
            info_paragraphs.append(
                _paragraph([_styled_node(f"리뷰 {review}개", color="#888888", size="fs13")],
                           align="center"))

        info_paragraphs.append(_paragraph([_styled_node("")]))

        # 구매 안내 (댓글 링크)
        info_paragraphs.append(_paragraph([
            _styled_node("▼ ▼ ", color="#DB4455", size="fs15"),
            _styled_node("구매는 댓글 확인", bold=True, color="#333333", size="fs15",
                         link_url=comment_url if comment_url else ""),
            _styled_node(" ▼ ▼", color="#DB4455", size="fs15"),
        ], align="center"))

        product_row = _table_row([_table_cell(info_paragraphs)])
        components.append(_table_component([product_row], col_count=1, width=38))
        components.append(_empty_line())

    # ── 6. 파트너스 고지
    components.append(_text_component([
        _paragraph([_styled_node(
            "※ 쿠팡 파트너스 활동을 통해 일정액의 수수료를 제공받을 수 있습니다.",
            color="#999999", size="fs11")], align="center")
    ]))

    doc = {
        "documentId": "",
        "document": {
            "version": "2.8.0",
            "theme": "default",
            "language": "ko-KR",
            "id": str(uuid.uuid4()).replace("-", "").upper()[:26],
            "components": components,
            "di": {
                "dif": False,
                "dio": [
                    {"dis": "N", "dia": {"t": 0, "p": 0, "st": 94, "sk": 40}},
                    {"dis": "N", "dia": {"t": 0, "p": 0, "st": 94, "sk": 40}},
                ],
            },
        },
    }
    return doc


# ─── Publisher 클래스 ──────────────────────────────────────────────────────

class NaverBlogPublisher(Publisher):
    """requests + RabbitWrite.naver API 기반 네이버 블로그 발행기."""

    def __init__(self, blog_id: str, username: str, password: str):
        self.blog_id  = blog_id
        self.username = username
        self.password = password
        self.session_mgr = SessionManager(f"naver_blog_{blog_id}")

    # ─── 로그인 ────────────────────────────────────────────────────────────

    def login(self) -> bool:
        """네이버 로그인. 저장 세션 → CDP → RSA 순서."""
        if self.session_mgr.load():
            log("[Naver Blog] 저장된 세션 로드 완료", "ok")
            return True

        ok = naver_login_cdp(self.session_mgr.session)
        if ok:
            self.session_mgr.save()
            return True

        log("[Naver Blog] CDP 실패, RSA 로그인 시도", "warn")
        ok = naver_login(self.session_mgr.session, self.username, self.password)
        if ok:
            self.session_mgr.save()
            return True

        log("[Naver Blog] 로그인 실패 — tools/naver_manual_login.py 실행 필요", "error")
        return False

    # ─── documentModel 조립 ────────────────────────────────────────────────

    def _build_document_model(self, title: str, content: str, **kwargs) -> str:
        """SE 에디터 documentModel JSON을 조립.

        kwargs에 riseset_data/product가 있으면 구조화된 레이아웃,
        없으면 단순 텍스트 레이아웃.
        """
        riseset_data = kwargs.get("riseset_data")
        product = kwargs.get("product")
        intro = kwargs.get("intro", "")
        comment_url = kwargs.get("comment_url", "")

        if riseset_data:
            doc = build_riseset_document(
                title, intro, riseset_data, product, self.blog_id,
                comment_url=comment_url)
            return json.dumps(doc, ensure_ascii=False)

        # 기본: 단순 텍스트 모드
        plain = re.sub(r"<[^>]+>", "", content)
        lines = [l.strip() for l in plain.split("\n") if l.strip()]
        if not lines:
            lines = [plain[:2000] if plain else "본문"]

        paragraphs = [_paragraph([_styled_node(line)]) for line in lines]

        doc = {
            "documentId": "",
            "document": {
                "version": "2.8.0",
                "theme": "default",
                "language": "ko-KR",
                "id": str(uuid.uuid4()).replace("-", "").upper()[:26],
                "components": [
                    {
                        "id": _se_uuid(),
                        "layout": "default",
                        "title": [_paragraph([_styled_node(title)])],
                        "subTitle": None,
                        "align": "left",
                        "@ctype": "documentTitle",
                    },
                    _text_component(paragraphs),
                ],
                "di": {
                    "dif": False,
                    "dio": [
                        {"dis": "N", "dia": {"t": 0, "p": 0, "st": 94, "sk": 40}},
                        {"dis": "N", "dia": {"t": 0, "p": 0, "st": 94, "sk": 40}},
                    ],
                },
            },
        }
        return json.dumps(doc, ensure_ascii=False)

    def _build_population_params(self, category_no: int, tags: list[str]) -> str:
        params = {
            "configuration": {
                "openType": 2,
                "commentYn": True,
                "searchYn": True,
                "sympathyYn": True,
                "scrapType": 2,
                "outSideAllowYn": True,
                "twitterPostingYn": False,
                "facebookPostingYn": False,
                "cclYn": False,
            },
            "populationMeta": {
                "categoryId": category_no,
                "logNo": None,
                "directorySeq": 21,
                "directoryDetail": None,
                "mrBlogTalkCode": None,
                "postWriteTimeType": "now",
                "tags": ",".join(tags) if tags else "",
                "moviePanelParticipation": False,
                "greenReviewBannerYn": False,
                "continueSaved": True,
                "noticePostYn": False,
                "autoByCategoryYn": True,
                "postLocationSupportYn": False,
                "postLocationJson": None,
                "prePostDate": None,
                "thisDayPostInfo": None,
                "scrapYn": False,
                "autoSaveNo": None,
            },
            "editorSource": "XQdkruFJsAUjbhDZppTiRA==",
        }
        return json.dumps(params, ensure_ascii=False)

    # ─── RabbitWrite 발행 ──────────────────────────────────────────────────

    def _rabbit_write(self, title: str, content: str,
                      tags: list[str], category_no: int, **kwargs) -> PostResult:
        headers = {
            "authority": "blog.naver.com",
            "accept": "application/json, text/plain, */*",
            "content-type": "application/x-www-form-urlencoded",
            "origin": "https://blog.naver.com",
            "referer": f"https://blog.naver.com/{self.blog_id}/postwrite",
            "user-agent": _USER_AGENT,
        }

        document_model = self._build_document_model(title, content, **kwargs)
        population_params = self._build_population_params(category_no, tags)

        data = {
            "blogId": self.blog_id,
            "documentModel": document_model,
            "populationParams": population_params,
            "productApiVersion": "v1",
        }

        resp = self.session_mgr.post(
            "https://blog.naver.com/RabbitWrite.naver",
            headers=headers, data=data,
        )

        if resp.ok:
            try:
                log_no = ""
                result_json = resp.json()

                # isSuccess 필드로 API 성공 여부 먼저 판단. False 면 errorCode/errorMessage 로 실패 반환.
                api_success = result_json.get("isSuccess")
                if api_success is False:
                    err = result_json.get("result", {}) or {}
                    err_code = err.get("errorCode", "")
                    err_msg  = err.get("errorMessage", "")
                    log(f"RabbitWrite 실패: isSuccess=false code={err_code} msg={err_msg}", "error")
                    return PostResult(
                        success=False,
                        message=f"isSuccess=false code={err_code} msg={err_msg} body={resp.text[:200]}",
                    )

                redirect_url = result_json.get("result", {}).get("redirectUrl", "")
                if redirect_url:
                    m = re.search(r"logNo=(\d+)", redirect_url)
                    if m:
                        log_no = m.group(1)
                if not log_no:
                    m = re.search(r'"logNo"\s*:\s*"?(\d+)', resp.text)
                    if m:
                        log_no = m.group(1)
                    else:
                        m = re.search(r"logNo=(\d+)", resp.text)
                        if m:
                            log_no = m.group(1)

                if log_no:
                    post_url = f"https://blog.naver.com/{self.blog_id}/{log_no}"
                    log(f"네이버 블로그 발행 성공: {post_url}", "ok")
                    return PostResult(success=True, url=post_url, post_id=log_no)

                # isSuccess=true 인데 logNo 만 못 찾은 경우 — 응답 포맷 변경 가능성. 실패로 보고.
                log(f"발행 응답 수신 (isSuccess=true) 이나 logNo 추출 실패: {resp.text[:300]}", "warn")
                return PostResult(
                    success=False, url="", post_id="",
                    message=f"logNo 추출 실패 (isSuccess={api_success}) body={resp.text[:200]}",
                )
            except Exception as e:
                log(f"RabbitWrite 응답 파싱 오류: {e}", "error")
                return PostResult(success=False, message=str(e))

        log(f"RabbitWrite 실패: {resp.status_code} {resp.text[:300]}", "error")
        return PostResult(success=False,
                          message=f"HTTP {resp.status_code}: {resp.text[:200]}")

    # ─── RabbitUpdate 수정 ──────────────────────────────────────────────

    def _rabbit_update(self, log_no: str, title: str, content: str,
                       tags: list[str], category_no: int, **kwargs) -> bool:
        """발행된 글의 본문을 수정한다 (RabbitUpdate.naver)."""
        headers = {
            "authority": "blog.naver.com",
            "accept": "application/json, text/plain, */*",
            "content-type": "application/x-www-form-urlencoded",
            "origin": "https://blog.naver.com",
            "referer": f"https://blog.naver.com/{self.blog_id}/postwrite",
            "user-agent": _USER_AGENT,
        }

        document_model_str = self._build_document_model(title, content, **kwargs)
        # documentId에 logNo 설정 (Update 시 필수)
        doc_obj = json.loads(document_model_str)
        doc_obj["documentId"] = log_no
        document_model = json.dumps(doc_obj, ensure_ascii=False)

        population_params = self._build_population_params(category_no, tags)
        # populationMeta.logNo에도 설정
        pop_obj = json.loads(population_params)
        pop_obj["populationMeta"]["logNo"] = int(log_no)
        population_params = json.dumps(pop_obj, ensure_ascii=False)

        data = {
            "blogId": self.blog_id,
            "documentModel": document_model,
            "populationParams": population_params,
            "productApiVersion": "v1",
        }

        resp = self.session_mgr.post(
            "https://blog.naver.com/RabbitUpdate.naver",
            headers=headers, data=data,
        )

        if resp.ok:
            log(f"[Naver Blog] 글 업데이트 완료 (logNo: {log_no})", "ok")
            return True

        log(f"[Naver Blog] RabbitUpdate 실패: {resp.status_code} {resp.text[:200]}", "error")
        return False

    # ─── 포스트 발행 (메인) ────────────────────────────────────────────────

    def post(self, title: str, content: str,
             tags: list[str] = None, category: str = "",
             image_url: str = "", **kwargs) -> PostResult:
        """네이버 블로그 글 발행.

        발행 후 상품 정보가 있으면 댓글 링크가 포함된 본문으로 자동 업데이트.

        kwargs:
            category_no:   카테고리 번호 (int)
            riseset_data:  일출일몰 정보 리스트 (구조화 레이아웃용)
            product:       쿠팡 상품 dict (구조화 레이아웃용)
            intro:         AI 도입부 텍스트
        """
        log(f"네이버 블로그 발행 시작: {title}", "step")
        category_no = kwargs.pop("category_no", 0)
        tags = tags or []

        # 1차 발행 (댓글 링크 없이)
        result = self._rabbit_write(title, content, tags, category_no, **kwargs)

        # 발행 성공 + 상품 있으면 → 댓글 URL 포함하여 본문 업데이트
        if result.success and result.post_id and kwargs.get("riseset_data") and kwargs.get("product"):
            comment_url = (
                f"https://m.blog.naver.com/CommentList.naver"
                f"?blogId={self.blog_id}&logNo={result.post_id}"
            )
            kwargs["comment_url"] = comment_url
            time.sleep(1)
            self._rabbit_update(
                result.post_id, title, content, tags, category_no, **kwargs)

        return result

    # ─── userNo 조회 ─────────────────────────────────────────────────────

    def _get_user_number(self) -> str:
        """블로그 페이지에서 userNo를 추출."""
        resp = self.session_mgr.get(
            f"https://blog.naver.com/PostList.naver?blogId={self.blog_id}",
            headers={"User-Agent": _USER_AGENT})
        if not resp.ok:
            log("[Naver Blog] PostList 접근 실패", "error")
            return ""
        # iframe mainFrame 주소 추출
        m = re.search(r'src="(/PostList\.naver\?[^"]+)"', resp.text)
        if not m:
            # userNo가 직접 있는 경우
            m2 = re.search(r"userNo\s*=\s*'(\d+)'", resp.text)
            return m2.group(1) if m2 else ""

        iframe_url = f"https://blog.naver.com{m.group(1)}"
        iframe_resp = self.session_mgr.get(
            iframe_url, headers={"User-Agent": _USER_AGENT})
        m2 = re.search(r"userNo\s*=\s*'(\d+)'", iframe_resp.text)
        return m2.group(1) if m2 else ""

    # ─── 댓글 작성 ────────────────────────────────────────────────────────

    def post_comment(self, post_no: str, comment: str) -> bool:
        """발행된 글에 댓글을 작성한다. (cbox API)"""
        user_no = self._get_user_number()
        if not user_no:
            log("[Naver Blog] userNo 추출 실패 — 댓글 작성 불가", "error")
            return False

        object_id = f"{user_no}_201_{post_no}"

        # 1) cbox_token 취득
        token_params = {
            "ticket": "blog",
            "templateId": "default",
            "pool": "blogid",
            "_cv": "20240207172406",
            "lang": "ko",
            "country": "",
            "objectId": object_id,
            "categoryId": "",
            "pageSize": "50",
            "indexSize": "10",
            "groupId": user_no,
            "listType": "OBJECT",
            "pageType": "default",
        }
        token_headers = {
            "authority": "apis.naver.com",
            "accept": "*/*",
            "referer": f"https://blog.naver.com/PostList.naver?blogId={self.blog_id}",
            "user-agent": _USER_AGENT,
        }

        resp = self.session_mgr.get(
            "https://apis.naver.com/commentBox/cbox/web_naver_token_jsonp.json",
            params=token_params, headers=token_headers)
        if not resp.ok:
            log(f"[Naver Blog] cbox_token 실패: {resp.status_code}", "error")
            return False

        cbox_token = resp.json().get("result", {}).get("cbox_token", "")
        if not cbox_token:
            log("[Naver Blog] cbox_token 비어 있음", "error")
            return False

        time.sleep(2)

        # 2) 댓글 작성
        comment_params = {
            "ticket": "blog",
            "templateId": "default",
            "pool": "blogid",
            "_cv": "20240207172406",
        }
        comment_data = {
            "lang": "ko",
            "country": "",
            "objectId": object_id,
            "categoryId": "",
            "pageSize": "50",
            "indexSize": "10",
            "groupId": user_no,
            "listType": "OBJECT",
            "pageType": "default",
            "clientType": "web-pc",
            "objectUrl": parse.quote(
                f"https://blog.naver.com/PostList.naver?blogId={self.blog_id}"),
            "contents": comment,
            "userType": "MANAGER",
            "pick": "false",
            "manager": "true",
            "score": "0",
            "likeItId": f"{self.blog_id}_{post_no}",
            "sort": "NEW",
            "secret": "false",
            "refresh": "true",
            "imageCount": "0",
            "commentType": "txt",
            "validateBanWords": "true",
            "cbox_token": cbox_token,
        }
        comment_headers = {
            "authority": "apis.naver.com",
            "accept": "application/json, text/javascript, */*; q=0.01",
            "content-type": "application/x-www-form-urlencoded; charset=UTF-8",
            "referer": f"https://blog.naver.com/PostList.naver?blogId={self.blog_id}",
            "user-agent": _USER_AGENT,
        }

        resp = self.session_mgr.post(
            "https://apis.naver.com/commentBox/cbox/web_naver_create_json.json",
            params=comment_params, headers=comment_headers, data=comment_data)

        if resp.ok:
            result = resp.json()
            comment_no = result.get("result", {}).get("comment", {}).get("commentNo", "")
            log(f"[Naver Blog] 댓글 작성 완료 (commentNo: {comment_no})", "ok")
            return True

        log(f"[Naver Blog] 댓글 작성 실패: {resp.status_code} {resp.text[:200]}", "error")
        return False
