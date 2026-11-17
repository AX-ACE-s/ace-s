"""규격 문구 해석·실측 판정 (기능② 규격 질문·실측 보정과의 접점).

수수료 산정(기능③, core/fees.py)은 **확정된 규격**을 입력으로 받는다. 이 모듈은 규격이 확정되기 전의 일,
즉 규격 문구를 구간/단가/선택형으로 해석하고 실측값으로 규격을 고르는 일을 맡는다.
팀의 규격 모듈(기능②)이 있으면 이 파일의 함수들을 그쪽 구현으로 교체하거나 연결하면 된다.
(확실한 경우만 숫자로 해석하고, 불확실한 표기는 선택형으로 남기는 원칙을 따른다.)
"""
from __future__ import annotations

import math
import re

from .errors import FeeError

def _spec_key(spec: str) -> str:
    """공백만 다른 같은 규격('모든규격'/'모든 규격')을 같은 것으로 보기 위한 키."""
    return re.sub(r"\s+", "", spec or "")


def _normalize_spec(spec: str) -> str:
    return (spec or "").replace("㎝", "cm").replace("㎡", "m2").replace("㎏", "kg").replace("ℓ", "l").replace(",", "")


# 길이는 cm 로 환산해 저장. 입력 단위: length=cm, area=㎡, volume=L, weight=kg, inch=인치, seats=인용
_UNIT_KIND = {"cm": ("length", 1.0), "m": ("length", 100.0), "m2": ("area", 1.0), "l": ("volume", 1.0),
              "리터": ("volume", 1.0), "kg": ("weight", 1.0), "인치": ("inch", 1.0), "인용": ("seats", 1.0)}
_UNITS = r"(cm|m2|m|kg|l|리터|인치|인용)"
_BOUND = re.compile(rf"(\d+(?:\.\d+)?)\s*{_UNITS}\s*형?\s*(이상|초과|미만|이하)", re.I)
_PER_UNIT = re.compile(rf"(\d+(?:\.\d+)?)\s*(cm|m2|m)\s*당", re.I)

KIND_LABEL = {"length": "길이(cm)", "area": "면적(㎡)", "volume": "용량(L)", "weight": "무게(kg)",
              "inch": "화면 크기(인치)", "seats": "인원(인용)"}


# 실측값 입력 단위 오류(m↔cm, cm↔인치 등) 방지: 현실적인 범위를 벗어나면 단위를 안내하고 판정하지 않는다.
KIND_UNIT = {"length": "cm", "area": "㎡", "volume": "L", "weight": "kg", "inch": "인치", "seats": "인용"}

PLAUSIBLE = {
    "length": (10, 1000, "cm 단위로 입력해 주세요(예: 1.5m는 150)"),
    "area": (0.1, 1000, "㎡ 단위로 입력해 주세요"),
    "volume": (1, 3000, "리터(L) 단위로 입력해 주세요"),
    "weight": (0.5, 1000, "kg 단위로 입력해 주세요"),
    "inch": (5, 120, "인치 단위로 입력해 주세요(cm가 아닙니다)"),
    "seats": (1, 20, "인원 수를 입력해 주세요"),
}


def _check_plausible(kind: str, value: float) -> None:
    lo, hi, hint = PLAUSIBLE.get(kind, (None, None, ""))
    if lo is not None and not (lo <= value <= hi):
        raise FeeError(f"입력한 값 {value:g}이(가) {KIND_LABEL[kind]} 기준으로 너무 작거나 큽니다. {hint}.")


def parse_range(spec: str) -> dict | None:
    """'1m 이상 2m 미만' -> {kind:'length', low:100, high:200, ...}. 범위형이 아니거나 불확실하면 None.

    숫자로 바꾸는 것은 확실한 경우만 한다. 아래는 해석하지 않고 '선택형'으로 남긴다(잘못된 자동 판정 방지).
      - 구간 표현 밖에 다른 숫자·'*'가 남는 경우: '3인용(폭 1.8m 이하)', '90*100미만', '120㎝이상 1쪽'
      - 같은 방향 경계가 둘 이상이거나 구간이 모순인 경우: '높이 1m 이상 폭 50cm 미만'
    """
    s = _normalize_spec(spec)
    matches = list(_BOUND.finditer(s))
    if not matches or re.search(r"[\d*×]", _BOUND.sub(" ", s)):
        return None
    kind = low = high = None
    low_incl = high_incl = False
    n_low = n_high = 0
    for m in matches:
        num, unit, word = m.groups()
        k, mult = _UNIT_KIND[unit.lower()]
        if kind not in (None, k):
            return None
        kind, v = k, float(num) * mult
        if word in ("이상", "초과"):
            low, low_incl, n_low = v, word == "이상", n_low + 1
        else:
            high, high_incl, n_high = v, word == "이하", n_high + 1
    if n_low > 1 or n_high > 1 or (low is not None and high is not None and low >= high):
        return None
    return {"kind": kind, "low": low, "high": high, "low_incl": low_incl, "high_incl": high_incl}


def parse_per_unit(spec: str) -> dict | None:
    """'1㎡당' -> {kind:'area', size:1.0, text:'1㎡당'}. 면적/길이 단가형 규격."""
    m = _PER_UNIT.search(_normalize_spec(spec))
    if not m:
        return None
    k, mult = _UNIT_KIND[m.group(2).lower()]
    return {"kind": k, "size": float(m.group(1)) * mult, "text": spec.strip()}


def _in_range(value: float, r: dict) -> bool:
    if r["low"] is not None and not (value >= r["low"] if r["low_incl"] else value > r["low"]):
        return False
    if r["high"] is not None and not (value <= r["high"] if r["high_incl"] else value < r["high"]):
        return False
    return True


def measure_to_spec(specs: list[str], value: float, kind: str) -> str | None:
    """실측값이 속하는 규격을 반환. 구간이 겹치면(예: '300L 이상','500L 이상') 가장 좁고 높은 구간을 고른다."""
    hits = []
    for s in specs:
        r = parse_range(s)
        if r and r["kind"] == kind and _in_range(value, r):
            width = (r["high"] - r["low"]) if (r["low"] is not None and r["high"] is not None) else math.inf
            hits.append((width, -(r["low"] if r["low"] is not None else -math.inf), s))
    return min(hits)[2] if hits else None


def spec_kind(specs: list[str]) -> str | None:
    for s in specs:
        r = parse_range(s)
        if r:
            return r["kind"]
    return None


def spec_hint(specs: list[str]) -> str:
    """'높이 또는 폭(큰수)' 처럼 규격 문구에서 측정 기준 설명만 뽑아 안내에 쓴다."""
    for s in specs:
        if parse_range(s):
            hint = _BOUND.sub("", _normalize_spec(s)).replace("~", " ")
            hint = re.sub(r"\s+", " ", hint).strip(" *")
            if hint and not re.fullmatch(r"[\d\s쪽형]*", hint):
                return hint
    return ""


def boundary_check(specs: list[str], value: float, kind: str, tol: float = 0.05) -> dict | None:
    """실측값이 규격 경계에 가까워(기본 ±5%) 조금만 달라져도 다른 규격이 될 수 있는지 판단한다.

    경계를 사이에 둔 반대편 값(거울값)이 다른 규격이면 {boundary, mirror, other_spec} 을 돌려준다.
    """
    chosen = measure_to_spec(specs, value, kind)
    if chosen is None:
        return None
    bounds = set()
    for s in specs:
        r = parse_range(s)
        if r and r["kind"] == kind:
            bounds.update(v for v in (r["low"], r["high"]) if v is not None)
    for b in sorted(bounds, key=lambda x: abs(x - value)):
        if b > 0 and abs(b - value) <= b * tol:
            mirror = 2 * b - value if value != b else b - 1e-9  # 경계와 정확히 같으면 바로 아래쪽을 본다
            other = measure_to_spec(specs, mirror, kind) if mirror > 0 else None
            if other and other != chosen:
                return {"boundary": b, "mirror": b if value == b else round(mirror, 2), "other_spec": other}
    return None
