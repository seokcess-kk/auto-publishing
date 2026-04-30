"""
트위터(X) 자동 발행 Publisher
- browser_cookie3로 Chrome Profile 2에서 세션 쿠키 추출
- X GraphQL API (CreateTweet)

참조: 04.Platform_Social/twitter/twitter(reqeusts)_뉴스픽(requests)_자동발행_배포용_ver6.py
"""
import os
import json
import pickle
from typing import Optional

import browser_cookie3
import requests

from common.logger import log
from .base import Publisher, PostResult


CHROME_COOKIE_FILE = os.path.expanduser(
    "~/Library/Application Support/Google/Chrome/Profile 2/Cookies"
)
SESSION_FILE = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), ".sessions", "twitter.pkl"
)

BEARER = (
    "Bearer AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOuH5E6I8xnZz4puTs"
    "%3D1Zv7ttfk8LF81IUq16cHjhLTvJu4FA33AGWWjCpTnA"
)
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
CREATE_TWEET_ID = "SoVnbfCycZ7fERGCwpZkYA"
CREATE_TWEET_URL = f"https://x.com/i/api/graphql/{CREATE_TWEET_ID}/CreateTweet"

FEATURES = {
    "rweb_video_screen_enabled": False,
    "profile_label_improvements_pcf_label_in_post_enabled": True,
    "responsive_web_graphql_timeline_navigation_enabled": True,
    "responsive_web_graphql_skip_user_profile_image_extensions_enabled": False,
    "responsive_web_edit_tweet_api_enabled": True,
    "graphql_is_translatable_rweb_tweet_is_translatable_enabled": True,
    "view_counts_everywhere_api_enabled": True,
    "longform_notetweets_consumption_enabled": True,
    "responsive_web_twitter_article_tweet_consumption_enabled": True,
    "freedom_of_speech_not_reach_fetch_enabled": True,
    "standardized_nudges_misinfo": True,
    "tweet_with_visibility_results_prefer_gql_limited_actions_policy_enabled": True,
    "longform_notetweets_rich_text_read_enabled": True,
    "longform_notetweets_inline_media_enabled": False,
    "responsive_web_enhance_cards_enabled": False,
    "verified_phone_label_enabled": False,
    "creator_subscriptions_tweet_preview_api_enabled": True,
    "responsive_web_profile_redirect_enabled": False,
    "rweb_tipjar_consumption_enabled": False,
    "premium_content_api_read_enabled": False,
    "communities_web_enable_tweet_community_results_fetch": True,
    "c9s_tweet_anatomy_moderator_badge_enabled": True,
    "content_disclosure_indicator_enabled": True,
    "content_disclosure_ai_generated_indicator_enabled": True,
    "post_ctas_fetch_enabled": True,
    "responsive_web_grok_analyze_button_fetch_trends_enabled": False,
    "responsive_web_grok_analyze_post_followups_enabled": True,
    "responsive_web_jetfuel_frame": True,
    "responsive_web_grok_share_attachment_enabled": True,
    "responsive_web_grok_annotations_enabled": True,
    "articles_preview_enabled": True,
    "responsive_web_grok_show_grok_translated_post": True,
    "responsive_web_grok_analysis_button_from_backend": True,
    "responsive_web_grok_image_annotation_enabled": True,
    "responsive_web_grok_imagine_annotation_enabled": True,
    "responsive_web_grok_community_note_auto_translation_is_enabled": True,
}


class TwitterPublisher(Publisher):
    """트위터(X) 발행기 — browser_cookie3 + GraphQL API."""

    def __init__(self):
        self.session = requests.Session()
        self._ct0 = ""

    def _load_chrome_cookies(self) -> bool:
        """Chrome Profile 2에서 x.com 쿠키 추출."""
        if not os.path.exists(CHROME_COOKIE_FILE):
            log("Chrome Profile 2 쿠키 파일 없음", "error")
            return False
        try:
            cj = browser_cookie3.chrome(
                cookie_file=CHROME_COOKIE_FILE, domain_name=".x.com"
            )
            for c in cj:
                self.session.cookies.set(c.name, c.value,
                                         domain=".x.com", path="/")
                if c.name == "ct0":
                    self._ct0 = c.value
            if not self._ct0:
                log("ct0 쿠키 없음 — X 로그인 필요", "error")
                return False
            log("Chrome 쿠키 추출 성공", "ok")
            return True
        except Exception as e:
            log(f"Chrome 쿠키 추출 실패: {e}", "error")
            return False

    def _setup_headers(self):
        """API 요청용 헤더 설정."""
        self.session.headers.update({
            "User-Agent": USER_AGENT,
            "authorization": BEARER,
            "x-csrf-token": self._ct0,
            "x-twitter-auth-type": "OAuth2Session",
            "x-twitter-active-user": "yes",
            "x-twitter-client-language": "ko",
            "content-type": "application/json",
        })

    def _save_session(self):
        """세션 쿠키 pickle 저장."""
        os.makedirs(os.path.dirname(SESSION_FILE), exist_ok=True)
        with open(SESSION_FILE, "wb") as f:
            pickle.dump(self.session.cookies, f)

    def _load_session(self) -> bool:
        """저장된 세션 로드."""
        if not os.path.exists(SESSION_FILE):
            return False
        try:
            with open(SESSION_FILE, "rb") as f:
                cookies = pickle.load(f)
            self.session.cookies = cookies
            self._ct0 = self.session.cookies.get("ct0", domain=".x.com") or ""
            if not self._ct0:
                return False
            self._setup_headers()
            return True
        except Exception:
            return False

    def _verify_session(self) -> bool:
        """세션 유효성 확인."""
        try:
            resp = self.session.get(
                "https://x.com/i/api/graphql/Fb7fyZ9MMCzvf_bNtwNdXA/HomeTimeline",
                params={
                    "variables": json.dumps({"count": 1,
                                             "includePromotedContent": False}),
                    "features": json.dumps(FEATURES),
                },
                timeout=10,
            )
            return resp.status_code == 200
        except Exception:
            return False

    def login(self) -> bool:
        """세션 로드 또는 Chrome 쿠키 추출."""
        # 1. 저장된 세션 시도
        if self._load_session() and self._verify_session():
            log("Twitter 세션 복원 성공", "ok")
            return True

        # 2. Chrome 쿠키 추출
        if not self._load_chrome_cookies():
            return False

        self._setup_headers()

        if not self._verify_session():
            log("Twitter 세션 검증 실패 — 브라우저에서 X 재로그인 필요", "error")
            return False

        self._save_session()
        log("Twitter 로그인 성공", "ok")
        return True

    def post(self, title: str, content: str,
             tags: list = None, category: str = "",
             image_url: str = "", **kwargs) -> PostResult:
        """트윗 발행."""
        tweet_text = content
        if tags:
            hashtags = " ".join(f"#{t.lstrip('#')}" for t in tags[:5])
            tweet_text = f"{tweet_text}\n{hashtags}"

        # 280자 초과 시 자르기
        if len(tweet_text) > 280:
            tweet_text = tweet_text[:277] + "..."

        log(f"트윗 발행: {tweet_text[:60]}...", "step")

        variables = {
            "tweet_text": tweet_text,
            "dark_request": False,
            "media": {"media_entities": [], "possibly_sensitive": False},
            "semantic_annotation_ids": [],
        }

        resp = self.session.post(
            CREATE_TWEET_URL,
            json={
                "variables": variables,
                "features": FEATURES,
                "queryId": CREATE_TWEET_ID,
            },
            timeout=10,
        )

        if resp.status_code == 200:
            data = resp.json()
            tweet = (data.get("data", {})
                        .get("create_tweet", {})
                        .get("tweet_results", {})
                        .get("result", {}))
            rest_id = tweet.get("rest_id", "")
            user_handle = (tweet.get("core", {})
                               .get("user_results", {})
                               .get("result", {})
                               .get("legacy", {})
                               .get("screen_name", ""))
            tweet_url = (f"https://x.com/{user_handle}/status/{rest_id}"
                        if rest_id else "")
            log(f"트윗 발행 성공: {tweet_url}", "ok")
            return PostResult(success=True, url=tweet_url, post_id=rest_id)
        else:
            msg = resp.text[:200]
            log(f"트윗 발행 실패 ({resp.status_code}): {msg}", "error")
            return PostResult(success=False, message=msg)

    def upload_image(self, local_path: str) -> str:
        """트위터 이미지 업로드 후 media_id 반환."""
        url = "https://upload.x.com/1.1/media/upload.json"
        headers = {
            "User-Agent": USER_AGENT,
            "authorization": BEARER,
            "x-csrf-token": self._ct0,
        }
        with open(local_path, "rb") as f:
            resp = self.session.post(url, files={"media": f},
                                     headers=headers, timeout=30)
        if resp.ok:
            media_id = resp.json().get("media_id_string", "")
            log(f"트위터 이미지 업로드: {media_id}", "ok")
            return media_id
        log(f"이미지 업로드 실패: {resp.status_code}", "error")
        return ""

    def get_categories(self) -> list:
        return []
