"""
Gemini AI 콘텐츠 생성 모듈
- 제목/키워드로 블로그 글 생성
- 상품 설명 생성
- 뉴스 요약

참조: 10.AI_Integration/gemini_ai/gemini_ai.py
     01.Platform_Naver/naver_cafe/ 내 Gemini 연동 코드
"""
import os
from typing import Optional

from common.logger import log


class GeminiGenerator:
    """Google Gemini API 기반 콘텐츠 생성기."""

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.getenv("GEMINI_API_KEY", "")
        self._model = None

    def _get_model(self):
        if self._model is None:
            try:
                import google.generativeai as genai
            except ImportError:
                raise ImportError("google-generativeai 패키지 필요: pip install google-generativeai")
            genai.configure(api_key=self.api_key)
            self._model = genai.GenerativeModel("gemini-2.5-flash")
        return self._model

    def generate(self, prompt: str) -> str:
        """프롬프트로 텍스트 생성."""
        log(f"Gemini 생성 요청: {prompt[:60]}...", "step")
        try:
            model = self._get_model()
            response = model.generate_content(prompt)
            text = response.text
            log("Gemini 생성 완료", "ok")
            return text
        except Exception as e:
            log(f"Gemini 생성 실패: {e}", "error")
            return ""

    def write_blog_post(self, title: str, keywords: list[str] = None,
                         length: int = 1000) -> str:
        """블로그 포스트 생성.

        Args:
            title:    포스트 제목
            keywords: 포함할 키워드 목록
            length:   목표 글자 수

        Returns:
            HTML 형식의 블로그 본문
        """
        kw_str = ", ".join(keywords) if keywords else ""
        prompt = (
            f"제목: {title}\n"
            f"키워드: {kw_str}\n"
            f"위 제목과 키워드를 바탕으로 {length}자 내외의 블로그 포스트를 HTML 형식으로 작성해줘. "
            "서론, 본론, 결론 구조를 갖추고, 소제목에는 <h2>, 강조는 <strong> 태그를 사용해줘."
        )
        return self.generate(prompt)

    def summarize(self, text: str, max_sentences: int = 3) -> str:
        """뉴스/기사 텍스트 요약."""
        prompt = (
            f"다음 내용을 {max_sentences}문장 이내로 핵심만 요약해줘:\n\n{text}"
        )
        return self.generate(prompt)

    def write_product_description(self, product_name: str,
                                   price: str = "", features: list[str] = None) -> str:
        """상품 소개 글 생성."""
        feat_str = "\n".join(f"- {f}" for f in features) if features else ""
        prompt = (
            f"상품명: {product_name}\n"
            f"가격: {price}\n"
            f"특징:\n{feat_str}\n\n"
            "위 상품을 구매욕을 자극하는 300자 내외의 소개 글로 작성해줘. "
            "장점을 중심으로 간결하게 써줘."
        )
        return self.generate(prompt)

    def generate_hashtags(self, topic: str, count: int = 10) -> list[str]:
        """주제에 맞는 해시태그 생성."""
        prompt = (
            f"'{topic}' 주제에 어울리는 SNS 해시태그 {count}개를 #기호 포함하여 쉼표로 구분해서 나열해줘."
        )
        result = self.generate(prompt)
        tags = [t.strip() for t in result.split(",") if t.strip().startswith("#")]
        return tags[:count]
