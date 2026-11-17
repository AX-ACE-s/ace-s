"""근거 안내 확장: 다른 규격 비교, 경계값 주의, 설명 문장, 복사 문구."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from core.fees import FeeTable, _stale_note  # noqa: E402
from core.specs import boundary_check  # noqa: E402

HEAD = "ctpvNm,sggNm,larWasNm,larWasSpcfct,paidFreeYn,fee,mngInstNm,crtrYmd,larWasSeNm\n"
R = "서울특별시 가구"


def make(*lines):
    return FeeTable.from_csv_text(HEAD + "\n".join(lines) + "\n")


FRIDGE = ["서울특별시,가구,냉장고,200L 미만,유료,5000,가구구청,2026-10-01,가전",
          "서울특별시,가구,냉장고,200L 이상 400L 미만,유료,8000,가구구청,2026-10-01,가전",
          "서울특별시,가구,냉장고,400L 이상,유료,12000,가구구청,2026-10-01,가전"]


def test_alternatives_show_price_differences():
    t = make(*FRIDGE)
    r = t.calculate(R, "냉장고", spec="200L 이상 400L 미만")
    assert [a["spec"] for a in r["alternatives"]] == ["200L 미만", "400L 이상"]  # 작은 구간부터
    assert [a["diff"] for a in r["alternatives"]] == [-3000, 4000]
    assert [a["diff_text"] for a in r["alternatives"]] == ["-3,000원", "+4,000원"]
    assert r["max_gap"] == 4000
    assert "최대 4,000원 차이" in r["explanation"]


def test_alternatives_flag_conflicting_spec_without_price():
    t = make(FRIDGE[0], FRIDGE[1],
             "서울특별시,가구,냉장고,400L 이상,유료,12000,가구구청,2026-10-01,가전",
             "서울특별시,가구,냉장고,400L 이상,유료,15000,가구구청,2026-10-02,가전")
    r = t.calculate(R, "냉장고", spec="200L 미만")
    bad = [a for a in r["alternatives"] if a["conflict"]][0]
    assert bad["fee"] is None and bad["diff"] is None and "12,000원 ~ 15,000원" in bad["fee_text"]
    assert r["max_gap"] == 3000  # 확정되지 않은 규격은 차액 계산에서 제외


def test_single_spec_has_no_alternatives():
    t = make("서울특별시,가구,의자,1개,유료,2000,가구구청,2026-10-01,가구류")
    r = t.calculate(R, "의자")
    assert r["alternatives"] == [] and r["max_gap"] == 0 and "차이가 납니다" not in r["explanation"]


def test_boundary_warning_near_edge_only():
    t = make(*FRIDGE)
    near = t.calculate(R, "냉장고", measure=395)
    assert near["basis"]["spec"] == "200L 이상 400L 미만"
    assert "400L" in near["boundary_warning"] and "400L 이상" in near["boundary_warning"]
    assert "4,000원 달라집니다" in near["boundary_warning"]
    assert t.calculate(R, "냉장고", measure=300)["boundary_warning"] == ""   # 경계에서 멀면 없음
    assert t.calculate(R, "냉장고", spec="400L 이상")["boundary_warning"] == ""  # 실측 없이 직접 고른 규격
    assert "200L" in t.calculate(R, "냉장고", measure=205)["boundary_warning"]  # 아래 경계 근처도 감지


def test_boundary_exact_value_and_other_side():
    specs = ["200L 미만", "200L 이상 400L 미만", "400L 이상"]
    exact = boundary_check(specs, 400, "volume")
    assert exact and exact["other_spec"] == "200L 이상 400L 미만" and exact["boundary"] == 400
    above = boundary_check(specs, 410, "volume")
    assert above and above["other_spec"] == "200L 이상 400L 미만"
    assert boundary_check(specs, 300, "volume") is None


def test_explanation_normal_case_has_reason_date_provider():
    t = make(*FRIDGE)
    r = t.calculate(R, "냉장고", measure=300)
    e = r["explanation"]
    assert "8,000원" in e and "300L" in e and "2026-10-01" in e and "가구구청" in e


def test_conflict_is_explained_not_decided():
    t = make("서울특별시,가구,소파,3인용,유료,5000,가구구청,2026-01-01,가구류",
             "서울특별시,가구,소파,3인용,유료,8000,가구구청,2026-02-01,가구류")
    r = t.calculate(R, "소파", spec="3인용")
    assert r["fee"] is None and "서로 다른 금액" in r["explanation"] and "5,000원" in r["explanation"]
    assert "예상 수수료: 금액 확인 필요" in r["copy_text"]


def test_inconsistent_paid_free_is_explained():
    t = make("서울특별시,가구,소파,3인용,무료,5000,가구구청,2026-01-01,가구류")
    r = t.calculate(R, "소파", spec="3인용")
    assert r["conflict"] and "맞지 않아" in r["explanation"]


def test_stale_data_is_in_explanation():
    t = make("서울특별시,가구,의자,1개,유료,2000,가구구청,2020-01-01,가구류")
    r = t.calculate(R, "의자")
    assert r["basis"]["is_stale"] and _stale_note("2020-01-01") in r["explanation"]


def test_copy_text_is_self_contained():
    t = make(*FRIDGE)
    r = t.calculate(R, "냉장고", measure=395)
    c = r["copy_text"]
    for must in ["서울특별시 가구", "냉장고", "200L 이상 400L 미만", "8,000원", "다른 규격 금액",
                 "주의:", "2026-10-01", "가구구청", r["notice"]]:
        assert must in c, must
