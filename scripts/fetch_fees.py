"""공공데이터포털 오픈API로 수수료 표준데이터를 받아 data/fees.csv 로 저장한다(수동 실행용).

    set DATA_GO_KR_KEY=인증키        (PowerShell: $env:DATA_GO_KR_KEY="인증키")
    python scripts/fetch_fees.py [--max-pages 2] [--sigungu 종로구] [--debug]

앱(app.py)도 인증키가 설정돼 있으면 시작할 때 같은 방식으로 자동 갱신한다.
"""
import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core.api_client import (DEFAULT_URL, OUT, ApiError, fetch_all, parse_json, parse_response,  # noqa: E402,F401
                             parse_xml, save_csv)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sigungu", help="시군구명으로 필터(선택, 파라미터명 sggNm 추정)")
    ap.add_argument("--max-pages", type=int, help="테스트용 최대 페이지 수")
    ap.add_argument("--debug", action="store_true", help="첫 응답 원문 앞부분을 출력")
    args = ap.parse_args()
    key = os.environ.get("DATA_GO_KR_KEY")
    if not key:
        sys.exit("환경변수 DATA_GO_KR_KEY(인증키)를 먼저 설정하세요.")
    rows = fetch_all(os.environ.get("FEE_API_URL", DEFAULT_URL), key,
                     {"sggNm": args.sigungu} if args.sigungu else {}, args.max_pages, args.debug,
                     progress=lambda n, t: print(f"\r{n}/{t}", end="", flush=True))
    print()
    if not rows:
        sys.exit("받은 데이터가 없습니다.")
    partial = bool(args.max_pages or args.sigungu)
    target = OUT.with_name("fees_partial.csv") if partial else OUT  # 일부만 받은 시험 결과는 앱이 읽지 않는 파일에 저장
    fields = save_csv(rows, target)
    print(f"{len(rows)}행 저장 → {target}\n컬럼: {', '.join(fields)}")
    if partial:
        print("※ 일부만 받은 시험 결과라 data/fees.csv 는 건드리지 않았습니다. 전체는 옵션 없이 실행하세요.")


if __name__ == "__main__":
    main()
