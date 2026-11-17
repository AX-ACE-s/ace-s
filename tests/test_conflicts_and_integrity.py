"""같은 규격에 금액이 둘 이상인 경우와, 원본 전 행 재조회 검증."""
import math
import sys
from collections import defaultdict
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
from core.fees import FeeError, FeeTable, _spec_key, parse_per_unit  # noqa: E402
from fetch_fees import parse_xml  # noqa: E402

HEAD = "ctpvNm,sggNm,larWasNm,larWasSpcfct,paidFreeYn,fee,mngInstNm,crtrYmd,larWasSeNm\n"
R = "서울특별시 가구"


def make(*lines):
    return FeeTable.from_csv_text(HEAD + "\n".join(lines) + "\n")


def test_conflict_is_not_picked_arbitrarily():
    t = make("서울특별시,가구,소파,3인용,유료,5000,가구구청,2026-01-01,가구류",
             "서울특별시,가구,소파,3인용,유료,8000,가구구청,2026-02-01,가구류")
    r = t.calculate(R, "소파", spec="3인용")
    assert r["conflict"] and r["fee"] is None and "확인 필요" in r["fee_text"]
    assert [c["fee"] for c in r["candidates"]] == [5000, 8000]
    assert "5,000원 ~ 8,000원" in r["fee_text"] and "지자체에 확인" in r["notice"]
    q = t.spec_question(R, "소파")
    assert q["conflicts"] == ["3인용"] and q["fees"]["3인용"] is None


def test_same_fee_duplicates_are_not_conflicts():
    t = make("서울특별시,가구,소파,3인용,유료,5000,가구구청,2026-01-01,가구류",
             "서울특별시,가구,소파,3인용,유료,5000,가구구청,2026-01-01,가구류")
    r = t.calculate(R, "소파", spec="3인용")
    assert not r["conflict"] and r["fee"] == 5000


def test_spec_labels_differing_only_by_spaces_are_one_spec():
    t = make("서울특별시,가구,책장,모든규격,유료,3000,가구구청,2026-01-01,가구류",
             "서울특별시,가구,책장,모든 규격,유료,4000,가구구청,2026-01-01,가구류")
    assert len(t.specs(R, "책장")) == 1 and t.calculate(R, "책장")["conflict"]
    assert t.calculate(R, "책장", spec="모든  규격")["conflict"]  # 공백이 달라도 같은 규격으로 인식


def test_total_excludes_unconfirmed_items():
    t = make("서울특별시,가구,소파,3인용,유료,5000,가구구청,2026-01-01,가구류",
             "서울특별시,가구,소파,3인용,유료,8000,가구구청,2026-02-01,가구류",
             "서울특별시,가구,의자,일반,유료,2000,가구구청,2026-01-01,가구류")
    total = t.calculate_many(R, [{"item": "소파", "spec": "3인용"}, {"item": "의자"}])
    assert total["total"] == 2000 and total["unconfirmed"] == 1


def test_ambiguous_spec_is_choice_only_not_measurable():
    t = make("서울특별시,가구,소파,3인용(폭 1.8m 이하),유료,6000,가구구청,2026-01-01,가구류",
             "서울특별시,가구,소파,3인용(폭 1.8m 초과),유료,9000,가구구청,2026-01-01,가구류")
    assert t.spec_question(R, "소파")["measure_kind"] is None
    with pytest.raises(FeeError):
        t.calculate(R, "소파", measure=150)  # 잘못된 자동 판정 대신 직접 선택 요구
    assert t.calculate(R, "소파", spec="3인용(폭 1.8m 이하)")["fee"] == 6000


# ---------------- 원본 전 행 재조회: 모든 (지자체·품목·규격)이 원본 금액과 일치하는지
def _verify(table: FeeTable):
    raw, label = defaultdict(set), {}
    for r in table.rows:  # 한 번만 훑어 금액 집합과 규격 표기를 모은다(행마다 전체를 다시 훑으면 수 분이 걸림)
        k = (r["region_id"], r["item"], _spec_key(r["spec"]))
        raw[k].add(r["fee"])
        label.setdefault(k, r["spec"])
    checked = 0
    for (region, item, key), fees in raw.items():
        spec = label[(region, item, key)]
        unit = parse_per_unit(spec)
        m = max(unit["size"], 10.0) if unit and unit["kind"] == "length" else (unit["size"] if unit else None)
        res = table.calculate(region, item, spec=spec, measure=m)
        if res.get("reason") == "inconsistent":  # 유료/무료 표기 모순 행은 확정하지 않고 등록값만 보여 줌
            assert res["candidates"][0]["fee"] in fees
        elif len(fees) > 1:
            assert res["conflict"] and {c["fee"] for c in res["candidates"]} == fees, (region, item, spec)
        else:
            expected = next(iter(fees)) * (math.ceil(m / unit["size"]) if unit else 1)
            assert not res["conflict"] and res["fee"] == expected, (region, item, spec, res["fee"], fees)
        # 비교표도 원본과 대조: 같은 지역·품목의 다른 규격 금액이 원본 행과 일치하고 차액 계산이 맞는지
        for a in res["alternatives"]:
            ak = (region, item, _spec_key(a["spec"]))
            assert ak in raw, (region, item, a["spec"])
            afees = raw[ak]
            if len(afees) > 1:
                assert a["conflict"] and a["fee"] is None, (region, item, a["spec"])
            else:
                assert not a["conflict"] and a["fee"] == next(iter(afees)), (region, item, a["spec"])
                if res["fee"] is not None:
                    assert a["diff"] == a["fee"] - res["fee"], (region, item, a["spec"])
        assert res["copy_text"] and res["explanation"]
        checked += 1
    return checked


def test_every_row_matches_source_in_preview():
    rows, _ = parse_xml((ROOT / "tests" / "fixtures" / "preview.xml").read_text(encoding="utf-8"))
    assert _verify(FeeTable.from_rows(rows)) > 30


def test_every_row_matches_source_in_loaded_data():
    """data/fees.csv(API로 받은 전체 데이터)가 있으면 그 전체를, 없으면 시연용 샘플을 검증한다."""
    assert _verify(FeeTable.load_default()) > 10


# ---------------- 단위 오입력 / 오래된 기준일 / 유료·무료 표기 모순
H2 = "ctpvNm,sggNm,larWasNm,larWasSpcfct,paidFreeYn,fee,mngInstNm,crtrYmd,larWasSeNm\n"


def test_unit_mixups_are_rejected_not_silently_judged():
    t = make("서울특별시,가구,장롱,1m 미만,유료,5000,구청,2026-01-01,가구류",
             "서울특별시,가구,장롱,1m 이상 2m 미만,유료,8000,구청,2026-01-01,가구류",
             "서울특별시,가구,텔레비전,32인치 미만,유료,3000,구청,2026-01-01,가구류",
             "서울특별시,가구,텔레비전,32인치 이상,유료,5000,구청,2026-01-01,가구류")
    with pytest.raises(FeeError, match="cm 단위"):
        t.calculate(R, "장롱", measure=1.5)  # 1.5m 를 의도했지만 cm 칸에 1.5 → 싸게 판정되던 문제
    assert t.calculate(R, "장롱", measure=150)["fee"] == 8000
    with pytest.raises(FeeError, match="인치"):
        t.calculate(R, "텔레비전", measure=140)  # cm 를 넣은 경우
    assert t.calculate(R, "텔레비전", measure=55)["fee"] == 5000


def test_stale_data_date_warns():
    t = make("서울특별시,가구,의자,모든규격,유료,2000,구청,2023-07-12,가구류",
             "서울특별시,가구,책상,모든규격,유료,3000,구청,2099-01-01,가구류")
    old = t.calculate(R, "의자")
    assert old["basis"]["is_stale"] and "2023-07-12" in old["notice"] and old["fee"] == 2000  # 금액은 그대로, 경고만 추가
    assert not t.calculate(R, "책상")["basis"]["is_stale"]


def test_paid_free_mismatch_is_not_shown_as_free_or_paid():
    t = make("서울특별시,가구,책상,모든규격,무료,3000,구청,2026-09-01,가구류",
             "서울특별시,가구,의자,모든규격,유료,0,구청,2026-09-01,가구류",
             "서울특별시,가구,선풍기,모든규격,무료,0,구청,2026-09-01,가구류")
    for item in ("책상", "의자"):
        r = t.calculate(R, item)
        assert r["conflict"] and r["reason"] == "inconsistent" and r["fee"] is None and "지자체에 확인" in r["notice"]
    assert "3,000원" in t.calculate(R, "책상")["fee_text"]  # 이전에는 '무료 (0원)'으로 잘못 안내
    ok = t.calculate(R, "선풍기")
    assert ok["is_free"] and not ok["conflict"]
