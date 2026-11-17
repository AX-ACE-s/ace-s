"""공공데이터포털 대형폐기물 수수료 오픈API 클라이언트 (앱·수집 스크립트 공용).

- 응답은 JSON/XML 둘 다 처리한다.
- 인증키는 환경변수 DATA_GO_KR_KEY 로만 받는다(코드·저장소에 넣지 않는다).
"""
from __future__ import annotations

import csv
import json
import re
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import requests

OUT = Path(__file__).resolve().parent.parent / "data" / "fees.csv"
PAGE_SIZE = 100
DEFAULT_URL = "https://api.data.go.kr/openapi/tn_pubr_public_lar_was_fee_api"  # 공공데이터포털 서비스정보의 End Point


class ApiError(RuntimeError):
    pass


def parse_xml(text: str) -> tuple[list[dict], int]:
    root = ET.fromstring(text[text.index("<"):])  # 앞쪽 잡문구 방지
    code = root.findtext(".//resultCode")
    if code is not None and code != "00":
        raise ApiError(f"API 오류 {code}: {root.findtext('.//resultMsg')}")
    if root.find(".//body") is None:  # OpenAPI_ServiceResponse 형태의 오류
        raise ApiError("API 오류: " + (root.findtext(".//returnAuthMsg") or root.findtext(".//errMsg") or text[:200]))
    items = [{c.tag: (c.text or "").strip() for c in it} for it in root.iter("item")]
    return items, int(root.findtext(".//totalCount") or 0)


def parse_json(payload: dict) -> tuple[list[dict], int]:
    """JSON 응답 해석. 실제 응답은 {"header":..,"body":..} 로 바로 시작하고,
    일부 API 는 {"response":{"header":..,"body":..}} 로 한 겹 감싼다. 둘 다 처리한다."""
    root = payload.get("response", payload)
    header = root.get("header", {})
    if header.get("resultCode") not in (None, "00"):
        raise ApiError(f"API 오류 {header.get('resultCode')}: {header.get('resultMsg')}")
    body = root.get("body", {})
    items = body.get("items", [])
    if isinstance(items, dict):  # {"item": [...]} 또는 {"item": {...}}(1건)
        items = items.get("item", [])
    if isinstance(items, dict):
        items = [items]
    items = [i["item"] if isinstance(i, dict) and set(i) == {"item"} else i for i in items]
    return items, int(body.get("totalCount", 0) or 0)


def parse_response(text: str) -> tuple[list[dict], int]:
    return parse_xml(text) if text.lstrip().startswith("<") else parse_json(json.loads(text))


_KEY_RE = re.compile(r"(serviceKey=)[^&\s\"'<>]*", re.I)


def scrub(text: str) -> str:
    """오류 문구에 들어간 인증키(serviceKey=...)를 가린다."""
    return _KEY_RE.sub(r"\1***", str(text))


def _request(url: str, params: dict) -> tuple[list[dict], int, str]:
    res = requests.get(url, params=params, timeout=30)
    res.raise_for_status()
    items, total = parse_response(res.text)
    return items, total, res.text


def fetch_all(url: str, key: str, extra: dict, max_pages: int | None = None, debug: bool = False, progress=None) -> list[dict]:
    rows, page, total = [], 1, None
    use_json = True  # 첫 응답이 비어 있으면 XML(type 생략)로 자동 전환
    while (total is None or len(rows) < total) and (max_pages is None or page <= max_pages):
        base = {"serviceKey": key, "pageNo": page, "numOfRows": PAGE_SIZE, **extra}
        for attempt in range(3):
            try:
                params = {**base, "type": "json"} if use_json else base
                items, total, raw = _request(url, params)
                if debug and page == 1:
                    print(f"[debug] 형식={'json' if use_json else 'xml'} totalCount={total} 응답 앞부분:\n{raw[:500]}\n")
                if not items and page == 1 and use_json:
                    use_json = False  # JSON 으로는 비어 있음 → XML 로 재시도
                    print("JSON 응답이 비어 있어 XML 형식으로 다시 시도합니다.")
                    continue
                break
            except ApiError as e:
                raise SystemExit(str(e))
            except (requests.RequestException, ValueError, ET.ParseError) as e:
                if attempt == 2:
                    hint = ("\n인증키가 올바른지 확인하세요. 공공데이터포털 마이페이지의 '일반 인증키'(긴 영문·숫자 문자열)를 쓰고, "
                            "활용신청이 승인된 상태여야 합니다.") if "403" in str(e) or "401" in str(e) else ""
                    raise SystemExit(f"API 호출 실패(페이지 {page}): {type(e).__name__}: {scrub(e)[:150]}{hint}")
                time.sleep(2)
        if not items:
            if page == 1:
                print(f"[안내] 응답에 데이터가 없습니다. totalCount={total}\n응답 앞부분:\n{raw[:500]}")
            break
        rows.extend(items)
        if progress: progress(len(rows), total)
        page += 1
        time.sleep(0.1)
    return rows




def save_csv(rows: list[dict], out: Path | None = None) -> list[str]:
    """임시 파일에 쓴 뒤 교체한다(쓰는 도중 앱이 반쪽 파일을 읽지 않도록)."""
    out = out or OUT
    fields = list(dict.fromkeys(k for r in rows for k in r))
    out.parent.mkdir(exist_ok=True)
    tmp = out.with_suffix(".tmp")
    with tmp.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    tmp.replace(out)
    return fields
