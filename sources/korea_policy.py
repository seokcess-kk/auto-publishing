"""
정책브리핑(korea.kr) RSS 피드 수집 모듈

https://www.korea.kr/etc/rss.do 의 전체 RSS 피드를 구분별로 수집.
feedparser 기반으로 API 키 없이 사용 가능.

구분:
- 정책포털 뉴스 (정책뉴스, 국민이 말하는 정책, 정책칼럼, 이슈인사이트)
- 정책포털 멀티미디어 (영상, 숏폼, 카드/한컷, 사진, 웹툰)
- 정책포털 브리핑룸 (보도자료, 사실은 이렇습니다, 부처/청와대/국무회의 브리핑, 연설문)
- 정책포털 정책자료 (전문자료)
- 정책포털 K-공감 (전체)
- 부처별 RSS (26개 부처)
- 청별 RSS (18개 청)
- 위원회 RSS (6개 위원회)
- 대통령 소속 위원회 RSS (4개)
"""
from typing import Optional

from common.logger import log

try:
    import feedparser
    HAS_FEEDPARSER = True
except ImportError:
    HAS_FEEDPARSER = False


# ─── 정책포털 뉴스 ─────────────────────────────────────────────────────────

PORTAL_NEWS = {
    "정책뉴스":         "https://www.korea.kr/rss/policy.xml",
    "국민이말하는정책":  "https://www.korea.kr/rss/reporter.xml",
    "정책칼럼":         "https://www.korea.kr/rss/column.xml",
    "이슈인사이트":     "https://www.korea.kr/rss/insight.xml",
}

# ─── 정책포털 멀티미디어 ───────────────────────────────────────────────────

PORTAL_MULTIMEDIA = {
    "영상":     "https://www.korea.kr/rss/media.xml",
    "숏폼":     "https://www.korea.kr/rss/shorts.xml",
    "카드한컷":  "https://www.korea.kr/rss/visual.xml",
    "사진":     "https://www.korea.kr/rss/photo.xml",
    "웹툰":     "https://www.korea.kr/rss/cartoon.xml",
}

# ─── 정책포털 브리핑룸 ─────────────────────────────────────────────────────

PORTAL_BRIEFING = {
    "보도자료":           "https://www.korea.kr/rss/pressrelease.xml",
    "사실은이렇습니다":    "https://www.korea.kr/rss/fact.xml",
    "부처브리핑":         "https://www.korea.kr/rss/ebriefing.xml",
    "청와대브리핑":       "https://www.korea.kr/rss/president.xml",
    "국무회의브리핑":     "https://www.korea.kr/rss/cabinet.xml",
    "연설문":             "https://www.korea.kr/rss/speech.xml",
}

# ─── 정책포털 정책자료 / K-공감 ────────────────────────────────────────────

PORTAL_ETC = {
    "전문자료":   "https://www.korea.kr/rss/expdoc.xml",
    "K공감":      "https://www.korea.kr/rss/archive.xml",
}

# ─── 부처 RSS ──────────────────────────────────────────────────────────────

DEPT_MINISTRY = {
    "국무조정실":         "https://www.korea.kr/rss/dept_opm.xml",
    "재정경제부":         "https://www.korea.kr/rss/dept_moef.xml",
    "과학기술정보통신부":  "https://www.korea.kr/rss/dept_msit.xml",
    "교육부":             "https://www.korea.kr/rss/dept_moe.xml",
    "외교부":             "https://www.korea.kr/rss/dept_mofa.xml",
    "통일부":             "https://www.korea.kr/rss/dept_unikorea.xml",
    "법무부":             "https://www.korea.kr/rss/dept_moj.xml",
    "국방부":             "https://www.korea.kr/rss/dept_mnd.xml",
    "행정안전부":         "https://www.korea.kr/rss/dept_mois.xml",
    "국가보훈부":         "https://www.korea.kr/rss/dept_mpva.xml",
    "문화체육관광부":     "https://www.korea.kr/rss/dept_mcst.xml",
    "농림축산식품부":     "https://www.korea.kr/rss/dept_mafra.xml",
    "산업통상자원부":     "https://www.korea.kr/rss/dept_motir.xml",
    "보건복지부":         "https://www.korea.kr/rss/dept_mw.xml",
    "기후에너지환경부":   "https://www.korea.kr/rss/dept_mcee.xml",
    "고용노동부":         "https://www.korea.kr/rss/dept_moel.xml",
    "성평등가족부":       "https://www.korea.kr/rss/dept_mogef.xml",
    "국토교통부":         "https://www.korea.kr/rss/dept_molit.xml",
    "해양수산부":         "https://www.korea.kr/rss/dept_mof.xml",
    "중소벤처기업부":     "https://www.korea.kr/rss/dept_mss.xml",
    "기획예산처":         "https://www.korea.kr/rss/dept_mpb.xml",
    "인사혁신처":         "https://www.korea.kr/rss/dept_mpm.xml",
    "법제처":             "https://www.korea.kr/rss/dept_moleg.xml",
    "식품의약품안전처":   "https://www.korea.kr/rss/dept_mfds.xml",
    "국가데이터처":       "https://www.korea.kr/rss/dept_mods.xml",
    "지식재산처":         "https://www.korea.kr/rss/dept_moip.xml",
}

# ─── 청 RSS ────────────────────────────────────────────────────────────────

DEPT_AGENCY = {
    "국세청":                     "https://www.korea.kr/rss/dept_nts.xml",
    "관세청":                     "https://www.korea.kr/rss/dept_customs.xml",
    "조달청":                     "https://www.korea.kr/rss/dept_pps.xml",
    "우주항공청":                 "https://www.korea.kr/rss/dept_kasa.xml",
    "재외동포청":                 "https://www.korea.kr/rss/dept_oka.xml",
    "검찰청":                     "https://www.korea.kr/rss/dept_spo.xml",
    "병무청":                     "https://www.korea.kr/rss/dept_mma.xml",
    "방위사업청":                 "https://www.korea.kr/rss/dept_dapa.xml",
    "경찰청":                     "https://www.korea.kr/rss/dept_npa.xml",
    "소방청":                     "https://www.korea.kr/rss/dept_nfa.xml",
    "국가유산청":                 "https://www.korea.kr/rss/dept_khs.xml",
    "농촌진흥청":                 "https://www.korea.kr/rss/dept_rda.xml",
    "산림청":                     "https://www.korea.kr/rss/dept_forest.xml",
    "질병관리청":                 "https://www.korea.kr/rss/dept_kdca.xml",
    "기상청":                     "https://www.korea.kr/rss/dept_kma.xml",
    "행정중심복합도시건설청":     "https://www.korea.kr/rss/dept_macc.xml",
    "새만금개발청":               "https://www.korea.kr/rss/dept_sda.xml",
    "해양경찰청":                 "https://www.korea.kr/rss/dept_kcg.xml",
}

# ─── 위원회 RSS ────────────────────────────────────────────────────────────

DEPT_COMMITTEE = {
    "방송미디어통신위원회":   "https://www.korea.kr/rss/dept_kmcc.xml",
    "원자력안전위원회":       "https://www.korea.kr/rss/dept_nssc.xml",
    "공정거래위원회":         "https://www.korea.kr/rss/dept_ftc.xml",
    "금융위원회":             "https://www.korea.kr/rss/dept_fsc.xml",
    "국민권익위원회":         "https://www.korea.kr/rss/dept_acrc.xml",
    "개인정보보호위원회":     "https://www.korea.kr/rss/dept_pipc.xml",
}

# ─── 대통령 소속 위원회 RSS ────────────────────────────────────────────────

DEPT_PRESIDENTIAL = {
    "국민통합위원회":         "https://www.korea.kr/rss/dept_k_cohesion.xml",
    "저출산고령사회위원회":   "https://www.korea.kr/rss/dept_betterfuture.xml",
    "경제사회노동위원회":     "https://www.korea.kr/rss/dept_esdc.xml",
    "국가기후위기대응위원회": "https://www.korea.kr/rss/dept_pcccr.xml",
}

# ─── 전체 카탈로그 ─────────────────────────────────────────────────────────

ALL_FEEDS = {
    "포털_뉴스":       PORTAL_NEWS,
    "포털_멀티미디어":  PORTAL_MULTIMEDIA,
    "포털_브리핑룸":    PORTAL_BRIEFING,
    "포털_기타":       PORTAL_ETC,
    "부처":           DEPT_MINISTRY,
    "청":             DEPT_AGENCY,
    "위원회":          DEPT_COMMITTEE,
    "대통령소속위원회": DEPT_PRESIDENTIAL,
}


class KoreaPolicySource:
    """정책브리핑(korea.kr) RSS 수집기."""

    def __init__(self):
        if not HAS_FEEDPARSER:
            raise ImportError("feedparser 패키지 필요: pip install feedparser")

    def fetch(self, feed_name: str, feed_url: str,
              count: int = 10) -> list[dict]:
        """단일 RSS 피드 수집.

        Args:
            feed_name: 피드 이름 (로그용)
            feed_url: RSS URL
            count: 최대 수집 건수

        Returns:
            기사 정보 dict 목록
        """
        log(f"korea.kr RSS 수집: {feed_name}", "step")
        try:
            feed = feedparser.parse(feed_url)
            items = []
            for entry in feed.entries[:count]:
                image = ""
                if hasattr(entry, "media_content"):
                    image = entry.media_content[0].get("url", "")
                elif hasattr(entry, "enclosures") and entry.enclosures:
                    image = entry.enclosures[0].get("url", "")

                items.append({
                    "title":    entry.get("title", ""),
                    "link":     entry.get("link", ""),
                    "summary":  entry.get("summary", ""),
                    "pub_date": entry.get("published", ""),
                    "category": feed_name,
                    "image":    image,
                })
            log(f"korea.kr RSS '{feed_name}' {len(items)}건 수집", "ok")
            return items
        except Exception as e:
            log(f"korea.kr RSS '{feed_name}' 수집 실패: {e}", "error")
            return []

    # ─── 정책포털 뉴스 ─────────────────────────────────────────────────

    def fetch_policy_news(self, count: int = 10) -> list[dict]:
        """정책뉴스 RSS."""
        return self.fetch("정책뉴스", PORTAL_NEWS["정책뉴스"], count)

    def fetch_reporter(self, count: int = 10) -> list[dict]:
        """국민이 말하는 정책 RSS."""
        return self.fetch("국민이말하는정책", PORTAL_NEWS["국민이말하는정책"], count)

    def fetch_column(self, count: int = 10) -> list[dict]:
        """정책칼럼 RSS."""
        return self.fetch("정책칼럼", PORTAL_NEWS["정책칼럼"], count)

    def fetch_insight(self, count: int = 10) -> list[dict]:
        """이슈인사이트 RSS."""
        return self.fetch("이슈인사이트", PORTAL_NEWS["이슈인사이트"], count)

    # ─── 정책포털 멀티미디어 ───────────────────────────────────────────

    def fetch_media(self, count: int = 10) -> list[dict]:
        """영상 RSS."""
        return self.fetch("영상", PORTAL_MULTIMEDIA["영상"], count)

    def fetch_shorts(self, count: int = 10) -> list[dict]:
        """숏폼 RSS."""
        return self.fetch("숏폼", PORTAL_MULTIMEDIA["숏폼"], count)

    def fetch_visual(self, count: int = 10) -> list[dict]:
        """카드/한컷 RSS."""
        return self.fetch("카드한컷", PORTAL_MULTIMEDIA["카드한컷"], count)

    def fetch_photo(self, count: int = 10) -> list[dict]:
        """사진 RSS."""
        return self.fetch("사진", PORTAL_MULTIMEDIA["사진"], count)

    def fetch_cartoon(self, count: int = 10) -> list[dict]:
        """웹툰 RSS."""
        return self.fetch("웹툰", PORTAL_MULTIMEDIA["웹툰"], count)

    # ─── 정책포털 브리핑룸 ─────────────────────────────────────────────

    def fetch_pressrelease(self, count: int = 10) -> list[dict]:
        """보도자료 RSS."""
        return self.fetch("보도자료", PORTAL_BRIEFING["보도자료"], count)

    def fetch_fact(self, count: int = 10) -> list[dict]:
        """사실은 이렇습니다 RSS."""
        return self.fetch("사실은이렇습니다", PORTAL_BRIEFING["사실은이렇습니다"], count)

    def fetch_ebriefing(self, count: int = 10) -> list[dict]:
        """부처 브리핑 RSS."""
        return self.fetch("부처브리핑", PORTAL_BRIEFING["부처브리핑"], count)

    def fetch_president(self, count: int = 10) -> list[dict]:
        """청와대 브리핑 RSS."""
        return self.fetch("청와대브리핑", PORTAL_BRIEFING["청와대브리핑"], count)

    def fetch_cabinet(self, count: int = 10) -> list[dict]:
        """국무회의 브리핑 RSS."""
        return self.fetch("국무회의브리핑", PORTAL_BRIEFING["국무회의브리핑"], count)

    def fetch_speech(self, count: int = 10) -> list[dict]:
        """연설문 RSS."""
        return self.fetch("연설문", PORTAL_BRIEFING["연설문"], count)

    # ─── 정책자료 / K-공감 ─────────────────────────────────────────────

    def fetch_expdoc(self, count: int = 10) -> list[dict]:
        """전문자료 RSS."""
        return self.fetch("전문자료", PORTAL_ETC["전문자료"], count)

    def fetch_archive(self, count: int = 10) -> list[dict]:
        """K-공감 RSS."""
        return self.fetch("K공감", PORTAL_ETC["K공감"], count)

    # ─── 구분별 일괄 수집 ──────────────────────────────────────────────

    def fetch_by_category(self, category: str,
                          count: int = 10) -> dict[str, list[dict]]:
        """특정 구분의 모든 피드를 일괄 수집.

        Args:
            category: '포털_뉴스', '포털_멀티미디어', '포털_브리핑룸',
                      '포털_기타', '부처', '청', '위원회', '대통령소속위원회'
            count: 피드당 최대 수집 건수

        Returns:
            {피드이름: [기사 목록]} dict
        """
        feeds = ALL_FEEDS.get(category)
        if not feeds:
            log(f"알 수 없는 카테고리: {category}", "error")
            return {}

        results = {}
        for name, url in feeds.items():
            results[name] = self.fetch(name, url, count)
        return results

    def fetch_department(self, dept_name: str,
                         count: int = 10) -> list[dict]:
        """특정 부처/청/위원회의 RSS 수집.

        Args:
            dept_name: 부처명 (예: '국토교통부', '기상청', '금융위원회')
            count: 최대 수집 건수

        Returns:
            기사 목록
        """
        for catalog in [DEPT_MINISTRY, DEPT_AGENCY, DEPT_COMMITTEE,
                        DEPT_PRESIDENTIAL]:
            if dept_name in catalog:
                return self.fetch(dept_name, catalog[dept_name], count)

        log(f"알 수 없는 부처/기관: {dept_name}", "error")
        return []

    # ─── HTML 포맷팅 ───────────────────────────────────────────────────

    def format_post_content(self, items: list[dict],
                            section_title: str = "정책브리핑") -> str:
        """수집된 기사를 블로그 포스트 HTML로 변환."""
        if not items:
            return "<p>수집된 기사가 없습니다.</p>"

        articles = []
        for item in items:
            img_tag = ""
            if item.get("image"):
                img_tag = f"<img src='{item['image']}' alt='{item['title']}' style='max-width:100%;'><br>\n"

            article = (
                f"<article>\n"
                f"<h3>{item['title']}</h3>\n"
                f"{img_tag}"
                f"<p class='meta'>{item.get('pub_date', '')} | {item.get('category', '')}</p>\n"
                f"<p>{item['summary']}</p>\n"
                f"<p><a href='{item['link']}' target='_blank'>원문 보기</a></p>\n"
                f"</article>\n<hr>\n"
            )
            articles.append(article)

        html = (
            f"<h2>{section_title}</h2>\n"
            + "\n".join(articles)
            + "<p><small>출처: 대한민국 정책브리핑 www.korea.kr</small></p>"
        )
        return html

    @staticmethod
    def list_all_feeds() -> dict[str, dict[str, str]]:
        """사용 가능한 전체 RSS 피드 카탈로그 반환."""
        return ALL_FEEDS

    @staticmethod
    def list_categories() -> list[str]:
        """사용 가능한 카테고리 목록 반환."""
        return list(ALL_FEEDS.keys())

    @staticmethod
    def list_departments() -> list[str]:
        """사용 가능한 부처/청/위원회 목록 반환."""
        all_depts = []
        for catalog in [DEPT_MINISTRY, DEPT_AGENCY, DEPT_COMMITTEE,
                        DEPT_PRESIDENTIAL]:
            all_depts.extend(catalog.keys())
        return all_depts
