"""수수료 데이터 품질 점검 리포트 (결과보고서 '어려움과 해결' 근거 수치용).

    python scripts/data_report.py            # data/fees.csv(전체) 또는 없으면 시연용 샘플
    python scripts/data_report.py --preview  # tests/fixtures/preview.xml (실제 응답 발췌)

출력: 지자체 수, 품목명 수, 규격 유형 분포(구간 해석 가능/단가형/단일 규격/선택형),
      같은 규격에 금액이 둘 이상인 건수, 무료 비율, 지자체별 기준일 범위.
"""
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from core.api_client import parse_xml  # noqa: E402
from core.fees import FeeTable, _spec_key, parse_per_unit, parse_range  # noqa: E402


def classify(spec: str, n_specs: int) -> str:
    if parse_per_unit(spec):
        return "단가형(㎡당 등)"
    if parse_range(spec):
        return "구간형(자동 판정 가능)"
    if n_specs == 1:
        return "단일 규격(모든규격 등)"
    return "선택형(자동 해석 안 함)"


def main() -> None:
    if "--preview" in sys.argv:
        rows, _ = parse_xml((ROOT / "tests" / "fixtures" / "preview.xml").read_text(encoding="utf-8"))
        table, label = FeeTable.from_rows(rows), "미리보기 발췌"
    else:
        table = FeeTable.load_default()
        label = "시연용 샘플" if table.is_sample else "data/fees.csv"
    groups, label_of = defaultdict(set), {}
    for r in table.rows:
        k = (r["region_id"], r["item"], _spec_key(r["spec"]))
        groups[k].add(r["fee"])
        label_of.setdefault(k, r["spec"])
    per_item = defaultdict(set)
    for (region, item, _k) in groups:
        per_item[(region, item)].add(_k)

    kinds = Counter()
    for (region, item, key) in groups:
        spec = label_of[(region, item, key)]
        kinds[classify(spec, len(per_item[(region, item)]))] += 1
    conflicts = sum(1 for f in groups.values() if len(f) > 1)
    free = sum(1 for r in table.rows if r["fee"] == 0)
    mismatch = sum(1 for r in table.rows if (r["paid_free"] == "무료" and r["fee"] > 0) or (r["paid_free"] == "유료" and r["fee"] == 0))
    from datetime import date, timedelta
    cutoff = (date.today() - timedelta(days=365)).isoformat()
    stale = sum(1 for r in table.rows if r["data_date"] and r["data_date"][:10] < cutoff)
    dates = sorted({r["data_date"] for r in table.rows if r["data_date"]})
    total = len(groups)

    print(f"[{label}] 행 {len(table.rows):,} / 지자체 {len(table.regions()):,} / 품목명 {len({r['item'] for r in table.rows}):,}종")
    print(f"규격(지자체·품목·규격 단위) {total:,}건")
    for k, v in kinds.most_common():
        print(f"  - {k}: {v:,}건 ({v / total:.1%})")
    print(f"같은 규격에 금액이 둘 이상(충돌): {conflicts:,}건 → 임의로 고르지 않고 '지자체 확인 필요'로 안내")
    print(f"무료(0원) 행: {free:,}건 ({free / len(table.rows):.1%})")
    print(f"유료/무료 표기와 금액이 모순인 행: {mismatch:,}건 → 확정하지 않고 지자체 확인 안내")
    print(f"데이터 기준일이 1년 넘게 지난 행: {stale:,}건 ({stale / len(table.rows):.1%}) → 결과에 경고 문구 추가")
    if dates:
        print(f"데이터 기준일 범위: {dates[0]} ~ {dates[-1]}")


if __name__ == "__main__":
    main()
