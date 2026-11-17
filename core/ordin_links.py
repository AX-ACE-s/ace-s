"""조례 별표(수수료 표) 원문 링크.

법제처 자치법규 Open API 의 별표 목록(scripts/build_ordin_links.py 로 가공)에서 만든
data/ordin_links.json 을 읽어, 지자체별로 법제처 별표 보기 주소를 돌려준다.
주소는 별표·조례 일련번호로만 만들며 인증키(OC)는 들어 있지 않다.
금액을 읽거나 비교하지는 않는다(원문으로 가는 링크만 제공).
"""
from __future__ import annotations

import json
import re
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
LINKS_PATH = DATA_DIR / "ordin_links.json"
VIEW_URL = "https://www.law.go.kr/LSW/ordinBylInfoP.do?bylSeq={byl}&ordinSeq={ordin}"

_SIDO = [("서울", "서울"), ("부산", "부산"), ("대구", "대구"), ("인천", "인천"), ("광주", "광주"), ("대전", "대전"),
         ("울산", "울산"), ("세종", "세종"), ("경기", "경기"), ("강원", "강원"), ("충청북", "충북"), ("충북", "충북"),
         ("충청남", "충남"), ("충남", "충남"), ("전라북", "전북"), ("전북", "전북"), ("전라남", "전남"), ("전남", "전남"),
         ("경상북", "경북"), ("경북", "경북"), ("경상남", "경남"), ("경남", "경남"), ("제주", "제주")]


def short_sido(name: str) -> str:
    name = (name or "").strip()
    if name.startswith("전남광주"):
        return "전남광주"
    for prefix, short in _SIDO:
        if name.startswith(prefix):
            return short
    return name


def view_url(byl_seq: str, ordin_seq: str) -> str:
    return VIEW_URL.format(byl=re.sub(r"\D", "", str(byl_seq)), ordin=re.sub(r"\D", "", str(ordin_seq)))


class OrdinLinks:
    def __init__(self, data: dict[str, list[dict]] | None = None):
        self._exact: dict[tuple[str, str], list[dict]] = {}
        for key, links in (data or {}).items():
            parts = key.split()
            if len(parts) < 2 or not links:
                continue
            sido, sigungu = short_sido(parts[0]), " ".join(parts[1:])
            self._exact[(sido, sigungu)] = links

    @classmethod
    def load_default(cls, path: Path = LINKS_PATH) -> "OrdinLinks":
        try:
            return cls(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            return cls({})

    def __len__(self) -> int:
        return len(self._exact)

    def lookup(self, region_id: str) -> list[dict]:
        """'시도 시군구' 로 링크를 찾는다. 시도 표기(전라북도/전북특별자치도 등)만 정규화해 같은 시도의 시군구에만 연결한다."""
        parts = (region_id or "").split()
        if len(parts) < 2:
            return []
        sido, sigungu = short_sido(parts[0]), " ".join(parts[1:])
        hit = self._exact.get((sido, sigungu))
        if hit:
            return hit
        # 통합특별시 표기: 법제처는 '전남광주통합특별시', 표준데이터는 '전라남도'/'광주광역시'일 수 있다.
        # 같은 이름이 다른 시도에 있을 수 있으므로(예: 고성군) 이 두 시도 사이에서만 짝을 맞춘다.
        if sido in ("전남", "광주"):
            return self._exact.get(("전남광주", sigungu), [])
        if sido == "전남광주":
            return self._exact.get(("전남", sigungu)) or self._exact.get(("광주", sigungu)) or []
        return []
