# coding: utf-8
"""나라장터 전 영업일 자동 수집 진입점 (스케줄러/exe용). 인자 없이 실행하면 전 영업일의 계약·입찰공고·낙찰을 모두 받는다."""
import sys

from g2b_api_crawler import main


if __name__ == "__main__":
    code = main([])
    if getattr(sys, "frozen", False):
        input("Press Enter to exit...")
    sys.exit(code)
