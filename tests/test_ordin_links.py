import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from core.fees import FeeTable
from core.ordin_links import OrdinLinks, short_sido, view_url
import build_ordin_links as bl

ROWS = [
    {"별표일련번호": "17675595", "관련자치법규일련번호": "1938495", "별표명": "<b>대형</b>폐기물 수집운반처리수수료품목별부과기준",
     "관련자치법규명": "무주군 폐기물관리 및 수수료 부과ㆍ징수에 관한 조례", "전체기관명": "전북특별자치도 무주군", "자치법규시행일자": "20240701"},
    {"별표일련번호": "1", "관련자치법규일련번호": "2", "별표명": "폐기물 처리 신고서 서식", "관련자치법규명": "무주군 폐기물 조례", "전체기관명": "전북특별자치도 무주군"},
    {"별표일련번호": "3", "관련자치법규일련번호": "4", "별표명": "수수료 기준표", "관련자치법규명": "전북교육청 폐기물 조례", "전체기관명": "전북특별자치도교육청"},
    {"별표일련번호": "5", "관련자치법규일련번호": "6", "별표명": "수수료 기준표", "관련자치법규명": "주차장 조례", "전체기관명": "경기도 용인시"},
]


def test_build_filters_and_url():
    out = bl.build(ROWS)
    assert list(out) == ["전북특별자치도 무주군"]
    l = out["전북특별자치도 무주군"][0]
    assert l["url"] == "https://www.law.go.kr/LSW/ordinBylInfoP.do?bylSeq=17675595&ordinSeq=1938495"
    assert "<" not in l["name"] and "OC" not in l["url"]


def test_view_url_digits_only():
    assert view_url("12&x=1", "34") == "https://www.law.go.kr/LSW/ordinBylInfoP.do?bylSeq=121&ordinSeq=34"


def test_sido_alias_lookup():
    o = OrdinLinks({"전북특별자치도 무주군": [{"name": "a", "url": "u"}], "강원특별자치도 고성군": [{"name": "g", "url": "g"}],
                    "전남광주통합특별시 영암군": [{"name": "y", "url": "y"}]})
    assert short_sido("전라북도") == "전북" and short_sido("충청남도") == "충남"
    assert o.lookup("전라북도 무주군")[0]["url"] == "u"       # 시도 표기만 다른 경우(개명)는 연결
    assert o.lookup("경상남도 고성군") == []                    # 같은 이름이라도 다른 시도면 연결하지 않음
    assert o.lookup("강원도 고성군")[0]["url"] == "g"
    assert o.lookup("전라남도 영암군")[0]["url"] == "y"         # 전남광주통합특별시 표기 차이
    assert o.lookup("없는시") == [] and o.lookup("") == []


def test_real_links_file_is_clean():
    p = ROOT / "data" / "ordin_links.json"
    d = json.loads(p.read_text(encoding="utf-8"))
    assert d
    for org, links in d.items():
        assert "교육" not in org and links
        for l in links:
            assert re.fullmatch(r"https://www\.law\.go\.kr/LSW/ordinBylInfoP\.do\?bylSeq=\d+&ordinSeq=\d+", l["url"])


def test_basis_has_links_for_real_data_only():
    csv_text = "시도명,시군구명,대형폐기물명,대형폐기물규격,수수료,데이터기준일자,제공기관명\n전북특별자치도,무주군,장롱,1m 미만,5000,2026-10-01,무주군\n"
    t = FeeTable.from_csv_text(csv_text, is_sample=False)
    t.ordin = OrdinLinks({"전북특별자치도 무주군": [{"name": "별표", "url": "https://www.law.go.kr/x"}]})
    r = t.calculate("전북특별자치도 무주군", "장롱")
    assert r["basis"]["ordinance_links"][0]["url"] == "https://www.law.go.kr/x"
    assert "조례 별표 원문: https://www.law.go.kr/x" in r["copy_text"]
    t.is_sample = True
    assert t.calculate("전북특별자치도 무주군", "장롱")["basis"]["ordinance_links"] == []
