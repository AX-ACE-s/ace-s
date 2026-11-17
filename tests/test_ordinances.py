"""조례 수집 스크립트: 실제 호출 없이 응답 해석·요약·안전장치를 확인한다(가짜 응답 사용)."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import fetch_ordinances as fo  # noqa: E402

XML = """<?xml version="1.0" encoding="UTF-8"?>
<OrdinSearch><totalCnt>2</totalCnt>
 <law id="1"><자치법규일련번호>111</자치법규일련번호><자치법규명>성남시 폐기물 관리에 관한 조례</자치법규명><지자체기관명>경기도 성남시</지자체기관명></law>
 <law id="2"><자치법규일련번호>222</자치법규일련번호><자치법규명>시흥시 폐기물관리 조례</자치법규명><지자체기관명>경기도 시흥시</지자체기관명></law>
</OrdinSearch>"""

JSON = ('{"OrdinSearch":{"totalCnt":"1","law":[{"자치법규일련번호":"333","자치법규명":"청주시 폐기물관리에 관한 조례",'
        '"지자체기관명":"충청북도 청주시"}]}}')


def test_parse_xml_records_and_total():
    recs, total = fo.parse_list(XML)
    assert total == 2 and [r["자치법규일련번호"] for r in recs] == ["111", "222"]
    assert recs[0]["지자체기관명"] == "경기도 성남시"


def test_parse_json_records_and_total():
    recs, total = fo.parse_list(JSON)
    assert total == 1 and recs[0]["자치법규명"].startswith("청주시") and fo.record_id(recs[0]) == "333"


def test_unrelated_response_has_no_records():
    recs, _ = fo.parse_list("<error><msg>사용자 정보 검증에 실패하였습니다.</msg></error>")
    assert recs == []


def test_snippets_find_keywords_and_skip_nearby_duplicates():
    body = "<본문>제5조 대형폐기물의 수수료는 별표 3과 같다. " + "가" * 300 + " 수수료는 5천원이다.</본문>"
    out = fo.snippets(body)
    assert len(out) == 2 and "대형폐기물" in out[0] and "수수료" in out[1]
    assert fo.snippets("<a>관련 없는 내용</a>") == []


def test_missing_oc_stops_without_calling(monkeypatch, capsys):
    monkeypatch.delenv("LAW_OC", raising=False)
    assert fo.main([]) == 2
    assert "LAW_OC" in capsys.readouterr().err


def test_error_messages_never_contain_oc(monkeypatch, capsys):
    monkeypatch.setenv("LAW_OC", "secretoc123")

    def boom(*a, **k):
        raise fo.requests.ConnectionError("https://www.law.go.kr/DRF/lawSearch.do?OC=secretoc123")
    monkeypatch.setattr(fo.requests, "get", boom)
    assert fo.main(["--max-pages", "1"]) == 1
    err = capsys.readouterr().err
    assert "secretoc123" not in err and "ConnectionError" in err


def test_fetch_list_with_fake_api(monkeypatch, tmp_path):
    monkeypatch.setattr(fo, "OUT", tmp_path)
    pages = {1: XML, 2: "<OrdinSearch><totalCnt>2</totalCnt></OrdinSearch>"}
    monkeypatch.setattr(fo, "_get", lambda path, params: pages[params["page"]])
    monkeypatch.setattr(fo.time, "sleep", lambda s: None)
    rows = fo.fetch_list("x", "폐기물", "XML", None)
    assert len(rows) == 2
    p = fo.save_list(rows)
    assert "성남시 폐기물 관리에 관한 조례" in p.read_text(encoding="utf-8-sig")
    assert (tmp_path / "raw" / "목록_1.txt").exists()


def test_fetch_bodies_writes_snippets(monkeypatch, tmp_path):
    monkeypatch.setattr(fo, "OUT", tmp_path)
    monkeypatch.setattr(fo.time, "sleep", lambda s: None)
    monkeypatch.setattr(fo, "_get", lambda path, params: "<본문>대형폐기물 수수료는 별표와 같다</본문>")
    rows, _ = fo.parse_list(XML)
    assert fo.fetch_bodies("x", rows, "XML", 1) == 1
    text = (tmp_path / "snippets.csv").read_text(encoding="utf-8-sig")
    assert "성남시 폐기물 관리에 관한 조례" in text and "대형폐기물" in text
    assert (tmp_path / "raw" / "본문_111.txt").exists()


def test_scrub_removes_oc_from_links_and_saved_files(monkeypatch, tmp_path):
    link = "/DRF/lawService.do?OC=myoc123&target=ordin&MST=1&type=HTML"
    assert "myoc123" not in fo.scrub(link) and "OC=***" in fo.scrub(link) and "MST=1" in fo.scrub(link)
    xml = ("<OrdinSearch><totalCnt>1</totalCnt><law><자치법규일련번호>9</자치법규일련번호><자치법규명>가 폐기물 관리 조례</자치법규명>"
           "<자치법규상세링크>" + link.replace("&", "&amp;") + "</자치법규상세링크></law></OrdinSearch>")
    monkeypatch.setattr(fo, "OUT", tmp_path)
    monkeypatch.setattr(fo, "_get", lambda path, params: xml)
    rows = fo.fetch_list("myoc123", "폐기물", "XML", None)
    p = fo.save_list(rows)
    saved = p.read_text(encoding="utf-8-sig") + (tmp_path / "raw" / "목록_1.txt").read_text(encoding="utf-8")
    assert "myoc123" not in saved and "OC=***" in saved


def test_byl_list_uses_ordinbyl_target_and_search3(monkeypatch, tmp_path):
    monkeypatch.setattr(fo, "OUT", tmp_path)
    seen = {}

    def fake(path, params):
        seen.update(params)
        return ("<OrdinBylSearch><totalCnt>1</totalCnt><ordinbyl><별표서식명>대형폐기물 수수료</별표서식명>"
                "<자치법규명>가평군 폐기물 관리에 관한 조례</자치법규명></ordinbyl></OrdinBylSearch>")
    monkeypatch.setattr(fo, "_get", fake)
    rows = fo.fetch_list("x", "대형폐기물", "XML", None, target="ordinbyl", extra={"search": 3}, prefix="별표목록")
    assert seen["target"] == "ordinbyl" and seen["search"] == 3 and "mobileYn" not in seen
    assert rows and rows[0]["별표서식명"] == "대형폐기물 수수료"
    assert (tmp_path / "raw" / "별표목록_1.txt").exists()


HTML_TABLE = """<html><body><table>
<tr><th>품목</th><th>규격</th><th>수수료</th></tr>
<tr><td>냉장고</td><td>400L 이상</td><td>10,000</td></tr>
<tr><td>장롱</td><td>1m 이상 <b>2m</b> 미만</td><td>8,000</td></tr>
</table></body></html>"""


def test_table_rows_extracts_cells():
    rows = fo.table_rows(HTML_TABLE)
    assert rows[0] == ["품목", "규격", "수수료"]
    assert rows[1] == ["냉장고", "400L 이상", "10,000"] and rows[2][1] == "1m 이상 2m 미만"


def test_is_fee_table_picks_tables_not_forms():
    assert fo.is_fee_table("(별표 2) 대형폐기물 등의 품목별 수수료 기준표")
    assert fo.is_fee_table("대형폐기물 수집운반처리수수료품목별부과기준")
    assert not fo.is_fee_table("[별지 제2호서식] 대형폐기물수수료 납부필증")
    assert not fo.is_fee_table("[별지 제10호서식] 대형폐기물 배출신고서")


def test_fetch_byl_bodies_writes_table_csv(monkeypatch, tmp_path):
    monkeypatch.setattr(fo, "OUT", tmp_path)
    monkeypatch.setattr(fo.time, "sleep", lambda s: None)
    seen = {}
    monkeypatch.setattr(fo, "_get", lambda path, params: (seen.update(params) or HTML_TABLE))
    rows = [{"별표일련번호": "20336379", "별표명": "(별표 2) 대형폐기물 등의 품목별 수수료 기준표",
             "관련자치법규명": "용인시 폐기물 관리에 관한 조례", "지자체기관명": "용인시"},
            {"별표일련번호": "1", "별표명": "[별지 제1호서식] 대형폐기물 배출신고서", "지자체기관명": "x"}]
    assert fo.fetch_byl_bodies("x", rows, None) == (1, 3)
    assert seen["target"] == "ordinbyl" and seen["ID"] == "20336379" and seen["type"] == "HTML"
    text = (tmp_path / "byl_tables.csv").read_text(encoding="utf-8-sig")
    assert "용인시" in text and "냉장고 | 400L 이상 | 10,000" in text
