"""별표 목록(data/ordin/byl_list.csv)에서 지자체별 수수료 표 링크(data/ordin_links.json)를 만든다.

사용: python scripts/build_ordin_links.py [byl_list.csv 경로]
 - 폐기물 조례의 별표 중 이름에 수수료/부과기준/품목별/기준표가 있고 신고서·대장 같은 서식이 아닌 것만 고른다.
 - 교육청 조례는 제외한다. 주소는 일련번호만으로 만들어 인증키가 들어가지 않는다.
"""
from __future__ import annotations

import csv
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from core.ordin_links import view_url  # noqa: E402

FEE_NAME = re.compile(r"수수료|부과기준|품목별|기준표")
NOT_FEE_FORM = re.compile(r"납부|영수|대장|환불|신고|판매|스티커|신청서|필증|지정|접수|수불|징수|확인서|계약서|서식")
TAG = re.compile(r"<[^>]+>")


def clean(text: str) -> str:
    return re.sub(r"\s+", " ", TAG.sub("", text or "")).strip()


def build(rows: list[dict]) -> dict[str, list[dict]]:
    out: dict[str, dict[str, dict]] = {}
    for r in rows:
        name, law, org = clean(r.get("별표명", "")), clean(r.get("관련자치법규명", "")), r.get("전체기관명", "").strip()
        if not (org and r.get("별표일련번호") and r.get("관련자치법규일련번호")):
            continue
        if "교육" in org or "폐기물" not in law:
            continue
        if not FEE_NAME.search(name) or NOT_FEE_FORM.search(name):
            continue
        out.setdefault(org, {})[r["별표일련번호"]] = {
            "name": name, "ordinance": law, "date": r.get("자치법규시행일자", ""),
            "url": view_url(r["별표일련번호"], r["관련자치법규일련번호"]),
        }
    result = {}
    for org, items in out.items():
        lst = sorted(items.values(), key=lambda x: ("대형" not in x["name"], x["date"] and -int(x["date"] or 0)))
        result[org] = lst[:3]
    return dict(sorted(result.items()))


def main() -> int:
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "data" / "ordin" / "byl_list.csv"
    if not src.exists():
        print(f"파일이 없습니다: {src}")
        return 2
    with open(src, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    result = build(rows)
    dst = ROOT / "data" / "ordin_links.json"
    dst.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"별표 {len(rows)}건 중 {len(result)}개 지자체, 링크 {sum(len(v) for v in result.values())}개 -> {dst}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
