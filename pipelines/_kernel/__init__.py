"""Pipeline kernels: 공통 run() 골격 모음.

상품형 WP/뉴스픽 등 반복되는 파이프라인 실행 순서를 캡슐화.
각 파이프라인은 Config 데이터클래스만 정의하고 kernel.run(cfg) 호출.

모듈:
    base_runner  — 범용 fetch→publish→notify 루프 (run_pipeline)
    newspick     — 뉴스픽→단일 Publisher 골격
    product_wp   — 쿠팡/알리 상품→WordPress 골격
"""
from .base_runner import run_pipeline

__all__ = ["run_pipeline"]
