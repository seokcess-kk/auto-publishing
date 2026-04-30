"""
네이버 카페 SmartEditor v2 document JSON 빌더.

Old_Source naver_cafe/네이버카페_쿠팡파트너스/...adpick_ver6.py 의
write_naver_cafe_article_for_sentence_image 의 SmartEditor 템플릿을 이식.

핵심 구조 (쿠팡 양식):
  table 3행 1열
    row1: 메인 이미지 (cafeptthumb-phinf 업로드 결과 path/url)
    row2: RECOMMENDED PRODUCTS 박스
          - "￦ {price} /won"
          - "Discount Rate {discount}"
          - "Star Evaluation {rating}"
          - "Review {review_count}"
          - "Good product!!!"
    row3: "🔻🔻 구매는 댓글 확인 🔻🔻" (m.cafe.naver.com 링크)

분양/일출일몰 양식은 추가 섹션을 더 넣은 변형.
"""
from __future__ import annotations

import json
import uuid
from typing import Optional


def _id() -> str:
    """SmartEditor 노드 ID 생성 — 'SE-uuid' 형식."""
    return f"SE-{uuid.uuid4()}"


def _doc_id() -> str:
    """document.id — 26자 base32 ULID 비슷한 형식. UUID 의 hex 일부 사용."""
    return uuid.uuid4().hex.upper()[:26]


def _text_node(value: str, *, color: str = "#000000",
               size: str = "fs15", bold: bool = False,
               bg: Optional[str] = None,
               link_url: Optional[str] = None) -> dict:
    """SmartEditor textNode 생성."""
    style = {
        "fontColor": color,
        "fontFamily": "system",
        "fontSizeCode": size,
        "bold": bold,
        "italic": False,
        "@ctype": "nodeStyle",
    }
    if bg:
        style["backgroundColor"] = bg
    node = {
        "id": _id(),
        "value": value,
        "style": style,
        "@ctype": "textNode",
    }
    if link_url:
        node["link"] = {"url": link_url, "@ctype": "urlLink"}
    return node


def _paragraph(nodes: list[dict], *, align: str = "center",
               line_height: float = 1.6) -> dict:
    return {
        "id": _id(),
        "nodes": nodes,
        "style": {
            "align": align,
            "lineHeight": line_height,
            "@ctype": "paragraphStyle",
        },
        "@ctype": "paragraph",
    }


def _image_node(*, src: str, path: str, width: int = 600, height: int = 600,
                filename: str = "upload.png", filesize: int = 100000,
                link_url: Optional[str] = None) -> dict:
    """이미지 노드. src/path 는 cafe.upphoto 응답에서 받은 값.

    src   = "https://cafeptthumb-phinf.pstatic.net{path}?type=w1600"
    path  = "/MjAyNi8wNC8yNi8x..."  (cafe.upphoto 응답)
    """
    node = {
        "id": _id(),
        "src": src,
        "internalResource": True,
        "represent": True,
        "path": path,
        "domain": "https://cafeptthumb-phinf.pstatic.net",
        "width": int(width),
        "height": int(height),
        "fileName": filename,
        "fileSize": int(filesize),
        "@ctype": "imageNode",
    }
    if link_url:
        node["link"] = {"url": link_url, "@ctype": "urlLink"}
    return node


def _table_cell(value: list[dict], *, height: int = 43) -> dict:
    return {
        "id": _id(),
        "borderInlineStyle": (
            "border-top:none;border-right:1px solid rgb(210, 210, 210);"
            "border-left:none;border-bottom:1px solid rgb(210, 210, 210);"
        ),
        "colSpan": 1,
        "rowSpan": 1,
        "width": 100,
        "height": height,
        "backgroundColor": "#ffffff",
        "value": value,
        "@ctype": "tableCell",
    }


def _table_row(cell: dict) -> dict:
    return {"cells": [cell], "@ctype": "tableRow"}


def _empty_text_component() -> dict:
    """본문 끝에 들어가는 빈 텍스트 component (Old_Source 와 동일)."""
    return {
        "id": _id(),
        "layout": "default",
        "value": [
            _paragraph([_text_node("", size="fs11", bold=True)]),
            _paragraph([_text_node("", size="fs11", bold=True)]),
            _paragraph([_text_node("", size="fs11", bold=True)]),
        ],
        "@ctype": "text",
    }


# ─── 양식별 빌더 ────────────────────────────────────────────────────────────

def build_coupang_document(
    *,
    image_src: str,
    image_path: str,
    image_filename: str = "upload.png",
    image_filesize: int = 100000,
    image_width: int = 600,
    image_height: int = 600,
    cafe_id_no: str,
    article_id_placeholder: str = "%ARTICLE_ID%",
    product_name: str = "",
    price: str = "0",
    discount_rate: str = "No data",
    star_rating: str = "No data",
    review_count: str = "0",
) -> str:
    """쿠팡 카페 글 본문용 SmartEditor document JSON 문자열.

    기본 1행 1열 표 안에 (이미지) → (RECOMMENDED PRODUCTS 박스) → (구매는 댓글 확인)
    article_id_placeholder 는 발행 후 실제 articleId 로 치환할 수 있도록 표시.
    cafe_id_no 는 카페 숫자 ID (NAVER_CAFE_CLUB_ID 와 동일).
    """
    comments_url = (
        f"https://m.cafe.naver.com/ca-fe/web/cafes/{cafe_id_no}"
        f"/articles/{article_id_placeholder}/comments"
    )

    # row1: 메인 이미지 (댓글 페이지로 링크)
    row1 = _table_row(_table_cell([
        _paragraph([
            _text_node(""),
            _image_node(
                src=image_src, path=image_path,
                width=image_width, height=image_height,
                filename=image_filename, filesize=image_filesize,
                link_url=comments_url,
            ),
            _text_node(""),
        ]),
    ]))

    # row2: RECOMMENDED PRODUCTS 박스 (상품명 + 가격 + 할인 + 별점 + 리뷰 + Good)
    box_paragraphs = [
        _paragraph([_text_node("RECOMMENDED PRODUCTS", size="fs11", bold=True)]),
    ]
    if product_name:
        box_paragraphs.append(_paragraph([
            _text_node(product_name[:80], size="fs13", bold=True),
        ]))
    box_paragraphs.extend([
        _paragraph([
            _text_node("￦", color="#212529", size="fs16", bg="#ffffff"),
            _text_node(price, size="fs34", bold=True),
            _text_node("/won", size="fs16"),
        ]),
        _paragraph([
            _text_node("Discount Rate", size="fs15", bold=True),
            _text_node(f" {discount_rate}", size="fs15"),
        ]),
        _paragraph([
            _text_node("Star ", size="fs15", bold=True),
            _text_node("Evaluation", color="#202124", size="fs15", bold=True, bg="#f8f9fa"),
            _text_node(f" {star_rating}", size="fs15"),
        ]),
        _paragraph([
            _text_node("Review", size="fs15", bold=True),
            _text_node(f" {review_count}", size="fs15"),
        ]),
        _paragraph([
            _text_node("Good", size="fs15", bold=True),
            _text_node(" product!!!", size="fs15"),
        ]),
    ])
    row2 = _table_row(_table_cell(box_paragraphs))

    # row3: "🔻🔻 구매는 댓글 확인 🔻🔻"
    row3 = _table_row(_table_cell([
        _paragraph([
            _text_node("🔻🔻 ", size="fs15"),
            _text_node("구매는 댓글 확인", size="fs15", bold=True, link_url=comments_url),
            _text_node(" 🔻🔻", size="fs15"),
        ]),
    ]))

    table_component = {
        "id": _id(),
        "layout": "default",
        "align": "center",
        "width": 43,
        "rows": [row1, row2, row3],
        "columnCount": 1,
        "borderInlineStyle": (
            "border-top:1px solid rgb(210, 210, 210);"
            "border-right:none;border-left:1px solid rgb(210, 210, 210);"
            "border-bottom:none;border-collapse:separate;"
        ),
        "@ctype": "table",
    }

    document = {
        "document": {
            "version": "2.8.0",
            "theme": "default",
            "language": "ko-KR",
            "id": _doc_id(),
            "components": [table_component, _empty_text_component()],
            "di": {
                "dif": False,
                "dio": [
                    {"dis": "N", "dia": {"t": 0, "p": 0, "st": 1, "sk": 0}},
                    {"dis": "N", "dia": {"t": 0, "p": 0, "st": 355, "sk": 0}},
                ],
            },
        },
        "documentId": "",
    }
    return json.dumps(document, ensure_ascii=False)


def build_realestate_document(
    *,
    images: list[dict],   # [{src, path, filename, filesize, width, height}, ...]
    cafe_id_no: str,
    article_id_placeholder: str = "%ARTICLE_ID%",
    summary: dict,        # 요약 정보 14개 필드
    detail: dict,         # 상세 정보 다수 필드
    intro_area: str = "",
    intro_house: str = "",
    intro_rcept: str = "",
) -> str:
    """분양정보 카페 글 본문용 SmartEditor document JSON.

    구조:
        표 1행 1열 (대표 이미지 1장 + 댓글 링크)
        '요약 정보' 헤더 + 글머리표 14항목
        '상세 정보' 헤더 + 글머리표 다수 항목
        '사진 정보 & 링크 정보' 표 (이미지 3장 가로 나열)
        '🔻🔻 구매는 댓글 확인 🔻🔻'
    """
    comments_url = (
        f"https://m.cafe.naver.com/ca-fe/web/cafes/{cafe_id_no}"
        f"/articles/{article_id_placeholder}/comments"
    )

    components: list[dict] = []

    # 도입부 ─────────────────────────────────────────────────────────────
    components.append({
        "id": _id(),
        "layout": "default",
        "value": [
            _paragraph([
                _text_node(f"{intro_area} {intro_house}",
                           color="#ee2323", size="fs16", bold=True),
                _text_node(f" 분양정보입니다. (청약접수시작일 : {intro_rcept}) "
                           "해당 게시물은 ", size="fs16"),
                _text_node("공공 데이터", size="fs16", bg="#dddddd",
                           link_url="https://www.data.go.kr"),
                _text_node("를 주기적으로 확인하여 업데이트되는 내용이 있을 때마다 "
                           "공유해 드리고 있습니다.", size="fs16"),
            ], align="left"),
        ],
        "@ctype": "text",
    })

    # 헤더 (요약 정보) ──────────────────────────────────────────────────────
    def _h2(label: str) -> dict:
        return {
            "id": _id(),
            "layout": "default",
            "value": [
                _paragraph([_text_node(label, color="#000000", size="fs26", bold=True)],
                           align="left"),
            ],
            "@ctype": "text",
        }

    def _ul(items: list[tuple]) -> dict:
        # items: [(label, value), ...] → 각 line "label : value"
        return {
            "id": _id(),
            "layout": "default",
            "value": [
                _paragraph(
                    [_text_node(f"{label} : {value}", size="fs15")],
                    align="left",
                )
                for label, value in items
            ],
            "@ctype": "text",
        }

    components.append(_h2("요약 정보"))
    components.append(_ul([
        ("주택명", summary.get("HOUSE_NM", "-")),
        ("홈페이지 주소", summary.get("HMPG_ADRES", "-")),
        ("건설 업체명(시공사)", summary.get("CNSTRCT_ENTRPS_NM", "-")),
        ("공급규모", summary.get("TOT_SUPLY_HSHLDCO", "-")),
        ("주택 상세구분 코드명", summary.get("HOUSE_DTL_SECD_NM", "-")),
        ("투기과열지구", summary.get("SPECLT_RDN_EARTH_AT", "-")),
        ("청약접수시작일", summary.get("RCEPT_BGNDE", "-")),
        ("청약접수 종료일", summary.get("RCEPT_ENDDE", "-")),
        ("1순위 접수일 해당 지역", summary.get("GNRL_RNK1_CRSPAREA_RCPTDE", "-")),
        ("2순위 접수일 해당 지역", summary.get("GNRL_RNK2_CRSPAREA_RCPTDE", "-")),
        ("특별공급 접수 시작일", summary.get("SPSPLY_RCEPT_BGNDE", "-")),
        ("특별공급 접수 종료일", summary.get("SPSPLY_RCEPT_ENDDE", "-")),
        ("문의처", summary.get("MDHS_TELNO", "-")),
    ]))

    # 상세 정보 ─────────────────────────────────────────────────────────────
    components.append(_h2("상세 정보"))
    detail_items = [
        ("사업주체명(시행사)", detail.get("BSNS_MBY_NM", "-")),
        ("건설 업체명(시공사)", detail.get("CNSTRCT_ENTRPS_NM", "-")),
        ("계약 시작일", detail.get("CNTRCT_CNCLS_BGNDE", "-")),
        ("계약 종료일", detail.get("CNTRCT_CNCLS_ENDDE", "-")),
        ("1순위 접수일 해당 지역", detail.get("GNRL_RNK1_CRSPAREA_RCPTDE", "-")),
        ("1순위 접수일 기타 지역", detail.get("GNRL_RNK1_ETC_AREA_RCPTDE", "-")),
        ("1순위 접수일 경기지역", detail.get("GNRL_RNK1_ETC_GG_RCPTDE", "-")),
        ("2순위 접수일 해당 지역", detail.get("GNRL_RNK2_CRSPAREA_RCPTDE", "-")),
        ("2순위 접수일 기타 지역", detail.get("GNRL_RNK2_ETC_AREA_RCPTDE", "-")),
        ("2순위 접수일 경기지역", detail.get("GNRL_RNK2_ETC_GG_RCPTDE", "-")),
        ("홈페이지 주소", detail.get("HMPG_ADRES", "-")),
        ("주택 상세구분코드 (01 : 민영, 03 : 국민)", detail.get("HOUSE_DTL_SECD", "-")),
        ("주택 상세구분 코드명", detail.get("HOUSE_DTL_SECD_NM", "-")),
        ("주택관리번호", detail.get("HOUSE_MANAGE_NO", "-")),
        ("주택명", detail.get("HOUSE_NM", "-")),
        ("주택 구분코드(01 : APT)", detail.get("HOUSE_SECD", "-")),
        ("주택 구분 코드명", detail.get("HOUSE_SECD_NM", "-")),
        ("공급 위치", detail.get("HSSPLY_ADRES", "-")),
        ("공급 위치 우편번호", detail.get("HSSPLY_ZIP", "-")),
        ("정비 사업", detail.get("IMPRMN_BSNS_AT", "-")),
        ("대규모 택지개발지구", detail.get("LRSCL_BLDLND_AT", "-")),
        ("조정대상지역 (Y : 과열지역, Y : 미대 상지 역, S : 위축지역)",
            detail.get("MDAT_TRGET_AREA_SECD", "-")),
        ("문의처", detail.get("MDHS_TELNO", "-")),
        ("입주예정월", detail.get("MVN_PREARNGE_YM", "-")),
        ("수도권 내 민영 공공주택지구", detail.get("NPLN_PRVOPR_PUBLIC_HOUSE_AT", "-")),
        ("분양가 상한제", detail.get("PARCPRC_ULS_AT", "-")),
        ("공고번호", detail.get("PBLANC_NO", "-")),
        ("모집공고 URL", detail.get("PBLANC_URL", "-")),
        ("당첨자 발표일", detail.get("PRZWNER_PRESNATN_DE", "-")),
        ("공공주택지구", detail.get("PUBLIC_HOUSE_EARTH_AT", "-")),
        ("청약접수시작일", detail.get("RCEPT_BGNDE", "-")),
        ("청약접수 종료일", detail.get("RCEPT_ENDDE", "-")),
        ("모집공고일 (YYYY-MM-DD)", detail.get("RCRIT_PBLANC_DE", "-")),
        ("분양 구분코드 (0 : 분양주택, 1 : 분양전환 가능 임대, 2 : 분양전환 불가 임대)",
            detail.get("RENT_SECD", "-")),
        ("분양 구분 코드명", detail.get("RENT_SECD_NM", "-")),
        ("투기과열지구", detail.get("SPECLT_RDN_EARTH_AT", "-")),
        ("특별공급 접수시작일", detail.get("SPSPLY_RCEPT_BGNDE", "-")),
        ("특별공급 접수 종료일", detail.get("SPSPLY_RCEPT_ENDDE", "-")),
        ("공급지역코드", detail.get("SUBSCRPT_AREA_CODE", "-")),
        ("공급 지역명", detail.get("SUBSCRPT_AREA_CODE_NM", "-")),
        ("공급규모", detail.get("TOT_SUPLY_HSHLDCO", "-")),
    ]
    components.append(_ul(detail_items))

    # '사진 정보 & 링크 정보' 섹션은 사용자 요청으로 제거 (분양정보 양식)

    # RECOMMENDED PRODUCTS 표 — 가격 위에 상품명 추가
    box_paragraphs = [
        _paragraph([_text_node("RECOMMENDED PRODUCTS", size="fs11", bold=True)]),
    ]
    product_name = detail.get("__product_name__", "") or ""
    if product_name:
        box_paragraphs.append(_paragraph([
            _text_node(product_name[:80], size="fs13", bold=True),
        ]))
    box_paragraphs.extend([
        _paragraph([
            _text_node("￦", size="fs16"),
            _text_node(detail.get("__price__", "0"), size="fs34", bold=True),
            _text_node("/won", size="fs16"),
        ]),
        _paragraph([
            _text_node("Discount Rate", size="fs15", bold=True),
            _text_node(f" {detail.get('__discount__', 'No data')}", size="fs15"),
        ]),
        _paragraph([
            _text_node("Star ", size="fs15", bold=True),
            _text_node("Evaluation", size="fs15", bold=True),
            _text_node(f" {detail.get('__rating__', 'No data')}", size="fs15"),
        ]),
        _paragraph([
            _text_node("Review", size="fs15", bold=True),
            _text_node(f" {detail.get('__review__', '0')}", size="fs15"),
        ]),
        _paragraph([
            _text_node("Good", size="fs15", bold=True),
            _text_node(" product!!!", size="fs15"),
        ]),
    ])
    components.append({
        "id": _id(),
        "layout": "default",
        "align": "center",
        "width": 43,
        "rows": [
            _table_row(_table_cell(box_paragraphs)),
            _table_row(_table_cell([
                _paragraph([
                    _text_node("🔻🔻 ", size="fs15"),
                    _text_node("구매는 댓글 확인", size="fs15", bold=True, link_url=comments_url),
                    _text_node(" 🔻🔻", size="fs15"),
                ]),
            ])),
        ],
        "columnCount": 1,
        "borderInlineStyle": (
            "border-top:1px solid rgb(210, 210, 210);"
            "border-right:none;border-left:1px solid rgb(210, 210, 210);"
            "border-bottom:none;border-collapse:separate;"
        ),
        "@ctype": "table",
    })

    components.append(_empty_text_component())

    document = {
        "document": {
            "version": "2.8.0",
            "theme": "default",
            "language": "ko-KR",
            "id": _doc_id(),
            "components": components,
            "di": {"dif": False, "dio": [
                {"dis": "N", "dia": {"t": 0, "p": 0, "st": 1, "sk": 0}},
            ]},
        },
        "documentId": "",
    }
    return json.dumps(document, ensure_ascii=False)


def build_riseset_document(
    *,
    image_src: str,
    image_path: str,
    image_filename: str = "riseset.png",
    image_filesize: int = 200000,
    image_width: int = 1080,
    image_height: int = 1080,
    cafe_id_no: str,
    article_id_placeholder: str = "%ARTICLE_ID%",
    riseset_table_html_lines: list[str],   # 지역별 일출일몰 표 텍스트 라인
    product_name: str = "",
    product_price: str = "0",
    product_review: str = "0",
) -> str:
    """일출일몰 카페 글 본문용 SmartEditor document JSON.

    구조:
        헤더 (큰 일출/일몰 시각)
        지역별 일출일몰 표
        쿠팡 추천 상품 카드 (RECOMMENDED PRODUCTS)
        '🔻🔻 구매는 댓글 확인 🔻🔻'
    """
    comments_url = (
        f"https://m.cafe.naver.com/ca-fe/web/cafes/{cafe_id_no}"
        f"/articles/{article_id_placeholder}/comments"
    )

    components: list[dict] = []

    # 헤더: 일출일몰 표 텍스트 (지역별)
    components.append({
        "id": _id(),
        "layout": "default",
        "value": [
            _paragraph([_text_node(line, size="fs16")], align="left")
            for line in riseset_table_html_lines
        ],
        "@ctype": "text",
    })

    # 추천 상품 카드 표 (이미지 + RECOMMENDED PRODUCTS + 댓글 링크)
    components.append({
        "id": _id(),
        "layout": "default",
        "align": "center",
        "width": 43,
        "rows": [
            _table_row(_table_cell([
                _paragraph([
                    _image_node(
                        src=image_src, path=image_path,
                        width=image_width, height=image_height,
                        filename=image_filename, filesize=image_filesize,
                        link_url=comments_url,
                    ),
                ]),
            ])),
            _table_row(_table_cell(
                [_paragraph([_text_node("📦 오늘의 추천 상품", size="fs13", bold=True)])]
                + ([_paragraph([_text_node(product_name[:80], size="fs13", bold=True)])]
                   if product_name else [])
                + [
                    _paragraph([
                        _text_node("￦ ", size="fs16"),
                        _text_node(f"{product_price}원", size="fs34", bold=True, color="#e4000f"),
                    ]),
                    _paragraph([_text_node(f"리뷰 {product_review}개", size="fs13")]),
                ]
            )),
            _table_row(_table_cell([
                _paragraph([
                    _text_node("🔻🔻 ", size="fs15"),
                    _text_node("구매는 댓글 확인", size="fs15", bold=True, link_url=comments_url),
                    _text_node(" 🔻🔻", size="fs15"),
                ]),
            ])),
        ],
        "columnCount": 1,
        "borderInlineStyle": (
            "border-top:1px solid rgb(210, 210, 210);"
            "border-right:none;border-left:1px solid rgb(210, 210, 210);"
            "border-bottom:none;border-collapse:separate;"
        ),
        "@ctype": "table",
    })

    components.append(_empty_text_component())

    document = {
        "document": {
            "version": "2.8.0",
            "theme": "default",
            "language": "ko-KR",
            "id": _doc_id(),
            "components": components,
            "di": {"dif": False, "dio": [
                {"dis": "N", "dia": {"t": 0, "p": 0, "st": 1, "sk": 0}},
            ]},
        },
        "documentId": "",
    }
    return json.dumps(document, ensure_ascii=False)
