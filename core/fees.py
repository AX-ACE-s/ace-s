"""대형폐기물 수수료 산정 모듈 (필수기능 3: 수수료 산정 및 근거 안내).

입력: 지자체(region_id), 품목, 규격(직접 선택 또는 실측값)
출력: 수수료 금액 + 산정 근거(지역·품목·규격·데이터 기준일·출처)

데이터: 공공데이터포털 '전국대형폐기물수거수수료정보표준데이터'.
 - data/fees.csv 가 있으면 그것을 사용하고, 없으면 시연용 data/fees_sample.csv 를 사용합니다.
 - 컬럼명은 포털 CSV(한글)와 오픈API(ctpvNm 등 영문 태그) 둘 다 인식합니다.
"""
from __future__ import annotations

import csv
import difflib
import io
import json
import math
import re
from datetime import date
from pathlib import Path

from .ordin_links import OrdinLinks
from .errors import FeeError  # noqa: F401  (다른 모듈이 core.fees 에서 가져다 쓴다)
from .specs import (KIND_LABEL, KIND_UNIT, _check_plausible, boundary_check, _normalize_spec, _spec_key, measure_to_spec,  # noqa: F401
                    parse_per_unit, parse_range, spec_hint, spec_kind)

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

COLUMN_ALIASES = {
    "sido": ["시도명", "ctpvNm"],
    "sigungu": ["시군구명", "sggNm"],
    "category": ["대형폐기물구분명", "larWasSeNm"],
    "item": ["대형폐기물명", "larWasNm", "품목명"],
    "spec": ["대형폐기물규격", "larWasSpcfct", "규격"],
    "paid_free": ["유무료여부", "paidFreeYn"],
    "fee": ["수수료", "fee", "수수료(원)"],
    "date": ["데이터기준일자", "crtrYmd", "기준일자"],
    "org": ["관리기관명", "mngInstNm", "제공기관명"],
}

OFFICIAL_SOURCE = "공공데이터포털 전국대형폐기물수거수수료정보표준데이터"
SOURCE_URL = "https://www.data.go.kr/data/15114146/standard.do"


# ---------------------------------------------------------------- 파일 로딩
def _decode(raw: bytes) -> str:
    for enc in ("utf-8-sig", "cp949", "euc-kr"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    raise FeeError("CSV 인코딩을 읽을 수 없습니다(UTF-8 또는 CP949 필요).")


def _pick(row: dict, key: str) -> str:
    for name in COLUMN_ALIASES[key]:
        if name in row and row[name] is not None:
            return str(row[name]).strip()
    return ""


def parse_fee(value: str) -> int | None:
    """'5,000' / '5000원' -> 5000. 숫자가 없으면 None. ('0' 은 0원)"""
    digits = re.sub(r"[^\d]", "", value or "")
    return int(digits) if digits else None


# ---------------------------------------------------------------- 표기 통일
_SPELLING = [("렌지", "레인지"), ("에어콘", "에어컨"), ("쇼파", "소파"), ("텔레비젼", "텔레비전"), ("티비", "텔레비전")]


def normalize_name(text: str) -> str:
    """품목명 비교용: 공백 제거, 소문자, 흔한 표기 차이(에어콘/에어컨 등) 통일."""
    t = re.sub(r"\s+", "", text or "").lower()
    for a, b in _SPELLING:
        t = t.replace(a, b)
    return t


STALE_DAYS = 365  # 데이터 기준일이 이보다 오래되면 현재 금액과 다를 수 있다고 안내


def _stale_note(data_date: str) -> str:
    try:
        days = (date.today() - date.fromisoformat((data_date or "").strip()[:10])).days
    except ValueError:
        return ""
    return (f"데이터 기준일({data_date[:10]})이 {days // 365}년 {days % 365 // 30}개월 이상 지나 현재 금액과 다를 수 있습니다."
            if days > STALE_DAYS else "")


# ---------------------------------------------------------------- 수수료 표
class FeeTable:
    def __init__(self, rows: list[dict], is_sample: bool, meta: dict | None = None):
        self.rows = rows
        self.is_sample = is_sample
        self.meta = meta or {}
        self.ordin = OrdinLinks.load_default()
        self._by_region: dict[str, list[dict]] = {}
        for r in rows:
            self._by_region.setdefault(r["region_id"], []).append(r)

    @classmethod
    def from_rows(cls, raw_rows: list[dict], is_sample: bool = False, meta: dict | None = None) -> "FeeTable":
        rows = []
        for raw in raw_rows:
            sido, sigungu = _pick(raw, "sido"), _pick(raw, "sigungu")
            item, spec = _pick(raw, "item"), _pick(raw, "spec")
            fee = parse_fee(_pick(raw, "fee"))
            if not (sigungu and item) or fee is None:
                continue  # 금액이 없거나 품목이 비어 있는 행은 건너뜀
            rows.append({
                "region_id": f"{sido} {sigungu}".strip(), "sido": sido, "sigungu": sigungu,
                "category": _pick(raw, "category"), "item": item, "spec": spec or "규격 구분 없음",
                "fee": fee, "paid_free": _pick(raw, "paid_free") or ("무료" if fee == 0 else "유료"),
                "data_date": _pick(raw, "date"), "org": _pick(raw, "org"),
            })
        if not rows:
            raise FeeError("수수료 CSV에서 읽을 수 있는 행이 없습니다. 컬럼명을 확인하세요.")
        return cls(rows, is_sample, meta)

    @classmethod
    def from_csv_text(cls, text: str, is_sample: bool = False, meta: dict | None = None) -> "FeeTable":
        return cls.from_rows(list(csv.DictReader(io.StringIO(text))), is_sample, meta)

    @classmethod
    def load_default(cls, data_dir: Path = DATA_DIR) -> "FeeTable":
        real, sample = data_dir / "fees.csv", data_dir / "fees_sample.csv"
        path, is_sample = (real, False) if real.exists() else (sample, True)
        meta_path = data_dir / "item_meta.json"
        meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
        return cls.from_csv_text(_decode(path.read_bytes()), is_sample, meta)

    # --- 조회
    def regions(self) -> list[dict]:
        out = {}
        for r in self.rows:
            out.setdefault(r["region_id"], {"id": r["region_id"], "sido": r["sido"], "sigungu": r["sigungu"]})
        return sorted(out.values(), key=lambda x: x["id"])

    def _region_rows(self, region_id: str) -> list[dict]:
        rows = self._by_region.get(region_id)
        if not rows:
            raise FeeError(f"'{region_id}' 지자체의 수수료 데이터가 없습니다.")
        return rows

    def items(self, region_id: str) -> list[str]:
        return sorted({r["item"] for r in self._region_rows(region_id)})

    def specs(self, region_id: str, item: str) -> list[dict]:
        """품목의 규격 선택지. 공백만 다른 같은 규격은 하나로 묶고, 금액이 서로 다르면 conflict 로 표시한다."""
        rows = [r for r in self._region_rows(region_id) if r["item"] == item]
        if not rows:
            raise FeeError(f"'{region_id}'에는 '{item}' 품목의 수수료 정보가 없습니다.")
        groups: dict[str, dict] = {}
        for r in rows:
            g = groups.setdefault(_spec_key(r["spec"]), {"spec": r["spec"], "rows": []})
            g["rows"].append(r)
        out = []
        for g in groups.values():
            fees = sorted({r["fee"] for r in g["rows"]})
            out.append({"spec": g["spec"], "fee": fees[0] if len(fees) == 1 else None, "fees": fees,
                        "conflict": len(fees) > 1, "paid_free": g["rows"][0]["paid_free"], "rows": g["rows"]})
        return out

    def spec_question(self, region_id: str, item: str) -> dict:
        """'필요한 규격만 질문'하기 위한 질문 정보."""
        specs = self.specs(region_id, item)
        names = [s["spec"] for s in specs]
        per_unit = parse_per_unit(names[0]) if len(names) == 1 else None
        kind = per_unit["kind"] if per_unit else spec_kind(names)
        label = self.meta.get(item, {}).get("measure_label") or KIND_LABEL.get(kind or "", "")
        return {
            "item": item,
            "needs_question": len(specs) > 1 or per_unit is not None,
            "options": names,
            "fees": {s["spec"]: s["fee"] for s in specs},
            "conflicts": [s["spec"] for s in specs if s["conflict"]],
            "measure_kind": kind,
            "measure_label": label,
            "measure_hint": spec_hint(names),
            "per_unit": per_unit["text"] if per_unit else None,
        }

    def match_items(self, region_id: str, query: str, limit: int = 5) -> list[dict]:
        q = normalize_name(query)
        if not q:
            return []
        scored = []
        for name in self.items(region_id):
            aliases = [name] + list(self.meta.get(name, {}).get("aliases", []))
            best = 0.0
            for a in aliases:
                key = normalize_name(a)
                if not key:
                    continue
                if key == q:
                    best = 1.0
                elif key in q or q in key:
                    best = max(best, 0.9)
                else:
                    best = max(best, difflib.SequenceMatcher(None, key, q).ratio())
            if best >= 0.5:
                scored.append({"item": name, "score": round(best, 2)})
        return sorted(scored, key=lambda x: -x["score"])[:limit]

    # --- 핵심: 수수료 산정
    def calculate(self, region_id: str, item: str, spec: str | None = None,
                  measure: float | None = None) -> dict:
        """금액과 산정 근거를 돌려주고, 비교·설명·복사용 문구를 덧붙인다(세부 계산은 _calculate_base)."""
        return self._enrich(self._calculate_base(region_id, item, spec, measure), measure)

    def _enrich(self, res: dict, measure: float | None) -> dict:
        """근거를 '나열'에서 '비교와 설명'으로: 다른 규격과의 차액, 경계값 주의, 쉬운 설명 문장, 복사용 문구."""
        b = res["basis"]
        options = self.specs(b["region"], b["item"])
        by_key = {_spec_key(o["spec"]): o for o in options}
        chosen = by_key[_spec_key(b["spec"])]
        names = [o["spec"] for o in options]
        kind = spec_kind(names)
        unit = KIND_UNIT.get(kind or "", "")
        per_unit = parse_per_unit(b["spec"]) is not None

        # 1) 다른 규격과 비교 (단가형은 총액과 단가를 비교할 수 없어 제외)
        alts = []
        if not per_unit:
            others = [o for o in options if o is not chosen]
            ranges = {o["spec"]: parse_range(o["spec"]) for o in options}
            if others and all(ranges.values()):  # 모두 구간형이면 작은 구간부터 정렬
                others.sort(key=lambda o: (ranges[o["spec"]]["low"] if ranges[o["spec"]]["low"] is not None else -1.0,
                                           ranges[o["spec"]]["high"] if ranges[o["spec"]]["high"] is not None else 1e18))
            for o in others[:8]:
                if o["conflict"]:
                    alts.append({"spec": o["spec"], "fee": None, "conflict": True, "diff": None, "diff_text": "",
                                 "fee_text": f"금액 확인 필요 ({min(o['fees']):,}원 ~ {max(o['fees']):,}원)"})
                    continue
                diff = None if res["fee"] is None else o["fee"] - res["fee"]
                alts.append({"spec": o["spec"], "fee": o["fee"], "conflict": False, "diff": diff,
                             "fee_text": "무료" if o["fee"] == 0 else f"{o['fee']:,}원",
                             "diff_text": "" if diff is None else ("같음" if diff == 0 else f"{diff:+,}원")})
        gaps = [abs(a["diff"]) for a in alts if a["diff"]]
        max_gap = max(gaps) if gaps else 0

        # 2) 경계값 주의 (실측값으로 정한 경우에만)
        boundary = ""
        r_chosen = parse_range(b["spec"])
        if measure is not None and kind and r_chosen and r_chosen["kind"] == kind and not per_unit:
            bc = boundary_check(names, measure, kind)
            if bc:
                other = by_key[_spec_key(bc["other_spec"])]
                other_fee = "금액 확인 필요" if other["conflict"] else f"{other['fee']:,}원"
                gap = ""
                if res["fee"] is not None and not other["conflict"] and other["fee"] != res["fee"]:
                    gap = f" 금액이 {abs(other['fee'] - res['fee']):,}원 달라집니다."
                boundary = (f"입력값 {measure:g}{unit}은(는) 규격 경계 {bc['boundary']:g}{unit}에 가깝습니다. "
                            f"실제 크기가 조금만 달라도 '{bc['other_spec']}'({other_fee})이(가) 될 수 있어요.{gap} 한 번 더 정확히 재 보세요.")

        # 3) 쉬운 설명 문장
        cat = f"({b['category']})" if b.get("category") else ""
        head = f"{b['region']}의 {b['item']}{cat} '{b['spec']}' 규격"
        stale = " " + _stale_note(b["data_date"]) if b.get("is_stale") else ""
        if res["conflict"] and res.get("reason") == "inconsistent":
            explanation = f"{head}은(는) 유료/무료 표기와 등록 금액이 서로 맞지 않아 금액을 확정하지 않았습니다. 지자체에 확인해 주세요.{stale}"
        elif res["conflict"]:
            cands = ", ".join(c["fee_text"] for c in res["candidates"])
            explanation = f"{head}에는 서로 다른 금액({cands})이 등록되어 있어 하나로 정하지 않았습니다. 지자체에 확인해 주세요.{stale}"
        else:
            d = b["decided_by"]
            if d.startswith("실측값"):
                why = f"입력하신 값 {measure:g}{unit}이(가) 이 규격에 해당합니다"
            elif d == "규격이 하나뿐인 품목":
                why = "이 품목은 규격 구분이 하나뿐입니다"
            elif d.startswith("단가"):
                why = b["calc_note"]
            else:
                why = "선택하신 규격입니다"
            tail = f" 다른 규격으로 잘못 신고하면 최대 {max_gap:,}원 차이가 납니다." if max_gap else ""
            explanation = (f"{head}의 수수료는 {res['fee_text']}입니다. {why}. "
                           f"{b['data_date']} 기준 {b['provider']}에서 등록한 금액입니다.{tail}{stale}")

        # 4) 복사용 문구 (신고·문의 때 근거로 붙여 넣기)
        lines = ["[대형폐기물 수수료 안내]", f"지역: {b['region']}", f"품목: {b['item']}{cat}",
                 f"규격: {b['spec']} ({b['decided_by']})", f"예상 수수료: {res['fee_text']}", f"설명: {explanation}"]
        if alts:
            lines.append("다른 규격 금액: " + " / ".join(f"{a['spec']} {a['fee_text']}" for a in alts))
        if boundary:
            lines.append("주의: " + boundary)
        if b.get("ordinance_links"):
            lines.append("조례 별표 원문: " + b["ordinance_links"][0]["url"])
        lines += [f"데이터 기준일: {b['data_date']} (제공기관: {b['provider']})", f"출처: {b['source']} {b['source_url']}", f"※ {res['notice']}"]
        res.update(alternatives=alts, max_gap=max_gap, boundary_warning=boundary,
                   explanation=explanation, copy_text="\n".join(lines))
        return res

    def _calculate_base(self, region_id: str, item: str, spec: str | None = None,
                        measure: float | None = None) -> dict:
        """확정된 지자체·품목·규격으로 수수료와 산정 근거를 반환한다.

        - spec 이 없고 measure(실측값)가 있으면 실측값으로 규격을 자동 결정한다.
        - '1㎡당' 같은 단가형 규격은 measure(면적)가 필요하며 단위 올림으로 추정한다.
        - 같은 규격에 서로 다른 금액이 등록돼 있으면 임의로 고르지 않고 conflict=True 와 후보 금액을 돌려준다.
        """
        options = self.specs(region_id, item)
        by_key = {_spec_key(o["spec"]): o for o in options}
        names = [o["spec"] for o in options]
        decided_by = "사용자 선택"
        if measure is not None and measure <= 0:
            raise FeeError("실측값은 0보다 커야 합니다.")

        if not spec and len(names) == 1:
            spec, decided_by = names[0], "규격이 하나뿐인 품목"
        if not spec and measure is not None:
            kind = spec_kind(names)
            if kind is None:
                raise FeeError("이 품목은 실측값으로 규격을 정할 수 없습니다. 규격을 직접 선택해 주세요.")
            _check_plausible(kind, measure)
            spec = measure_to_spec(names, measure, kind)
            if spec is None:
                raise FeeError("입력한 실측값에 해당하는 규격을 찾지 못했습니다. 규격을 직접 선택해 주세요.")
            decided_by = f"실측값 {measure:g}({KIND_LABEL[kind]}) 기준 자동 판정"
        if not spec:
            raise FeeError("규격을 선택하거나 실측값을 입력해 주세요.")
        chosen = by_key.get(_spec_key(spec))
        if chosen is None:
            raise FeeError(f"'{spec}'은(는) '{item}'의 유효한 규격이 아닙니다.")
        spec = chosen["spec"]
        row = chosen["rows"][0]
        basis = {
            "region": region_id, "item": item, "category": row["category"], "spec": spec,
            "decided_by": decided_by, "calc_note": "",
            "data_date": row["data_date"] or "미기재", "provider": row["org"] or "미기재",
            "source": OFFICIAL_SOURCE, "source_url": SOURCE_URL,
            "is_sample": self.is_sample,
            "ordinance_links": [] if self.is_sample else self.ordin.lookup(region_id),
        }
        notice = ("시연용 샘플 데이터입니다. 실제 금액이 아니므로 실제 신고 전 지자체 공식 안내를 확인하세요."
                  if self.is_sample else
                  "표준데이터 기준 예상 금액이며, 실제 금액은 지자체 신청 시 확정됩니다.")
        stale = _stale_note(basis["data_date"])
        basis["is_stale"] = bool(stale)
        stale_suffix = f" {stale}" if stale else ""
        notice += stale_suffix

        if chosen["conflict"]:  # 같은 규격에 서로 다른 금액 → 임의로 고르지 않는다
            seen, candidates = set(), []
            for r in chosen["rows"]:
                k = (r["fee"], r["category"], r["data_date"], r["org"])
                if k not in seen:
                    seen.add(k)
                    candidates.append({"fee": r["fee"], "fee_text": f"{r['fee']:,}원", "category": r["category"],
                                       "data_date": r["data_date"] or "미기재", "provider": r["org"] or "미기재"})
            lo, hi = min(chosen["fees"]), max(chosen["fees"])
            basis["calc_note"] = "같은 규격에 서로 다른 금액이 등록되어 있어 하나로 확정하지 않았습니다."
            return {"fee": None, "fee_text": f"금액 확인 필요 ({lo:,}원 ~ {hi:,}원)", "is_free": False,
                    "conflict": True, "reason": "duplicate", "candidates": candidates, "basis": basis,
                    "notice": "같은 규격에 서로 다른 금액이 등록되어 있습니다. 지자체에 확인해 주세요." + stale_suffix}

        if (chosen["paid_free"] == "무료" and chosen["fee"] > 0) or (chosen["paid_free"] == "유료" and chosen["fee"] == 0):
            # 유료/무료 표기와 금액이 서로 모순 → 어느 쪽이 맞는지 알 수 없으므로 확정하지 않는다
            basis["calc_note"] = "유료/무료 표기와 등록 금액이 서로 맞지 않아 확정하지 않았습니다."
            cand = [{"fee": chosen["fee"], "fee_text": f"{chosen['fee']:,}원", "category": row["category"],
                     "data_date": row["data_date"] or "미기재", "provider": row["org"] or "미기재"}]
            return {"fee": None, "fee_text": f"금액 확인 필요 (등록값 {chosen['fee']:,}원, '{chosen['paid_free']}' 표기)",
                    "is_free": False, "conflict": True, "reason": "inconsistent", "candidates": cand, "basis": basis,
                    "notice": "유료/무료 표기와 금액이 서로 맞지 않습니다. 지자체에 확인해 주세요." + stale_suffix}

        fee = chosen["fee"]
        per_unit = parse_per_unit(spec)
        if per_unit:
            if measure is None:
                raise FeeError(f"'{spec}' 요금입니다. 크기({KIND_LABEL[per_unit['kind']]})를 입력해 주세요.")
            _check_plausible(per_unit["kind"], measure)
            units = math.ceil(measure / per_unit["size"])
            basis["decided_by"] = f"단가 × 크기 {measure:g}"
            basis["calc_note"] = f"{spec} {fee:,}원 × {units} (단위 올림 기준 추정)"
            fee = fee * units
        free = chosen["paid_free"] == "무료" or fee == 0
        return {"fee": fee, "fee_text": "무료 (0원)" if free else f"{fee:,}원", "is_free": free,
                "conflict": False, "basis": basis, "notice": notice}

    def calculate_many(self, region_id: str, entries: list[dict]) -> dict:
        """여러 품목 합계. 금액이 확정되지 않은(충돌) 품목은 합계에서 빼고 unconfirmed 로 알려 준다."""
        if not entries:
            raise FeeError("산정할 품목이 없습니다.")
        results = [self.calculate(region_id, e["item"], e.get("spec"), e.get("measure")) for e in entries]
        total = sum(r["fee"] for r in results if not r["conflict"])
        unconfirmed = sum(1 for r in results if r["conflict"])
        return {"total": total, "total_text": f"{total:,}원", "unconfirmed": unconfirmed, "results": results}
