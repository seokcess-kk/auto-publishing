"""
WordPress 프로필 로더 (공통 모듈).

config.json 의 `wordpress_profiles` 를 읽거나 .env 로 폴백.
각 프로필은 site_url, jwt_token, category_id, tag_id, channel_id 등을 포함.
"""
import json
import os

from common.logger import log
from publishers.wordpress import WordPressPublisher


_BASE_DIR    = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_CONFIG_PATH = os.path.join(_BASE_DIR, "config.json")


def load_wp_profile(profile_name: str = None) -> dict:
    """config.json 에서 WordPress 프로필 로드. 없으면 .env 폴백."""
    if not os.path.exists(_CONFIG_PATH):
        return {
            "name":        os.getenv("WP_USERNAME", ""),
            "site_url":    os.getenv("WP_SITE_URL", ""),
            "username":    os.getenv("WP_USERNAME", ""),
            "jwt_token":   os.getenv("WP_JWT_TOKEN", ""),
            "category_id": int(os.getenv("WP_CATEGORY_ID", "1")),
            "tag_id":      int(os.getenv("WP_TAG_ID", "1")),
            "channel_id":  os.getenv("COUPANG_CHANNEL_ID_WP", ""),
        }

    with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
        config = json.load(f)

    profiles = config.get("wordpress_profiles", {})
    if not profile_name:
        profile_name = config.get("default_profile", "")

    if profile_name not in profiles:
        log(f"프로필 '{profile_name}' 없음. 사용 가능: {list(profiles.keys())}", "error")
        return {}

    profile = dict(profiles[profile_name])
    profile["name"] = profile_name

    if not profile.get("jwt_token") and profile.get("jwt_token_env"):
        profile["jwt_token"] = os.getenv(profile["jwt_token_env"], "")

    return profile


def list_wp_profiles() -> list:
    """config.json 의 모든 프로필 이름 반환."""
    if not os.path.exists(_CONFIG_PATH):
        return []
    with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
        config = json.load(f)
    return list(config.get("wordpress_profiles", {}).keys())


def build_publisher(profile: dict) -> WordPressPublisher:
    """프로필로부터 JWT 인증 Publisher 인스턴스 생성."""
    return WordPressPublisher(
        site_url=profile["site_url"],
        jwt_token=profile["jwt_token"],
    )
