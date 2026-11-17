"""사진 기반 품목 인식 (계획서 필수기능 1).

사진 한 장을 AI(Claude 비전)에 보내 '해당 지자체 수수료표에 있는 품목' 중에서만 후보를 고르게 한다.
 - 후보는 반드시 수수료표의 품목명이어야 하며, 목록에 없는 이름은 버린다(AI가 지어낸 품목 방지).
 - 인식 결과는 '추천'일 뿐이며, 사용자가 확인하거나 직접 검색·선택해 바꿀 수 있다.
 - 사진은 저장하지 않고 인식 요청에만 사용한다. 키는 환경변수 ANTHROPIC_API_KEY 로만 받는다.
"""
from __future__ import annotations

import base64
import json
import os
import re

import requests

from .fees import normalize_name

API_URL = "https://api.anthropic.com/v1/messages"
DEFAULT_MODEL = "claude-sonnet-5-5"
MAX_IMAGE_BYTES = 5 * 1024 * 1024  # AI 이미지 입력 한도
MAX_CANDIDATES = 3
MAX_ITEMS_IN_PROMPT = 600


def sniff_media_type(data: bytes) -> str | None:
    """파일 앞부분(매직 넘버)으로 이미지 형식을 판별한다. 확장자·클라이언트 값은 믿지 않는다."""
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    return None


def is_available() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def build_prompt(items: list[str]) -> str:
    names = "\n".join(f"- {n}" for n in items[:MAX_ITEMS_IN_PROMPT])
    return (
        "사진에 보이는 대형폐기물(가구·가전 등)이 아래 '품목 목록' 중 무엇인지 골라 주세요.\n"
        "규칙:\n"
        "1. item 은 반드시 목록에 있는 이름을 글자 그대로 써야 합니다. 목록에 없으면 후보에 넣지 마세요.\n"
        "2. 가능성이 높은 순서로 최대 3개, 확신이 낮으면 적게 또는 빈 배열로 답하세요.\n"
        "3. evidence 에는 사진에서 실제로 관찰한 특징(모양·구성·재질 등)만 한 문장으로 쓰세요.\n"
        "4. 사람의 얼굴이나 개인정보는 언급하지 마세요.\n"
        '5. 설명 없이 JSON 만 출력: {"candidates":[{"item":"...","confidence":0.0~1.0,"evidence":"..."}],"observations":"사진 한 줄 설명"}\n\n'
        f"품목 목록:\n{names}"
    )


def parse_candidates(text: str, items: list[str]) -> tuple[list[dict], str]:
    """AI 응답 텍스트에서 후보를 꺼내고, 수수료표에 없는 품목은 버린다."""
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        return [], ""
    try:
        data = json.loads(m.group(0))
    except ValueError:
        return [], ""
    exact = set(items)
    by_norm = {normalize_name(n): n for n in items}
    out, seen = [], set()
    for c in (data.get("candidates") or [])[:10]:
        if not isinstance(c, dict):
            continue
        name = str(c.get("item", "")).strip()
        name = name if name in exact else by_norm.get(normalize_name(name))
        if not name or name in seen:
            continue
        seen.add(name)
        try:
            conf = max(0.0, min(1.0, float(c.get("confidence", 0))))
        except (TypeError, ValueError):
            conf = 0.0
        out.append({"item": name, "confidence": round(conf, 2), "evidence": str(c.get("evidence", ""))[:150]})
    out.sort(key=lambda x: -x["confidence"])
    return out[:MAX_CANDIDATES], str(data.get("observations", ""))[:150]


def identify(image: bytes, media_type: str, items: list[str]) -> dict:
    """반환: {available, candidates:[{item,confidence,evidence}], observations, message}"""
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        return {"available": False, "candidates": [], "observations": "",
                "message": "AI 사진 인식이 설정되지 않았습니다. 아래에서 품목을 직접 검색하거나 선택해 주세요."}
    body = {
        "model": os.environ.get("ANTHROPIC_MODEL", DEFAULT_MODEL),
        "max_tokens": 700,
        "messages": [{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": media_type,
                                         "data": base64.b64encode(image).decode()}},
            {"type": "text", "text": build_prompt(items)},
        ]}],
    }
    try:
        res = requests.post(API_URL, json=body, timeout=45,
                            headers={"x-api-key": key, "anthropic-version": "2023-06-01"})
        res.raise_for_status()
        text = "".join(b.get("text", "") for b in res.json().get("content", []) if b.get("type") == "text")
    except (requests.RequestException, ValueError) as e:  # 키·본문이 오류 메시지에 섞이지 않게 형식만 알린다
        return {"available": True, "candidates": [], "observations": "",
                "message": f"AI 인식 요청에 실패했습니다({type(e).__name__}). 직접 검색해 주세요."}
    candidates, observations = parse_candidates(text, items)
    message = "" if candidates else "사진에서 맞는 품목을 찾지 못했습니다. 아래에서 직접 검색하거나 선택해 주세요."
    return {"available": True, "candidates": candidates, "observations": observations, "message": message}
