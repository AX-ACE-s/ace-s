"""공공데이터포털 오픈API 실제 응답(미리보기)에서 발췌한 데이터로 검증한다."""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
from core.fees import FeeError, FeeTable, measure_to_spec, parse_per_unit, parse_range  # noqa: E402
from fetch_fees import ApiError, parse_json, parse_response, parse_xml  # noqa: E402

FIXTURE = (ROOT / "tests" / "fixtures" / "preview.xml").read_text(encoding="utf-8")
JONGNO, NYJ, HS = "서울특별시 종로구", "경기도 남양주시", "충청남도 홍성군"


@pytest.fixture(scope="module")
def table():
    rows, _ = parse_xml(FIXTURE)
    return FeeTable.from_rows(rows)


def test_loads_real_columns(table):
    assert {r["id"] for r in table.regions()} == {JONGNO, NYJ, HS}
    assert not table.is_sample


@pytest.mark.parametrize("spec,kind", [
    ("264㎡ 형 이상", "area"), ("66㎡ 미만", "area"), ("3인용이상", "seats"),
    ("4인용 이하", "seats"), ("50㎝ 미만", "length"), ("높이1m이상", "length"), ("300L 이상", "volume"),
    ("높이 또는 폭(큰수) 1m 이상 ~ 2m 미만", "length"), ("날개 50cm 미만", "length"),
])
def test_real_spec_formats_parse(spec, kind):
    assert parse_range(spec)["kind"] == kind


@pytest.mark.parametrize("spec", ["모든규격", "모든 규격", "업소용", "더블 이상", "좌변기", "없음",
    # 불확실한 표기는 숫자로 바꾸지 않고 선택형으로 남긴다
    "120㎝이상 1쪽", "3인용(폭 1.8m 이하)", "90*100미만", "높이 1m 이상 폭 50cm 미만", "1m 이상 2m 이상"])
def test_non_range_specs(spec):
    assert parse_range(spec) is None


def test_per_unit():
    assert parse_per_unit("1㎡당")["kind"] == "area"
    assert parse_per_unit("모든규격") is None


def test_overlapping_ranges_pick_most_specific(table):
    # 종로구 냉장고: 300L 미만 / 300L 이상 / 500L 이상 (구간이 겹침)
    assert table.calculate(JONGNO, "냉장고", measure=250)["fee"] == 6000
    assert table.calculate(JONGNO, "냉장고", measure=400)["fee"] == 8000
    assert table.calculate(JONGNO, "냉장고", measure=500)["fee"] == 11000
    assert table.calculate(JONGNO, "냉장고", measure=900)["fee"] == 11000
    specs = ["300L 미만", "300L 이상", "500L 이상"]
    assert measure_to_spec(specs, 600, "volume") == "500L 이상"


def test_free_items(table):
    r = table.calculate(JONGNO, "선풍기", measure=40)  # 날개 50cm 미만은 무료
    assert r["fee"] == 0 and r["is_free"] and "무료" in r["fee_text"]
    assert table.calculate(JONGNO, "선풍기", measure=60)["fee"] == 3000
    assert table.calculate(HS, "에어컨(온풍기)", spec="66㎡ 형 이상")["is_free"]


def test_single_spec_item_needs_no_question(table):
    q = table.spec_question(JONGNO, "세탁기")
    assert not q["needs_question"]
    assert table.calculate(JONGNO, "세탁기")["fee"] == 5000


def test_basis_fields_from_real_data(table):
    b = table.calculate(JONGNO, "세탁기")["basis"]
    assert b["data_date"] == "2026-09-07" and b["provider"] == "서울특별시 종로구청" and b["category"] == "가전제품류"


def test_per_unit_area_requires_and_uses_measure(table):
    q = table.spec_question(JONGNO, "문짝")
    assert q["needs_question"] and q["per_unit"] and q["measure_kind"] == "area"
    with pytest.raises(FeeError):
        table.calculate(JONGNO, "문짝")
    r = table.calculate(JONGNO, "문짝", measure=1.5)  # 1㎡당 2,000원, 올림 2단위
    assert r["fee"] == 4000 and "올림" in r["basis"]["calc_note"]


def test_measure_hint(table):
    assert "높이 또는 폭" in table.spec_question(NYJ, "수족관")["measure_hint"]
    assert table.calculate(NYJ, "수족관", measure=150)["fee"] == 6000
    with pytest.raises(FeeError):  # 2m 이상 구간은 데이터에 없음
        table.calculate(NYJ, "수족관", measure=250)


def test_spelling_variants_match(table):
    assert table.match_items(JONGNO, "에어컨")[0]["item"] == "에어콘"  # 데이터상 표기 '에어콘'
    assert table.match_items(JONGNO, "가스오븐렌지")[0]["item"] == "가스오븐레인지"


def test_cart_total_with_free_and_paid(table):
    t = table.calculate_many(JONGNO, [{"item": "세탁기"}, {"item": "선풍기", "spec": "날개 50cm 미만"}])
    assert t["total"] == 5000


# ---------------- 수집 스크립트 파서
def test_parse_xml_response():
    items, total = parse_response(FIXTURE)
    assert len(items) == 46 and items[0]["ctpvNm"] == "서울특별시"


def test_parse_xml_api_error():
    bad = "<response><header><resultCode>30</resultCode><resultMsg>SERVICE KEY IS NOT REGISTERED</resultMsg></header></response>"
    with pytest.raises(ApiError):
        parse_xml(bad)
    with pytest.raises(ApiError):
        parse_xml("<OpenAPI_ServiceResponse><cmmMsgHeader><returnAuthMsg>UNREGISTERED</returnAuthMsg></cmmMsgHeader></OpenAPI_ServiceResponse>")


def test_parse_json_variants():
    flat = {"response": {"header": {"resultCode": "00"}, "body": {"items": [{"a": 1}], "totalCount": 1}}}
    wrapped = {"response": {"header": {"resultCode": "00"}, "body": {"items": [{"item": {"a": 1}}], "totalCount": "1"}}}
    assert parse_json(flat) == ([{"a": 1}], 1) and parse_json(wrapped) == ([{"a": 1}], 1)


def test_parse_json_real_shape_without_response_wrapper():
    """실제 API 응답 구조: response 껍데기 없이 header/body 로 시작, items 가 {"item": [...]}"""
    real = {"header": {"resultCode": "00", "resultMsg": "NORMAL SERVICE."},
            "body": {"items": {"item": [{"ctpvNm": "서울특별시", "sggNm": "종로구", "larWasNm": "변기", "fee": "5000"}]},
                     "numOfRows": "100", "pageNo": "1", "totalCount": "23186"}}
    items, total = parse_response(__import__("json").dumps(real, ensure_ascii=False))
    assert total == 23186 and items[0]["larWasNm"] == "변기"
    single = {"header": {"resultCode": "00"}, "body": {"items": {"item": {"a": 1}}, "totalCount": 1}}
    assert parse_json(single) == ([{"a": 1}], 1)
    with pytest.raises(ApiError):
        parse_json({"header": {"resultCode": "30", "resultMsg": "NOT REGISTERED"}, "body": {}})


# ---------------- 앱의 API 자동 갱신 (실제 호출 없이 가짜 응답으로 검증)
def _rows_from_fixture():
    rows, _ = parse_xml(FIXTURE)
    return rows


def test_app_refresh_from_api_swaps_table(monkeypatch, tmp_path):
    import app as appmod
    from core import api_client
    out = tmp_path / "fees.csv"
    monkeypatch.setattr(api_client, "OUT", out)
    monkeypatch.setattr(appmod.FeeTable, "load_default",
                        classmethod(lambda cls: cls.from_csv_text(out.read_text(encoding="utf-8-sig"))))
    monkeypatch.setattr(appmod.api_client, "fetch_all", lambda *a, **k: _rows_from_fixture())
    monkeypatch.setenv("DATA_GO_KR_KEY", "dummy")
    monkeypatch.setattr(appmod, "table", appmod.table)  # 테스트 후 원복
    appmod.refresh_from_api()
    assert appmod.data_state["source"] == "api" and appmod.data_state["error"] is None
    assert not appmod.table.is_sample and any(r["id"] == "서울특별시 종로구" for r in appmod.table.regions())
    assert out.exists()


def test_app_refresh_failure_keeps_old_data(monkeypatch):
    import app as appmod
    before = appmod.table

    def boom(*a, **k):
        raise SystemExit("API 호출 실패")
    monkeypatch.setattr(appmod.api_client, "fetch_all", boom)
    monkeypatch.setenv("DATA_GO_KR_KEY", "dummy")
    appmod.refresh_from_api()
    assert appmod.table is before and "API 호출 실패" in appmod.data_state["error"]
    assert appmod.data_state["refreshing"] is False


def test_app_refresh_without_key_is_noop(monkeypatch):
    import app as appmod
    monkeypatch.delenv("DATA_GO_KR_KEY", raising=False)
    called = []
    monkeypatch.setattr(appmod.api_client, "fetch_all", lambda *a, **k: called.append(1))
    appmod.refresh_from_api()
    assert not called
