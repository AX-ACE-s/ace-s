import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import app  # noqa: E402
from core.fees import FeeError, FeeTable, measure_to_spec, parse_fee, parse_range  # noqa: E402

R = "서울특별시 시연구"


@pytest.fixture(scope="module")
def table():
    return FeeTable.load_default()


@pytest.fixture()
def client():
    return app.test_client()


def test_parse_fee():
    assert parse_fee("5,000") == 5000 and parse_fee("8000원") == 8000 and parse_fee("") is None


def test_parse_range_variants():
    assert parse_range("1m 미만") == {"kind": "length", "low": None, "high": 100.0, "low_incl": False, "high_incl": False}
    r = parse_range("1m 이상 2m 미만")
    assert (r["low"], r["high"], r["low_incl"]) == (100.0, 200.0, True)
    assert parse_range("400L 이상")["kind"] == "volume"
    assert parse_range("1인용") is None


@pytest.mark.parametrize("cm,expected", [(99, "1m 미만"), (100, "1m 이상 2m 미만"), (199.9, "1m 이상 2m 미만"), (200, "2m 이상")])
def test_measure_boundaries(cm, expected):
    specs = ["1m 미만", "1m 이상 2m 미만", "2m 이상"]
    assert measure_to_spec(specs, cm, "length") == expected


def test_calculate_with_basis(table):
    r = table.calculate(R, "장롱", spec="1m 이상 2m 미만")
    assert r["fee"] == 8000
    b = r["basis"]
    assert b["region"] == R and b["item"] == "장롱" and b["data_date"] == "2026-10-01" and b["source_url"].startswith("https://")
    assert b["is_sample"] is True and "샘플" in r["notice"]
    assert "ordinance_url" not in b  # 검색 링크는 쓰지 않는다(일련번호 링크는 ordinance_links)


def test_calculate_by_measure(table):
    r = table.calculate(R, "장롱", measure=150)
    assert r["basis"]["spec"] == "1m 이상 2m 미만" and "실측" in r["basis"]["decided_by"]
    assert table.calculate(R, "냉장고", measure=450)["fee"] == 12000  # 400L 이상


def test_regions_differ(table):
    assert table.calculate("경기도 예시시", "장롱", spec="1m 미만")["fee"] != table.calculate(R, "장롱", spec="1m 미만")["fee"]


def test_errors(table):
    for args in [("없는구", "장롱", "1m 미만", None), (R, "우주선", None, None), (R, "장롱", "엉뚱규격", None),
                 (R, "장롱", None, None), (R, "장롱", None, -5), (R, "의자", None, 100)]:  # 의자: 범위형 규격이 없어 실측 불가
        with pytest.raises(FeeError):
            table.calculate(*args[:3], measure=args[3])


def test_spec_question(table):
    q = table.spec_question(R, "냉장고")
    assert q["needs_question"] and q["measure_kind"] == "volume" and "용량" in q["measure_label"]
    assert table.spec_question(R, "의자")["measure_kind"] is None
    assert table.spec_question(R, "침대")["measure_kind"] == "seats"  # "2인용 이상" 은 인원 범위로 해석


def test_match_items(table):
    assert table.match_items(R, "낡은 쇼파 버릴래요")[0]["item"] == "소파"
    assert table.match_items(R, "TV")[0]["item"] == "텔레비전"
    assert table.match_items(R, "냉쟝고")[0]["item"] == "냉장고"  # 오타 허용
    assert table.match_items(R, "ㅋㅋㅋㅋ") == []


def test_total(table):
    t = table.calculate_many(R, [{"item": "장롱", "spec": "1m 미만"}, {"item": "침대", "spec": "1인용"}])
    assert t["total"] == 11000


def test_csv_loader_cp949_and_skips_bad_rows():
    text = "시도명,시군구명,대형폐기물명,대형폐기물규격,수수료\n서울,가구,책장,1m 미만,\"3,000\"\n서울,가구,책장,1m 이상,\n"
    t = FeeTable.from_csv_text(text)
    assert len(t.rows) == 1 and t.rows[0]["fee"] == 3000
    with pytest.raises(FeeError):
        FeeTable.from_csv_text("a,b\n1,2\n")


def test_api_flow(client):
    assert client.get("/api/health").json["ok"]
    regions = client.get("/api/regions").json["regions"]
    assert len(regions) >= 2
    assert "장롱" in client.get(f"/api/items?region={R}").json["items"]
    assert client.get(f"/api/items?region={R}&q=옷장").json["matches"][0]["item"] == "장롱"
    r = client.post("/api/fee", json={"region": R, "item": "장롱", "measure": "150"})
    assert r.status_code == 200 and r.json["fee"] == 8000
    bad = client.post("/api/fee", json={"region": R, "item": "장롱", "measure": "abc"})
    assert bad.status_code == 400 and "숫자" in bad.json["error"]
    assert client.get("/api/items?region=없음").status_code == 400
    assert client.get("/").status_code == 200


def test_api_error_never_shows_service_key():
    from core import api_client
    msg = "403 Client Error: Forbidden for url: https://api.data.go.kr/x?serviceKey=SECRETKEY%3D&pageNo=1"
    out = api_client.scrub(msg)
    assert "SECRETKEY" not in out and "serviceKey=***" in out and "pageNo=1" in out
