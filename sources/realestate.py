"""
청약홈 분양정보 수집 + 단지별 블로그 포스트 HTML 생성.

- 데이터명: 한국부동산원_청약홈 분양정보 조회 서비스 (data.go.kr)
- Base:    https://api.odcloud.kr/api/ApplyhomeInfoDetailSvc/v1
- Swagger: https://infuser.odcloud.kr/api/stages/37000/api-docs
- 인증:    DATA_GO_KR_KEY env

포스트 포맷은 00.Old_Source/wordpress/...getAPTLttotPblancDetail_ver7.py 의
레이아웃을 계승한다: 상단 도입 문구 → '요약 정보' → '상세 정보' → 해시태그.
"""
import os
from datetime import datetime, timedelta
from typing import Optional

import requests

from common.logger import log


BASE_URL = "https://api.odcloud.kr/api/ApplyhomeInfoDetailSvc/v1"
DATA_GO_KR_PORTAL = "https://www.data.go.kr"


class RealestateSource:
    """청약홈 분양정보 API 래퍼 + 블로그 HTML 빌더."""

    def __init__(self, service_key: Optional[str] = None):
        self.service_key = service_key or os.getenv("DATA_GO_KR_KEY", "")
        if not self.service_key:
            raise ValueError("DATA_GO_KR_KEY env 누락")

    # ─── 저수준 GET ──────────────────────────────────────────────────────────
    def _get(self, path: str, *, page: int = 1, per_page: int = 100) -> list[dict]:
        params = {
            "page": page,
            "perPage": per_page,
            "serviceKey": self.service_key,
        }
        url = f"{BASE_URL}/{path}"
        try:
            resp = requests.get(url, params=params, timeout=15)
            if not resp.ok:
                log(f"분양정보 API 오류 {resp.status_code}: {resp.text[:200]}", "error")
                return []
            payload = resp.json()
            data = payload.get("data", []) or []
            log(f"분양정보 {path}: {len(data)}건 (total={payload.get('totalCount')})", "ok")
            return data
        except Exception as e:
            log(f"분양정보 수집 실패: {e}", "error")
            return []

    # ─── 공개 메서드 ─────────────────────────────────────────────────────────
    def get_apt_subscriptions(self, per_page: int = 100) -> list[dict]:
        """APT 분양정보 상세조회."""
        return self._get("getAPTLttotPblancDetail", per_page=per_page)

    def get_urbty_subscriptions(self, per_page: int = 100) -> list[dict]:
        """오피스텔/도시형/민간임대/생활숙박 분양정보."""
        return self._get("getUrbtyOfctlLttotPblancDetail", per_page=per_page)

    def get_remndr_subscriptions(self, per_page: int = 100) -> list[dict]:
        """APT 잔여세대 분양정보."""
        return self._get("getRemndrLttotPblancDetail", per_page=per_page)

    # ─── 필터링 ──────────────────────────────────────────────────────────────
    @staticmethod
    def filter_upcoming(items: list[dict], *, days_ahead: int = 30,
                        region: Optional[str] = None) -> list[dict]:
        """청약 접수 시작일(RCEPT_BGNDE) 이 오늘부터 N 일 이내인 것만.

        region 이 주어지면 SUBSCRPT_AREA_CODE_NM 부분일치 필터.
        결과는 접수 시작일 오름차순 정렬.
        """
        today = datetime.now().date()
        cutoff = today + timedelta(days=days_ahead)

        def _in_window(item: dict) -> bool:
            raw = item.get("RCEPT_BGNDE") or ""
            if not raw:
                return False
            try:
                d = datetime.strptime(raw[:10], "%Y-%m-%d").date()
            except ValueError:
                return False
            return today <= d <= cutoff

        out = [i for i in items if _in_window(i)]
        if region:
            r = region.strip()
            out = [i for i in out
                   if r in (i.get("SUBSCRPT_AREA_CODE_NM") or "")]
        out.sort(key=lambda i: i.get("RCEPT_BGNDE") or "9999-12-31")
        return out

    # ─── HTML 빌더 ───────────────────────────────────────────────────────────
    @staticmethod
    def build_post(item: dict, *, extra_hashtags: Optional[list[str]] = None) -> dict:
        """단지 1건 → {title, content, tags, description} 반환.

        구 Old_Source/wordpress .../ver7 의 레이아웃을 유지하면서 모집공고 링크 CTA,
        구조화된 요약 박스, 빈 값을 '-' 로 정돈하는 advanced 처리 추가.
        """
        def V(key: str, default: str = "-") -> str:
            v = item.get(key)
            if v in (None, "", "null"):
                return default
            return str(v)

        house_nm      = V("HOUSE_NM")
        area_nm       = V("SUBSCRPT_AREA_CODE_NM")
        rcept_bgnde   = V("RCEPT_BGNDE")
        rcept_endde   = V("RCEPT_ENDDE")
        przwner_de    = V("PRZWNER_PRESNATN_DE")
        pblanc_url    = V("PBLANC_URL", "")
        hmpg          = V("HMPG_ADRES", "")
        cntrct_bg     = V("CNTRCT_CNCLS_BGNDE")
        cntrct_end    = V("CNTRCT_CNCLS_ENDDE")
        bsns_mby      = V("BSNS_MBY_NM")
        cnstrct       = V("CNSTRCT_ENTRPS_NM")
        tot_units     = V("TOT_SUPLY_HSHLDCO")
        hssply_adres  = V("HSSPLY_ADRES")
        hssply_zip    = V("HSSPLY_ZIP")
        mvn_ym        = V("MVN_PREARNGE_YM")
        mdhs_telno    = V("MDHS_TELNO")
        house_dtl_cd  = V("HOUSE_DTL_SECD")
        house_dtl_nm  = V("HOUSE_DTL_SECD_NM")
        house_manage  = V("HOUSE_MANAGE_NO")
        house_secd    = V("HOUSE_SECD")
        house_secd_nm = V("HOUSE_SECD_NM")
        imprmn        = V("IMPRMN_BSNS_AT")
        lrscl         = V("LRSCL_BLDLND_AT")
        mdat_area     = V("MDAT_TRGET_AREA_SECD")
        npln_pub      = V("NPLN_PRVOPR_PUBLIC_HOUSE_AT")
        parcprc       = V("PARCPRC_ULS_AT")
        pblanc_no     = V("PBLANC_NO")
        public_house  = V("PUBLIC_HOUSE_EARTH_AT")
        rcrit_pblanc  = V("RCRIT_PBLANC_DE")
        rent_secd     = V("RENT_SECD")
        rent_secd_nm  = V("RENT_SECD_NM")
        speclt        = V("SPECLT_RDN_EARTH_AT")
        spsply_bg     = V("SPSPLY_RCEPT_BGNDE")
        spsply_end    = V("SPSPLY_RCEPT_ENDDE")
        sub_code      = V("SUBSCRPT_AREA_CODE")
        rnk1_area     = V("GNRL_RNK1_CRSPAREA_RCPTDE")
        rnk1_etc      = V("GNRL_RNK1_ETC_AREA_RCPTDE")
        rnk1_gg       = V("GNRL_RNK1_ETC_GG_RCPTDE")
        rnk2_area     = V("GNRL_RNK2_CRSPAREA_RCPTDE")
        rnk2_etc      = V("GNRL_RNK2_ETC_AREA_RCPTDE")
        rnk2_gg       = V("GNRL_RNK2_ETC_GG_RCPTDE")

        # 해시태그: 지역 + 단지명(공백 제거) + 기본 세트
        tags: list[str] = ["분양정보", "부동산", "청약"]
        if area_nm not in ("-", ""):
            tags.append(area_nm.replace(" ", ""))
        if house_nm not in ("-", ""):
            tags.append(house_nm.replace(" ", ""))
        tags.append(f"{area_nm}청약")
        tags.append(f"{area_nm}분양")
        if extra_hashtags:
            tags += list(extra_hashtags)
        # 중복 제거 + '-' 제거
        tags = [t for t in dict.fromkeys(tags) if t and "-" not in t]
        hashtag_line = " ".join(f"#{t}" for t in tags)

        # 제목
        title = f"{area_nm} {house_nm} 분양정보 — 청약 {rcept_bgnde} 시작"

        # 메타
        meta_description = (
            f"{area_nm} {house_nm} 분양정보. 청약접수 {rcept_bgnde}~{rcept_endde}, "
            f"당첨자 발표 {przwner_de}. 시공사 {cnstrct}, 총 {tot_units}세대."
        )

        # H2 스타일 (구 소스 계승)
        h2_style = (
            'style="box-sizing: border-box; border-right-width: 0px; '
            'border-top-width: 0px; border-left: #333333 12px solid; '
            'border-bottom: #333333 2px solid; line-height: 1.7; '
            'margin-right: 0px; padding: 3px 5px 3px 10px;"'
        )

        # 요약 정보 박스 (advanced: 핵심 수치만 먼저 강조)
        hero = (
            '<div style="border:1px solid #e0e0e0; border-radius:8px; '
            'padding:16px; margin:12px 0; background:#fafafa;">'
            f'<p style="margin:0 0 6px 0; font-size:14px; color:#666;">📍 {area_nm} · {hssply_adres}</p>'
            f'<p style="margin:0 0 6px 0; font-size:20px; font-weight:700;">{house_nm}</p>'
            f'<p style="margin:0; font-size:14px; color:#333;">'
            f'🗓 청약 접수 <b>{rcept_bgnde} ~ {rcept_endde}</b> · '
            f'🏆 당첨자 발표 <b>{przwner_de}</b> · '
            f'🏠 총 <b>{tot_units}세대</b></p>'
            '</div>'
        )

        # 모집공고 CTA
        cta = ""
        if pblanc_url and pblanc_url != "-":
            cta = (
                '<p style="text-align:center; margin:20px 0;">'
                f'<a href="{pblanc_url}" target="_blank" rel="noopener" '
                'style="display:inline-block; padding:10px 20px; background:#2a7ae2; '
                'color:#fff; border-radius:4px; text-decoration:none; font-weight:600;">'
                '📄 모집공고 전문 보기 (청약홈)</a></p>'
            )

        hmpg_link = (
            f'<a href="{hmpg}" target="_blank" rel="noopener">{hmpg}</a>'
            if hmpg and hmpg != "-" else "-"
        )
        pblanc_link = (
            f'<a href="{pblanc_url}" target="_blank" rel="noopener">{pblanc_url}</a>'
            if pblanc_url and pblanc_url != "-" else "-"
        )

        intro = (
            '<p data-ke-size="size16">'
            f'<span style="color:#ee2323;"><i><b>{area_nm} {house_nm}</b></i></span>'
            f' 분양정보입니다. (청약접수시작일 : {rcept_bgnde}) '
            f'해당 게시물은 <a href="{DATA_GO_KR_PORTAL}" target="_blank" '
            'style="text-decoration:none;">'
            '<span style="background-color:#dddddd;">공공 데이터</span></a>'
            '를 주기적으로 확인하여 업데이트되는 내용이 있을 때마다 공유해 드리고 있습니다.</p>'
        )

        summary = (
            f'<h2 {h2_style} data-ke-size="size26"><span><b>요약 정보</b></span></h2>\n'
            '<ul style="list-style-type: disc;" data-ke-list-type="disc">\n'
            f'<li>주택명 : {house_nm}</li>\n'
            f'<li>홈페이지 주소 :&nbsp;{hmpg_link}</li>\n'
            f'<li>건설 업체명(시공사) : {cnstrct}</li>\n'
            f'<li>공급규모 : {tot_units}</li>\n'
            f'<li>주택 상세구분 코드명 : {house_dtl_nm}</li>\n'
            f'<li>투기과열지구 : {speclt}</li>\n'
            f'<li>청약접수시작일 : {rcept_bgnde}</li>\n'
            f'<li>청약접수 종료일 : {rcept_endde}</li>\n'
            f'<li>1순위 접수일 해당 지역 : {rnk1_area}</li>\n'
            f'<li>2순위 접수일 해당 지역 : {rnk2_area}</li>\n'
            f'<li>특별공급 접수 시작일 : {spsply_bg}</li>\n'
            f'<li>특별공급 접수 종료일 : {spsply_end}</li>\n'
            f'<li>문의처 : {mdhs_telno}</li>\n'
            '</ul>\n'
        )

        detail = (
            f'<h2 {h2_style} data-ke-size="size26"><span><b>상세 정보</b></span></h2>\n'
            '<ul style="list-style-type: disc;" data-ke-list-type="disc">\n'
            f'<li>사업주체명(시행사) :&nbsp;{bsns_mby}</li>\n'
            f'<li>건설 업체명(시공사) :&nbsp;{cnstrct}</li>\n'
            f'<li>계약 시작일 :&nbsp;{cntrct_bg}</li>\n'
            f'<li>계약 종료일 :&nbsp;{cntrct_end}</li>\n'
            f'<li>1순위 접수일 해당 지역 :&nbsp;{rnk1_area}</li>\n'
            f'<li>1순위 접수일 기타 지역 :&nbsp;{rnk1_etc}</li>\n'
            f'<li>1순위 접수일 경기지역 :&nbsp;{rnk1_gg}</li>\n'
            f'<li>2순위 접수일 해당 지역 :&nbsp;{rnk2_area}</li>\n'
            f'<li>2순위 접수일 기타 지역 :&nbsp;{rnk2_etc}</li>\n'
            f'<li>2순위 접수일 경기지역 :&nbsp;{rnk2_gg}</li>\n'
            f'<li>홈페이지 주소 :&nbsp;{hmpg_link}</li>\n'
            f'<li>주택 상세구분코드 (01 : 민영, 03 : 국민) :&nbsp;{house_dtl_cd}</li>\n'
            f'<li>주택 상세구분 코드명 :&nbsp;{house_dtl_nm}</li>\n'
            f'<li>주택관리번호 :&nbsp;{house_manage}</li>\n'
            f'<li>주택명 :&nbsp;{house_nm}</li>\n'
            f'<li>주택 구분코드(01 : APT) :&nbsp;{house_secd}</li>\n'
            f'<li>주택 구분 코드명 :&nbsp;{house_secd_nm}</li>\n'
            f'<li>공급 위치 :&nbsp;{hssply_adres}</li>\n'
            f'<li>공급 위치 우편번호 :&nbsp;{hssply_zip}</li>\n'
            f'<li>정비 사업 :&nbsp;{imprmn}</li>\n'
            f'<li>대규모 택지개발지구 :&nbsp;{lrscl}</li>\n'
            '<li>조정대상지역 (Y : 과열지역, Y : 미대 상지 역, S : 위축지역) '
            f':&nbsp;{mdat_area}</li>\n'
            f'<li>문의처 :&nbsp;{mdhs_telno}</li>\n'
            f'<li>입주예정월 :&nbsp;{mvn_ym}</li>\n'
            f'<li>수도권 내 민영 공공주택지구 :&nbsp;{npln_pub}</li>\n'
            f'<li>분양가 상한제 :&nbsp;{parcprc}</li>\n'
            f'<li>공고번호 :&nbsp;{pblanc_no}</li>\n'
            f'<li>모집공고 URL :&nbsp;{pblanc_link}</li>\n'
            f'<li>당첨자 발표일 :&nbsp;{przwner_de}</li>\n'
            f'<li>공공주택지구 :&nbsp;{public_house}</li>\n'
            f'<li>청약접수시작일 :&nbsp;{rcept_bgnde}</li>\n'
            f'<li>청약접수 종료일 :&nbsp;{rcept_endde}</li>\n'
            f'<li>모집공고일 (YYYY-MM-DD) :&nbsp;{rcrit_pblanc}</li>\n'
            '<li>분양 구분코드 (0 : 분양주택, 1 : 분양전환 가능 임대, 2 : 분양전환 불가 임대) '
            f':&nbsp;{rent_secd}</li>\n'
            f'<li>분양 구분 코드명 :&nbsp;{rent_secd_nm}</li>\n'
            f'<li>투기과열지구 :&nbsp;{speclt}</li>\n'
            f'<li>특별공급 접수시작일 :&nbsp;{spsply_bg}</li>\n'
            f'<li>특별공급 접수 종료일 :&nbsp;{spsply_end}</li>\n'
            f'<li>공급지역코드 :&nbsp;{sub_code}</li>\n'
            f'<li>공급 지역명 :&nbsp;{area_nm}</li>\n'
            f'<li>공급규모 :&nbsp;{tot_units}</li>\n'
            '</ul>\n'
        )

        footer = (
            '<p><small>📌 본 정보는 한국부동산원 청약홈(공공데이터포털)에서 '
            '주기적으로 가져와 업데이트됩니다. 실제 청약 및 계약 전 '
            '<a href="' + pblanc_url + '" target="_blank" rel="noopener">모집공고 전문</a>을 '
            '반드시 확인하세요.</small></p>'
            if pblanc_url and pblanc_url != "-"
            else '<p><small>📌 본 정보는 한국부동산원 청약홈(공공데이터포털)에서 주기적으로 업데이트됩니다.</small></p>'
        )

        content = (
            intro
            + hero
            + cta
            + summary
            + detail
            + footer
            + f'<p>&nbsp;</p>\n<p>{hashtag_line}</p>\n'
        )

        return {
            "title": title,
            "content": content,
            "tags": tags,
            "description": meta_description,
            "house_manage_no": house_manage,
        }
