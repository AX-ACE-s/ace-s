"""법제처 국가법령정보 공동활용 Open API로 '폐기물' 자치법규(조례)를 받아 저장한다.

사용법(Windows cmd 기준):
    set LAW_OC=본인OC
    python scripts/fetch_ordinances.py --max-pages 2          # 먼저 소량으로 응답 확인
    python scripts/fetch_ordinances.py                         # 전체 목록 수집
    python scripts/fetch_ordinances.py --bodies --limit 20     # 목록 중 20건 본문 수집·요약
    python scripts/fetch_ordinances.py --byl --max-pages 1     # 별표 목록 시험
    python scripts/fetch_ordinances.py --byl                   # 별표 목록 전체
    python scripts/fetch_ordinances.py --byl-bodies --limit 5  # 수수료 표 본문 5건 → byl_tables.csv

결과(모두 data/ordin/ 아래, .gitignore 처리됨):
    list.csv       조례 목록(응답에 있는 모든 필드)
    raw/목록_*.txt  API 원본 응답(형식 확인용. 주소 안의 OC 는 *** 로 가려 저장)
    raw/본문_*.txt  본문 원본 응답(OC 가림)
    snippets.csv   본문에서 '대형폐기물'·'수수료'가 나오는 문장 조각

주의: OC 는 환경변수 LAW_OC 로만 받는다. 코드·저장소·채팅에 넣지 않는다.
응답 형식(XML/JSON, 필드명)을 공식 가이드로 확인하지 못한 채 만들었으므로,
형식이 다르면 raw/ 의 원본을 보고 고친다. 어떤 형식이든 '자치법규명' 필드가 있는 항목을 찾아 읽는다.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import time
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from pathlib import Path

import requests

BASE = "https://www.law.go.kr/DRF"
OUT = Path(__file__).resolve().parent.parent / "data" / "ordin"
NAME_KEYS = ("자치법규명", "자치법규명한글", "별표명", "별표서식명", "서식명")
ID_KEYS = ("자치법규일련번호", "MST", "mst")
KEYWORDS = ("대형폐기물", "수수료")


class FetchError(RuntimeError):
    pass


# ---------------------------------------------------------------- 응답 해석
def _local(tag: str) -> str:
    return tag.split("}")[-1]


def records_from_xml(text: str) -> tuple[list[dict], int | None]:
    """XML 에서 '자치법규명' 자식이 있는 요소를 한 건으로 보고 {태그: 값} 으로 만든다."""
    root = ET.fromstring(text)
    recs = []
    for el in root.iter():
        kids = {_local(c.tag): (c.text or "").strip() for c in el if len(c) == 0}
        if any(k in kids for k in NAME_KEYS):
            recs.append(kids)
    total = None
    for el in root.iter():
        if _local(el.tag).lower() in ("totalcnt", "totalcount") and (el.text or "").strip().isdigit():
            total = int(el.text.strip())
    return recs, total


def records_from_json(text: str) -> tuple[list[dict], int | None]:
    data = json.loads(text)
    recs, total = [], None

    def walk(o):
        nonlocal total
        if isinstance(o, dict):
            if any(k in o for k in NAME_KEYS):
                recs.append({k: ("" if v is None else str(v)) for k, v in o.items() if not isinstance(v, (dict, list))})
            for k, v in o.items():
                if k.lower() in ("totalcnt", "totalcount") and str(v).isdigit():
                    total = int(v)
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(data)
    return recs, total


def parse_list(text: str) -> tuple[list[dict], int | None]:
    t = text.lstrip()
    if t.startswith("{") or t.startswith("["):
        return records_from_json(t)
    return records_from_xml(t)


_OC_RE = re.compile(r"(OC=)[^&\s\"'<>]*", re.I)


def scrub(text: str) -> str:
    """응답 안의 주소(자치법규상세링크 등)에 OC 가 들어 있어, 저장·출력 전에 가린다."""
    return _OC_RE.sub(r"\1***", text)


def strip_tags(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", text)).strip()


def _head(text: str, n: int = 150) -> str:
    return re.sub(r"\s+", " ", text[:n])


def record_id(rec: dict) -> str:
    for k in ID_KEYS:
        if rec.get(k):
            return rec[k]
    return ""


# ---------------------------------------------------------------- 호출
def _get(path: str, params: dict) -> str:
    try:
        r = requests.get(f"{BASE}/{path}", params=params, timeout=30)
    except requests.RequestException as e:  # 메시지에 주소(OC)가 섞일 수 있어 종류만 알린다
        raise FetchError(f"요청 실패({type(e).__name__}). 네트워크를 확인하세요.") from None
    if r.status_code != 200:
        raise FetchError(f"HTTP {r.status_code} — 신청한 IP 와 현재 IP 가 같은지 확인하세요.")
    return r.text


def fetch_list(oc: str, query: str, fmt: str, max_pages: int | None, display: int = 100,
               target: str = "ordin", extra: dict | None = None, prefix: str = "목록"):
    OUT.joinpath("raw").mkdir(parents=True, exist_ok=True)
    rows, page, total = [], 1, None
    while True:
        params = {"OC": oc, "target": target, "type": fmt, "query": query, "display": display, "page": page}
        params.update(extra or {})
        text = _get("lawSearch.do", params)
        OUT.joinpath("raw", f"{prefix}_{page}.txt").write_text(scrub(text), encoding="utf-8")
        try:
            recs, t = parse_list(text)
        except (ET.ParseError, json.JSONDecodeError):
            raise FetchError(f"응답을 읽지 못했습니다. data/ordin/raw/{prefix}_{page}.txt 를 열어 확인하세요 "
                             f"(앞부분: {_head(text)})") from None
        total = t if t is not None else total
        if not recs:
            if page == 1:
                raise FetchError(f"항목이 없습니다. data/ordin/raw/{prefix}_1.txt 의 형식을 확인하세요 "
                                 f"(앞부분: {_head(text)})")
            break
        rows += [{k: strip_tags(scrub(v)) for k, v in r.items()} for r in recs]
        print(f"  목록 {page}쪽: {len(recs)}건 (누적 {len(rows)}{'/' + str(total) if total else ''})")
        if (max_pages and page >= max_pages) or (total and len(rows) >= total):
            break
        page += 1
        time.sleep(0.3)
    return rows


def save_list(rows: list[dict], name: str = "list.csv") -> Path:
    cols = []
    for r in rows:
        for k in r:
            if k not in cols:
                cols.append(k)
    p = OUT / name
    with p.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)
    return p


def snippets(text: str, width: int = 90) -> list[str]:
    plain = re.sub(r"<[^>]+>", " ", text)
    plain = re.sub(r"\s+", " ", plain)
    out, last = [], -10**9
    for m in re.finditer("|".join(KEYWORDS), plain):
        if m.start() - last < width:  # 가까운 중복은 건너뛴다
            continue
        last = m.start()
        out.append(plain[max(0, m.start() - width): m.end() + width])
    return out


def fetch_bodies(oc: str, rows: list[dict], fmt: str, limit: int | None) -> int:
    OUT.joinpath("raw").mkdir(parents=True, exist_ok=True)
    targets = [r for r in rows if record_id(r)][: limit or None]
    n = 0
    with (OUT / "snippets.csv").open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["조례명", "지자체", "MST", "조각"])
        for r in targets:
            mst = record_id(r)
            name = next((r[k] for k in NAME_KEYS if r.get(k)), "")
            org = r.get("지자체기관명", "")
            try:
                text = _get("lawService.do", {"OC": oc, "target": "ordin", "type": fmt, "MST": mst})
            except FetchError as e:
                print(f"  {name}: 건너뜀 ({e})")
                continue
            OUT.joinpath("raw", f"본문_{mst}.txt").write_text(scrub(text), encoding="utf-8")
            for s in snippets(text):
                w.writerow([name, org, mst, s])
            n += 1
            time.sleep(0.3)
    return n


# ---------------------------------------------------------------- 별표(수수료 표) 본문
FEE_NAME = re.compile(r"수수료|부과기준|품목별|기준표")
NOT_FEE_FORM = re.compile(r"납부|영수|대장|환불|신고|판매|스티커|신청서|필증|지정|접수|수불|징수|확인서|계약서|서식")


class _Tables(HTMLParser):
    """HTML 의 <tr> 한 줄을 셀 목록으로 모은다."""

    def __init__(self):
        super().__init__()
        self.rows, self._row, self._cell, self._in = [], None, [], False

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            self._in, self._cell = True, []

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._row is not None and self._in:
            self._row.append(re.sub(r"\s+", " ", "".join(self._cell)).strip())
            self._in = False
        elif tag == "tr" and self._row is not None:
            if any(self._row):
                self.rows.append(self._row)
            self._row = None

    def handle_data(self, data):
        if self._in:
            self._cell.append(data)


def table_rows(html: str) -> list[list[str]]:
    p = _Tables()
    p.feed(html)
    return p.rows


def is_fee_table(name: str) -> bool:
    return bool(FEE_NAME.search(name)) and not NOT_FEE_FORM.search(name)


def fetch_byl_bodies(oc: str, rows: list[dict], limit: int | None) -> tuple[int, int]:
    """별표 본문(HTML)을 받아 raw 로 저장하고, 표의 각 줄을 byl_tables.csv 로 만든다."""
    OUT.joinpath("raw").mkdir(parents=True, exist_ok=True)
    targets = [r for r in rows if is_fee_table(r.get("별표명", "")) and r.get("별표일련번호")][: limit or None]
    n_ok = n_rows = 0
    with (OUT / "byl_tables.csv").open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["지자체", "조례명", "별표명", "별표ID", "줄번호", "셀"])
        for r in targets:
            bid = r["별표일련번호"]
            try:
                text = _get("lawService.do", {"OC": oc, "target": "ordinbyl", "ID": bid, "type": "HTML"})
            except FetchError as e:
                print(f"  {r.get('별표명', '')[:30]}: 건너뜀 ({e})")
                continue
            OUT.joinpath("raw", f"별표본문_{bid}.txt").write_text(scrub(text), encoding="utf-8")
            trs = table_rows(text)
            for i, cells in enumerate(trs, 1):
                w.writerow([r.get("지자체기관명", ""), r.get("관련자치법규명", ""), r.get("별표명", ""), bid, i, " | ".join(cells)])
            n_ok += 1
            n_rows += len(trs)
            time.sleep(0.3)
    return n_ok, n_rows


# ---------------------------------------------------------------- 실행
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="법제처 자치법규(조례) 수집")
    ap.add_argument("--query", default="폐기물", help="조례명 검색어(기본: 폐기물)")
    ap.add_argument("--type", default="XML", choices=["XML", "JSON"], help="응답 형식(기본 XML)")
    ap.add_argument("--max-pages", type=int, default=None, help="목록 쪽수 제한(시험용)")
    ap.add_argument("--byl", action="store_true",
                    help="조례 별표 목록 수집: 별표 본문에 '--byl-query'(기본: 대형폐기물)가 들어간 별표를 찾는다")
    ap.add_argument("--byl-query", default="대형폐기물", help="별표 본문 검색어")
    ap.add_argument("--byl-bodies", action="store_true",
                    help="data/ordin/byl_list.csv 에서 수수료 표(이름에 수수료·부과기준 등, 신고서류 제외) 본문을 받아 표로 정리")
    ap.add_argument("--bodies", action="store_true", help="목록의 조례 본문도 받아 요약")
    ap.add_argument("--limit", type=int, default=None, help="본문을 받을 조례 수 제한")
    ap.add_argument("--title-regex", default=r"폐기물\s*관리", help="본문은 제목이 이 정규식에 맞는 조례만(기본: 폐기물 관리)")
    ap.add_argument("--exclude-regex", default=r"음식물|사업장|해양|영농|처리시설|대행|평가|위원회|과태료|포상",
                    help="제목에 이 말이 들어가면 제외(대형폐기물 수수료와 무관한 조례)")
    ap.add_argument("--region", default="", help="지자체기관명에 이 말이 들어간 조례만(예: 경기도, 성남시)")
    a = ap.parse_args(argv)

    oc = os.environ.get("LAW_OC", "").strip()
    if not oc:
        print("환경변수 LAW_OC 가 없습니다. 예) set LAW_OC=본인OC", file=sys.stderr)
        return 2
    if a.byl_bodies:
        src = OUT / "byl_list.csv"
        if not src.exists():
            print("data/ordin/byl_list.csv 가 없습니다. 먼저 --byl 을 실행하세요.", file=sys.stderr)
            return 1
        rows = list(csv.DictReader(src.open(encoding="utf-8-sig")))
        cands = [r for r in rows if is_fee_table(r.get("별표명", ""))]
        print(f"수수료 표 후보 {len(cands)}건 / 별표 {len(rows)}건")
        try:
            ok, nrows = fetch_byl_bodies(oc, rows, a.limit)
        except FetchError as e:
            print(f"오류: {e}", file=sys.stderr)
            return 1
        print(f"본문 {ok}건, 표 {nrows}줄 저장: {OUT / 'byl_tables.csv'}")
        return 0
    if a.byl:
        try:
            print(f"별표 목록 수집: 본문에 '{a.byl_query}' ({a.type})")
            rows = fetch_list(oc, a.byl_query, a.type, a.max_pages, target="ordinbyl",
                              extra={"search": 3}, prefix="별표목록")
            print(f"별표 {len(rows)}건 저장: {save_list(rows, 'byl_list.csv')}")
            return 0
        except FetchError as e:
            print(f"오류: {e}", file=sys.stderr)
            return 1
    try:
        print(f"조례 목록 수집: '{a.query}' ({a.type})")
        rows = fetch_list(oc, a.query, a.type, a.max_pages)
        p = save_list(rows)
        print(f"목록 {len(rows)}건 저장: {p}")
        if a.bodies:
            def title(r):
                return next((r[k] for k in NAME_KEYS if r.get(k)), "")
            sel = [r for r in rows if re.search(a.title_regex, title(r)) and not re.search(a.exclude_regex, title(r))
                   and a.region in r.get("지자체기관명", "")]
            print(f"본문 수집 대상 {len(sel)}건")
            n = fetch_bodies(oc, sel, a.type, a.limit)
            print(f"본문 {n}건 처리, 조각은 {OUT / 'snippets.csv'}")
    except FetchError as e:
        print(f"오류: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
