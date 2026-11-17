"""우리동네 대형폐기물 AI 수거비용 산정 - 웹서버.

현재 구현 범위: 수수료 산정·근거 안내 API (+ 이를 시험하는 화면).
다른 필수기능(품목 인식, 규격 질문 연동, 체크리스트)은 같은 Flask 앱에
Blueprint 로 추가하면 되도록 core/ 아래에 모듈을 분리했습니다.
"""
import os
import threading
import time

from flask import Flask, jsonify, request, send_from_directory

from core import api_client, vision
from core.fees import FeeError, FeeTable

app = Flask(__name__, static_folder="static", static_url_path="/static")
app.config["MAX_CONTENT_LENGTH"] = 8 * 1024 * 1024  # 이미지 업로드 대비 8MB 제한

# ---- 데이터 관리: API 자동 갱신 ------------------------------------------------
# 인증키(DATA_GO_KR_KEY)가 있으면 서버 시작 시 공공데이터포털 API에서 수수료 데이터를 받아
# data/fees.csv 에 저장하고(백그라운드), 완료되면 메모리의 표를 교체한다.
# 키가 없거나 호출이 실패하면 기존 data/fees.csv, 그것도 없으면 시연용 샘플로 동작한다.
REFRESH_DAYS = float(os.environ.get("FEE_REFRESH_DAYS", "7"))  # 이 기간이 지나면 다시 받음
table = FeeTable.load_default()
data_state = {"source": "sample" if table.is_sample else "csv", "refreshing": False,
              "updated": None, "error": None, "progress": None}


def _csv_is_stale() -> bool:
    if not api_client.OUT.exists():
        return True
    return (time.time() - api_client.OUT.stat().st_mtime) > REFRESH_DAYS * 86400


def refresh_from_api() -> None:
    """API 에서 전체 데이터를 받아 저장하고 표를 교체한다. (오래 걸리므로 백그라운드에서 실행)"""
    global table
    key = os.environ.get("DATA_GO_KR_KEY")
    if not key or data_state["refreshing"]:
        return
    data_state.update(refreshing=True, error=None, progress="0%")
    try:
        def progress(n, total):
            data_state["progress"] = f"{n}/{total}"
        rows = api_client.fetch_all(os.environ.get("FEE_API_URL", api_client.DEFAULT_URL), key, {}, progress=progress)
        if not rows:
            raise RuntimeError("API 에서 받은 데이터가 없습니다.")
        api_client.save_csv(rows)
        table = FeeTable.load_default()
        data_state.update(source="api", updated=time.strftime("%Y-%m-%d %H:%M"))
    except BaseException as e:  # SystemExit 포함: 실패해도 기존 데이터로 계속 서비스
        data_state["error"] = f"{type(e).__name__}: {api_client.scrub(e)[:120]}"
    finally:
        data_state.update(refreshing=False, progress=None)


def start_background_refresh() -> None:
    if os.environ.get("DATA_GO_KR_KEY") and _csv_is_stale():
        threading.Thread(target=refresh_from_api, daemon=True).start()


@app.errorhandler(FeeError)
def handle_fee_error(err):
    return jsonify({"error": str(err)}), 400


@app.errorhandler(413)
def too_large(_err):
    return jsonify({"error": "파일이 너무 큽니다. 5MB 이하의 사진을 올려 주세요."}), 413


@app.get("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.get("/api/health")
def health():
    return jsonify({"ok": True, "sample_data": table.is_sample, "rows": len(table.rows), "ai_recognition": vision.is_available(), **data_state})


@app.get("/api/regions")
def regions():
    return jsonify({"regions": table.regions(), "sample_data": table.is_sample})


@app.get("/api/items")
def items():
    region = request.args.get("region", "")
    query = request.args.get("q", "").strip()
    if query:
        return jsonify({"matches": table.match_items(region, query)})
    return jsonify({"items": table.items(region)})


@app.post("/api/identify")
def identify():
    """사진으로 품목 후보를 추천한다. 후보는 해당 지자체 수수료표의 품목으로만 제한된다."""
    items = table.items(request.form.get("region", ""))  # 없는 지자체면 FeeError(400)
    upload = request.files.get("image")
    if not upload:
        raise FeeError("사진 파일을 선택해 주세요.")
    data = upload.read(vision.MAX_IMAGE_BYTES + 1)
    if len(data) > vision.MAX_IMAGE_BYTES:
        raise FeeError("사진은 5MB 이하만 올릴 수 있습니다.")
    media_type = vision.sniff_media_type(data)
    if not media_type:
        raise FeeError("JPG, PNG, WEBP, GIF 형식의 사진만 올릴 수 있습니다.")
    return jsonify(vision.identify(data, media_type, items))


@app.get("/api/spec-question")
def spec_question():
    """품목 확정 후 '필요한 규격만' 묻기 위한 정보(선택지/실측 입력 단위)."""
    return jsonify(table.spec_question(request.args.get("region", ""), request.args.get("item", "")))


@app.post("/api/fee")
def fee():
    body = request.get_json(silent=True) or {}
    measure = body.get("measure")
    try:
        measure = float(measure) if measure not in (None, "") else None
    except (TypeError, ValueError):
        raise FeeError("실측값은 숫자로 입력해 주세요.")
    return jsonify(table.calculate(body.get("region", ""), body.get("item", ""),
                                   body.get("spec") or None, measure))


@app.post("/api/fee/total")
def fee_total():
    body = request.get_json(silent=True) or {}
    return jsonify(table.calculate_many(body.get("region", ""), body.get("entries") or []))


if __name__ == "__main__":
    start_background_refresh()
    app.run(host="127.0.0.1", port=5000, debug=False)
