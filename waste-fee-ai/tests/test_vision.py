import io
import sys
from pathlib import Path

import pytest
import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import app as appmod  # noqa: E402
from core import vision  # noqa: E402

JPEG = b"\xff\xd8\xff\xe0" + b"0" * 64
ITEMS = ["에어콘", "냉장고", "세탁기", "소파"]


def test_sniff_media_type():
    assert vision.sniff_media_type(JPEG) == "image/jpeg"
    assert vision.sniff_media_type(b"\x89PNG\r\n\x1a\n....") == "image/png"
    assert vision.sniff_media_type(b"RIFF1234WEBPVP8 ") == "image/webp"
    assert vision.sniff_media_type(b"GIF89a....") == "image/gif"
    assert vision.sniff_media_type(b"<html>not an image</html>") is None


def test_parse_candidates_filters_hallucinations_and_maps_spelling():
    text = '''```json
    {"candidates":[
      {"item":"냉장고","confidence":0.9,"evidence":"문이 두 개인 직육면체"},
      {"item":"우주선","confidence":0.8,"evidence":"목록에 없음"},
      {"item":"에어컨","confidence":0.4,"evidence":"표기 차이"},
      {"item":"냉장고","confidence":0.3,"evidence":"중복"},
      {"item":"소파","confidence":7,"evidence":"범위 초과 신뢰도"}
    ],"observations":"주방 가전"}
    ```'''
    cands, obs = vision.parse_candidates(text, ITEMS)
    assert [c["item"] for c in cands] == ["소파", "냉장고", "에어콘"]  # 목록 밖 항목·중복 제거, 에어컨→에어콘
    assert cands[0]["confidence"] == 1.0 and obs == "주방 가전"


@pytest.mark.parametrize("bad", ["", "설명만 있고 JSON 없음", "{깨진 json", '{"candidates": "문자열"}'])
def test_parse_candidates_bad_output(bad):
    assert vision.parse_candidates(bad, ITEMS)[0] == []


def test_parse_candidates_capped_at_three():
    many = ",".join(f'{{"item":"{n}","confidence":0.{i + 1}}}' for i, n in enumerate(ITEMS))
    assert len(vision.parse_candidates('{"candidates":[%s]}' % many, ITEMS)[0]) == 3


def test_identify_without_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    r = vision.identify(JPEG, "image/jpeg", ITEMS)
    assert r["available"] is False and r["candidates"] == [] and "직접" in r["message"]


class FakeResp:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self.payload


def test_identify_with_key_sends_image_and_item_list(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "dummy-key")
    seen = {}

    def fake_post(url, json, timeout, headers):
        seen.update(url=url, body=json, headers=headers)
        return FakeResp({"content": [{"type": "text", "text": '{"candidates":[{"item":"냉장고","confidence":0.8,"evidence":"두 칸 문"}]}'}]})
    monkeypatch.setattr(vision.requests, "post", fake_post)
    r = vision.identify(JPEG, "image/jpeg", ITEMS)
    assert r["available"] and r["candidates"][0]["item"] == "냉장고" and r["message"] == ""
    content = seen["body"]["messages"][0]["content"]
    assert content[0]["type"] == "image" and content[0]["source"]["media_type"] == "image/jpeg"
    assert "- 냉장고" in content[1]["text"] and seen["headers"]["x-api-key"] == "dummy-key"


def test_identify_request_failure_does_not_leak_key(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "super-secret-key")

    def boom(*a, **k):
        raise requests.ConnectionError("failed with super-secret-key in url")
    monkeypatch.setattr(vision.requests, "post", boom)
    r = vision.identify(JPEG, "image/jpeg", ITEMS)
    assert r["candidates"] == [] and "super-secret-key" not in str(r) and "실패" in r["message"]


# ---------------- API 라우트
@pytest.fixture()
def client():
    return appmod.app.test_client()


@pytest.fixture()
def region():
    return appmod.table.regions()[0]["id"]


def _post(client, region, data=None, name="a.jpg"):
    form = {"region": region}
    if data is not None:
        form["image"] = (io.BytesIO(data), name)
    return client.post("/api/identify", data=form, content_type="multipart/form-data")


def test_identify_route_validation(client, region):
    assert _post(client, region).status_code == 400  # 파일 없음
    assert _post(client, "없는 지역", JPEG).status_code == 400
    bad = _post(client, region, b"<html>fake</html>", "evil.jpg")  # 확장자가 jpg 여도 내용으로 판별
    assert bad.status_code == 400 and "JPG" in bad.json["error"]
    big = _post(client, region, JPEG + b"0" * vision.MAX_IMAGE_BYTES)
    assert big.status_code == 400 and "5MB" in big.json["error"]


def test_identify_route_ok_limits_candidates_to_region_items(client, region, monkeypatch):
    captured = {}

    def fake_identify(image, media_type, items):
        captured["items"], captured["mt"] = items, media_type
        return {"available": True, "candidates": [], "observations": "", "message": ""}
    monkeypatch.setattr(appmod.vision, "identify", fake_identify)
    r = _post(client, region, JPEG)
    assert r.status_code == 200 and captured["mt"] == "image/jpeg"
    assert captured["items"] == appmod.table.items(region)  # 해당 지자체 품목으로만 제한
    assert "ai_recognition" in client.get("/api/health").json
